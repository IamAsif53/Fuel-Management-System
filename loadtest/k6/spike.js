import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate } from 'k6/metrics';

const spikeLatency = new Trend('fsip_spike_latency');
const spikeErrorRate = new Rate('fsip_spike_errors');

export const options = {
  scenarios: {
    traffic_spike: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '10s', target: 20 },   // Initial baseline
        { duration: '10s', target: 200 },  // Rapid spike to 200 VUs
        { duration: '30s', target: 200 },  // Hold peak 200 VUs
        { duration: '10s', target: 20 },   // Rapid drop to verify recovery
        { duration: '15s', target: 0 },    // Cooldown
      ],
      gracefulRampDown: '5s',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<1000'],
    http_req_failed: ['rate<0.02'],
    fsip_spike_latency: ['p(95)<1000'],
  },
};

const BASE_URL = __ENV.API_BASE_URL || 'http://localhost:18080';
const OPERATOR_TOKEN = __ENV.OPERATOR_TOKEN || 'change-me-operator';

export default function () {
  const clientIp = `10.2.${Math.floor(__VU / 256)}.${__VU % 256 + 1}`;
  const params = {
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${OPERATOR_TOKEN}`,
      'X-Forwarded-For': clientIp,
    },
    timeout: '5s',
  };

  const res = http.get(`${BASE_URL}/api/state`, params);
  const ok = check(res, {
    'status is 200': (r) => r.status === 200,
  });

  spikeLatency.add(res.timings.duration);
  spikeErrorRate.add(ok ? 0 : 1);

  sleep(0.2);
}
