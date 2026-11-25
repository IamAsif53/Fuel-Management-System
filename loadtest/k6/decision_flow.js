import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate, Counter } from 'k6/metrics';

const approvalLatency = new Trend('fsip_approval_latency');
const listLatency = new Trend('fsip_decisions_list_latency');
const approvalErrors = new Rate('fsip_approval_errors');
const approvalSuccess = new Counter('fsip_approvals_total');

export const options = {
  scenarios: {
    decision_flow: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '10s', target: 10 },
        { duration: '30s', target: 25 },  // 25 concurrent operators
        { duration: '10s', target: 0 },
      ],
      gracefulRampDown: '5s',
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<600'],
    http_req_failed: ['rate<0.05'],
    fsip_decisions_list_latency: ['p(95)<500'],
    fsip_approval_latency: ['p(90)<600'],
  },
};

const BASE_URL = __ENV.API_BASE_URL || 'http://localhost:18080';
const OPERATOR_TOKEN = __ENV.OPERATOR_TOKEN || 'change-me-operator';

export default function () {
  const clientIp = `10.1.${Math.floor(__VU / 256)}.${__VU % 256 + 1}`;
  const params = {
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${OPERATOR_TOKEN}`,
      'X-Forwarded-For': clientIp,
    },
    timeout: '5s',
  };

  // 1. Fetch recommendations
  const resRecs = http.get(`${BASE_URL}/api/recommendations`, params);
  listLatency.add(resRecs.timings.duration);
  check(resRecs, {
    'recommendations status 200': (r) => r.status === 200,
  });

  // 2. Fetch decisions list
  const resDec = http.get(`${BASE_URL}/api/decisions?limit=10`, params);
  listLatency.add(resDec.timings.duration);
  check(resDec, {
    'decisions list status 200': (r) => r.status === 200,
  });

  // 3. Attempt approval if pending recommendation found
  try {
    const body = JSON.parse(resRecs.body);
    if (body && Array.isArray(body.items) && body.items.length > 0) {
      const rec = body.items[0];
      const approvePayload = JSON.stringify({
        reviewed_driver: true,
        comment: 'k6 automated concurrent approval test',
      });
      const approveParams = Object.assign({}, params, {
        responseCallback: http.expectedStatuses(200, 409, 422),
      });
      const resApprove = http.post(
        `${BASE_URL}/api/decisions/${rec.id}/approve`,
        approvePayload,
        approveParams
      );
      approvalLatency.add(resApprove.timings.duration);

      // Status 200 (approved) or 409/422 (already processed / expired by another concurrent VU)
      const isExpected = resApprove.status === 200 || resApprove.status === 409 || resApprove.status === 422;
      check(resApprove, {
        'approve handled cleanly (200, 409, or 422)': () => isExpected,
      });

      if (resApprove.status === 200) {
        approvalSuccess.add(1);
        approvalErrors.add(0);
      } else if (!isExpected) {
        approvalErrors.add(1);
      }
    }
  } catch (e) {
    // Non-JSON or transient
  }

  sleep(0.8);
}
