/**
 * Shared configuration and helper utilities for k6 load tests (Phase 17.1).
 *
 * All environment-sensitive values (URLs, keys, concurrency parameters) are
 * read from environment variables with safe development defaults.
 */

// Base URL for the LLM Gateway
export const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

// API Key for authenticating against the gateway.
// Required for authenticated endpoints (/v1/chat/completions).
export const API_KEY = __ENV.API_KEY || '';

// Target model name for completion requests
export const MODEL = __ENV.MODEL || 'qwen2.5:3b';

// Virtual Users (concurrency)
export const VUS = parseInt(__ENV.VUS || '1', 10);

// Duration for load test scenarios
export const DURATION = __ENV.DURATION || '10s';

/**
 * Generate standard HTTP headers for OpenAI-compatible gateway requests.
 *
 * @param {string} [customRequestId] - Optional explicit X-Request-ID.
 * @returns {object} Headers map.
 */
export function getHeaders(customRequestId) {
  const headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
  };

  if (API_KEY) {
    headers['Authorization'] = `Bearer ${API_KEY}`;
  }

  if (customRequestId) {
    headers['X-Request-ID'] = customRequestId;
  }

  return headers;
}

/**
 * Build a minimal valid OpenAI-compatible chat completion payload.
 *
 * @param {string} [promptText] - Prompt text for the user message.
 * @param {string} [modelName] - Overridden model name if needed.
 * @returns {string} JSON-serialized request payload.
 */
export function buildChatPayload(promptText = 'ping', modelName = MODEL) {
  return JSON.stringify({
    model: modelName,
    messages: [
      {
        role: 'user',
        content: promptText,
      },
    ],
    temperature: 0.1,
    max_tokens: 16,
    stream: false,
  });
}
