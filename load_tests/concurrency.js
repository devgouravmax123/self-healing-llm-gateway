/**
 * Phase 17.2 — Concurrent Provider-Target Load Test Script
 *
 * Simulates concurrent virtual users exercising the OpenAI-compatible chat
 * completion endpoint (/v1/chat/completions) across healthy ProviderTarget candidates.
 *
 * Metrics rely primarily on k6 built-in metrics:
 * - http_reqs (RPS / throughput)
 * - http_req_duration (p50, p95, p99 latencies)
 * - http_req_failed (error rate)
 * - vus / iterations
 */

import http from 'k6/http';
import { check } from 'k6';
import { BASE_URL, API_KEY, MODEL, getHeaders, buildChatPayload } from './config.js';

// Configurable target concurrency with conservative development default (10 VUs)
const CONCURRENT_VUS = parseInt(__ENV.CONCURRENT_VUS || '10', 10);
const RAMP_UP_DURATION = __ENV.RAMP_UP_DURATION || '10s';
const STEADY_DURATION = __ENV.STEADY_DURATION || '30s';
const RAMP_DOWN_DURATION = __ENV.RAMP_DOWN_DURATION || '10s';

export const options = {
  stages: [
    { duration: RAMP_UP_DURATION, target: CONCURRENT_VUS },   // Stage 1: Ramp-up
    { duration: STEADY_DURATION, target: CONCURRENT_VUS },    // Stage 2: Steady state
    { duration: RAMP_DOWN_DURATION, target: 0 },               // Stage 3: Ramp-down
  ],
  thresholds: {
    // Sanity / correctness check for healthy load scenario (NOT a production SLO)
    http_req_failed: ['rate<0.01'],
  },
};

export function setup() {
  if (!API_KEY) {
    throw new Error(
      'API_KEY environment variable is required for Phase 17.2 concurrency load testing. ' +
      'Missing API_KEY will cause 401 authentication failures.'
    );
  }
}

export default function () {
  const url = `${BASE_URL}/v1/chat/completions`;
  const requestId = `k6-concurrent-${__VU}-${__ITER}-${Date.now()}`;
  const payload = buildChatPayload('ping', MODEL);
  const headers = getHeaders(requestId);

  const res = http.post(url, payload, { headers: headers });

  check(res, {
    'status is 200': (r) => r.status === 200,
    'response has valid content-type': (r) =>
      Boolean(r.headers['Content-Type'] && r.headers['Content-Type'].includes('application/json')),
    'response includes X-Request-ID': (r) =>
      Boolean(r.headers['X-Request-Id'] || r.headers['X-Request-ID']),
  });
}
