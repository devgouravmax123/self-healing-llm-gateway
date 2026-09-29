/**
 * Phase 17.3 — Concurrent Provider-Failure / Chaos Load Test Script
 *
 * Simulates concurrent virtual users exercising the OpenAI-compatible chat
 * completion endpoint (/v1/chat/completions) while deterministic provider failure
 * is injected out-of-band by the operator.
 *
 * Metrics rely on k6 built-in metrics and server-side Prometheus instrumentation:
 * - http_reqs (Throughput / RPS)
 * - http_req_duration (End-to-end response latency)
 * - http_req_failed (Failure rate)
 * - vus / iterations
 *
 * Note: Fault injection is administered separately by the operator out-of-band
 * to maintain strict separation of load generation from administrative control.
 */

import http from 'k6/http';
import { check } from 'k6';
import { BASE_URL, API_KEY, MODEL, getHeaders, buildChatPayload } from './config.js';

// Configurable concurrency with sensible Phase 17.3 defaults (10 VUs, 60s steady)
const CHAOS_VUS = parseInt(__ENV.CHAOS_VUS || '10', 10);
const CHAOS_RAMP_UP_DURATION = __ENV.CHAOS_RAMP_UP_DURATION || '10s';
const CHAOS_STEADY_DURATION = __ENV.CHAOS_STEADY_DURATION || '60s';
const CHAOS_RAMP_DOWN_DURATION = __ENV.CHAOS_RAMP_DOWN_DURATION || '10s';
const CHAOS_MODEL = __ENV.CHAOS_MODEL || MODEL;

export const options = {
  stages: [
    { duration: CHAOS_RAMP_UP_DURATION, target: CHAOS_VUS },   // Stage 1: Ramp-up
    { duration: CHAOS_STEADY_DURATION, target: CHAOS_VUS },    // Stage 2: Steady state (chaos active/cleared)
    { duration: CHAOS_RAMP_DOWN_DURATION, target: 0 },         // Stage 3: Ramp-down
  ],
};

export function setup() {
  if (!API_KEY) {
    throw new Error(
      'API_KEY environment variable is required for Phase 17.3 chaos load testing. ' +
      'Missing API_KEY will cause 401 authentication failures.'
    );
  }
}

export default function () {
  const url = `${BASE_URL}/v1/chat/completions`;
  const requestId = `k6-chaos-${__VU}-${__ITER}-${Date.now()}`;
  const payload = buildChatPayload('ping', CHAOS_MODEL);
  const headers = getHeaders(requestId);

  const res = http.post(url, payload, { headers: headers });

  check(res, {
    'status is 200 or accepted recovery': (r) => r.status === 200 || r.status === 503,
    'response has valid content-type': (r) =>
      Boolean(r.headers['Content-Type'] && r.headers['Content-Type'].includes('application/json')),
    'response includes X-Request-ID': (r) =>
      Boolean(r.headers['X-Request-Id'] || r.headers['X-Request-ID']),
  });
}
