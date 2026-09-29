/**
 * Phase 17.4 — Rate Limiting Under Load Script
 *
 * Simulates concurrent virtual users exercising the OpenAI-compatible chat
 * completion endpoint (/v1/chat/completions) against the standard configured
 * tenant rate limit (DEFAULT_TENANT_RPM=60).
 *
 * Measures:
 * - Allowed requests (HTTP 200)
 * - Rate-limited rejections (HTTP 429)
 * - Rate-limit response headers (Retry-After, X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset)
 * - Response latency distributions for 200 vs 429
 * - Overall throughput and error rates
 */

import http from 'k6/http';
import { check } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { BASE_URL, API_KEY, MODEL, getHeaders, buildChatPayload } from './config.js';

// Custom k6 metric trackers for rate limiting
const successfulRequests = new Counter('rate_limit_successful_requests');
const rateLimitedRequests = new Counter('rate_limit_rejected_requests');
const otherFailedRequests = new Counter('rate_limit_other_failed_requests');
const rateLimitDuration = new Trend('rate_limit_rejected_duration_ms');

// Configurable concurrency with standard Phase 17 defaults (10 VUs, 10s ramp-up, 30s steady, 10s ramp-down)
const RATE_LIMIT_VUS = parseInt(__ENV.RATE_LIMIT_VUS || '10', 10);
const RAMP_UP_DURATION = __ENV.RAMP_UP_DURATION || '10s';
const STEADY_DURATION = __ENV.STEADY_DURATION || '30s';
const RAMP_DOWN_DURATION = __ENV.RAMP_DOWN_DURATION || '10s';
const RATE_LIMIT_MODEL = __ENV.RATE_LIMIT_MODEL || MODEL;

export const options = {
  stages: [
    { duration: RAMP_UP_DURATION, target: RATE_LIMIT_VUS },   // Stage 1: Ramp-up
    { duration: STEADY_DURATION, target: RATE_LIMIT_VUS },    // Stage 2: Steady state
    { duration: RAMP_DOWN_DURATION, target: 0 },              // Stage 3: Ramp-down
  ],
};

export function setup() {
  if (!API_KEY) {
    throw new Error(
      'API_KEY environment variable is required for Phase 17.4 rate-limit load testing. ' +
      'Missing API_KEY will cause 401 authentication failures.'
    );
  }
}

export default function () {
  const url = `${BASE_URL}/v1/chat/completions`;
  const requestId = `k6-ratelimit-${__VU}-${__ITER}-${Date.now()}`;
  const payload = buildChatPayload('ping', RATE_LIMIT_MODEL);
  const headers = getHeaders(requestId);

  const res = http.post(url, payload, { headers: headers });

  if (res.status === 200) {
    successfulRequests.add(1);
    check(res, {
      'status is 200': (r) => r.status === 200,
      '200 response has valid content-type': (r) =>
        Boolean(r.headers['Content-Type'] && r.headers['Content-Type'].includes('application/json')),
      '200 response includes X-Request-ID': (r) =>
        Boolean(r.headers['X-Request-Id'] || r.headers['X-Request-ID']),
    });
  } else if (res.status === 429) {
    rateLimitedRequests.add(1);
    rateLimitDuration.add(res.timings.duration);
    check(res, {
      'status is 429': (r) => r.status === 429,
      '429 response has Retry-After header': (r) =>
        Boolean(r.headers['Retry-After'] || r.headers['retry-after']),
      '429 response has X-RateLimit-Limit header': (r) =>
        Boolean(r.headers['X-Ratelimit-Limit'] || r.headers['x-ratelimit-limit'] || r.headers['X-RateLimit-Limit']),
      '429 response has X-RateLimit-Remaining header equal to 0': (r) => {
        const remaining = r.headers['X-Ratelimit-Remaining'] || r.headers['x-ratelimit-remaining'] || r.headers['X-RateLimit-Remaining'];
        return remaining === '0';
      },
      '429 response has X-RateLimit-Reset header': (r) =>
        Boolean(r.headers['X-Ratelimit-Reset'] || r.headers['x-ratelimit-reset'] || r.headers['X-RateLimit-Reset']),
      '429 response includes X-Request-ID': (r) =>
        Boolean(r.headers['X-Request-Id'] || r.headers['X-Request-ID']),
    });
  } else {
    otherFailedRequests.add(1);
    check(res, {
      'unexpected status encountered': () => false,
    });
  }
}
