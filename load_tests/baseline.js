/**
 * Phase 17.1 — Baseline Load Test Script
 *
 * Establishes a conservative, reproducible single-VU baseline to measure
 * round-trip request duration, response structures, and HTTP status distribution
 * without overloading local resources.
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';
import { BASE_URL, API_KEY, MODEL, VUS, DURATION, getHeaders, buildChatPayload } from './config.js';

// Custom metric definitions for detailed baseline observability
export const chatLatencyTrend = new Trend('chat_req_duration_ms', true);
export const successfulRequests = new Counter('chat_successful_requests');
export const failedRequests = new Counter('chat_failed_requests');
export const rateLimitedRequests = new Counter('chat_rate_limited_requests');
export const successRate = new Rate('chat_success_rate');

export const options = {
  vus: VUS,
  duration: DURATION,
  thresholds: {
    // Sanity checks for baseline integrity
    http_req_failed: ['rate<0.10'], // Less than 10% unexpected network failures
    'http_req_duration': ['p(95)<30000'], // Under provider timeout (30s)
  },
};

export function setup() {
  if (!API_KEY) {
    console.warn(
      '[WARN] API_KEY environment variable is not set. Authenticated endpoints (/v1/chat/completions) will return 401.'
    );
  }
}

export default function () {
  const url = `${BASE_URL}/v1/chat/completions`;
  const requestId = `k6-baseline-${__VU}-${__ITER}-${Date.now()}`;
  const payload = buildChatPayload('Hello gateway, this is a baseline test.', MODEL);
  const headers = getHeaders(requestId);

  const startTime = Date.now();
  const res = http.post(url, payload, { headers: headers });
  const latency = Date.now() - startTime;

  chatLatencyTrend.add(latency);

  const is200 = res.status === 200;
  const is429 = res.status === 429;
  const isError = res.status >= 500;

  if (is200) {
    successfulRequests.add(1);
    successRate.add(true);
  } else {
    failedRequests.add(1);
    successRate.add(false);
    if (is429) {
      rateLimitedRequests.add(1);
    }
  }

  check(res, {
    'status is 200 (or expected auth rejection if missing key)': (r) =>
      r.status === 200 || (!API_KEY && r.status === 401),
    'response has valid content-type': (r) =>
      r.headers['Content-Type'] && r.headers['Content-Type'].includes('application/json'),
    'response includes X-Request-ID': (r) => Boolean(r.headers['X-Request-Id'] || r.headers['X-Request-ID']),
  });

  // Conservative pacing between baseline requests
  sleep(1);
}
