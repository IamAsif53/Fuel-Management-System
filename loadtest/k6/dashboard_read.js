import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate, Counter } from 'k6/metrics';

// Custom metrics
const stateLatency = new Trend('fsip_state_latency');
const riskLatency = new Trend('fsip_risk_latency');
const decisionsLatency = new Trend('fsip_decisions_latency');
const errorRate = new Rate('fsip_req_errors');
const successCounter = new Counter('fsip_req_success');

export const options = {
  scenarios: {
    dashboard_read: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '10s', target: 50 },   // Warm-up to 50 VUs
        { duration: '20s', target: 200 },  // Ramp-up to 200 VUs
        { duration: '40s', target: 200 },  // Sustained load at 200 VUs
        { duration: '10s', target: 0 },    // Ramp-down / Recovery
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    http_req_duration: ['p(90)<400', 'p(95)<600'], // Sustained load latency thresholds
    http_req_failed: ['rate<0.01'],                // Request failure rate below 1%
    fsip_state_latency: ['p(95)<500'],
    fsip_risk_latency: ['p(95)<500'],
    fsip_decisions_latency: ['p(90)<500'],
  },
};

const BASE_URL = __ENV.API_BASE_URL || 'http://localhost:18080';
const OPERATOR_TOKEN = __ENV.OPERATOR_TOKEN || 'change-me-operator';

export default function () {
  // Use unique client IP per VU to evaluate multi-tenant/operator traffic correctly
  const clientIp = `10.0.${Math.floor(__VU / 256)}.${__VU % 256 + 1}`;
  const params = {
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${OPERATOR_TOKEN}`,
      'X-Forwarded-For': clientIp,
    },
    timeout: '5s',
  };

  // 1. GET /api/state (primary real-time polling endpoint)
  const resState = http.get(`${BASE_URL}/api/state`, params);
  const stateOk = check(resState, {
    'state status is 200': (r) => r.status === 200,
    'state has sim_run_id': (r) => {
      try {
        const body = JSON.parse(r.body);
        return body && body.sim_run_id !== undefined;
      } catch (e) {
        return false;
      }
    },
  });
  stateLatency.add(resState.timings.duration);
  if (stateOk) {
    successCounter.add(1);
    errorRate.add(0);
  } else {
    errorRate.add(1);
  }

  sleep(0.6);

  // 2. GET /api/risk (risk heatmap and stockout projections)
  const resRisk = http.get(`${BASE_URL}/api/risk`, params);
  const riskOk = check(resRisk, {
    'risk status is 200': (r) => r.status === 200,
    'risk has rows': (r) => {
      try {
        const body = JSON.parse(r.body);
        return Array.isArray(body.rows);
      } catch (e) {
        return false;
      }
    },
  });
  riskLatency.add(resRisk.timings.duration);
  if (riskOk) {
    successCounter.add(1);
    errorRate.add(0);
  } else {
    errorRate.add(1);
  }

  sleep(0.6);

  // 3. GET /api/decisions (recommendation ledger and audit log)
  const resDec = http.get(`${BASE_URL}/api/decisions?limit=10`, params);
  const decOk = check(resDec, {
    'decisions status is 200': (r) => r.status === 200,
    'decisions has items': (r) => {
      try {
        const body = JSON.parse(r.body);
        return Array.isArray(body.items);
      } catch (e) {
        return false;
      }
    },
  });
  decisionsLatency.add(resDec.timings.duration);
  if (decOk) {
    successCounter.add(1);
    errorRate.add(0);
  } else {
    errorRate.add(1);
  }

  sleep(0.6);
}
