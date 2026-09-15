/**
 * Shared LLM-completion resilience, used by every workflow that calls an
 * LLM (research, write, and both GitHub sub-workflows). Tries a prioritized
 * list of (provider, model) candidates for the exact same call, advancing
 * to the next one only on a transient error (rate limit / 429 / 503 /
 * quota exhausted) — a permanent error (bad request, invalid args) fails
 * immediately instead of cascading through every candidate identically.
 *
 * Ordered best to worst:
 *   1-5. Groq's free tier — as many genuinely usable chat models as the API
 *        actually offers right now (checked live against GET /v1/models;
 *        excludes groq/compound[-mini] — confirmed separately that they
 *        don't support user-defined tool calling despite appearing to — and
 *        excludes the audio/guard/classifier models, which aren't chat
 *        models at all).
 *   6. Gemini — a different provider entirely, so a fresh quota pool once
 *      every Groq candidate is exhausted, not just another way to hit the
 *      same wall.
 *   7. OpenRouter — final cross-provider fallback, picked from its public
 *      /api/v1/models listing, live-verified against a real request.
 */
import { completeChatOnce } from "./llmClient.js";
import { saveQueryContext, clearQueryContext } from "./queryContext.js";

const GROQ_FALLBACK_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b", "qwen/qwen3.6-27b", "allam-2-7b"];

function buildCandidates() {
  const primaryGroqModel = process.env.GROQ_MODEL || "openai/gpt-oss-120b";
  const groqModels = [primaryGroqModel, ...GROQ_FALLBACK_MODELS.filter((m) => m !== primaryGroqModel)];

  const candidates = groqModels.map((model) => ({ provider: "groq", model }));
  candidates.push({ provider: "gemini", model: process.env.GEMINI_MODEL || "gemini-3.5-flash-lite" });
  candidates.push({ provider: "openrouter", model: process.env.OPENROUTER_MODEL || "nvidia/nemotron-3-super-120b-a12b:free" });
  return candidates;
}

function isRateLimited(err) {
  const status = err?.status;
  // 413 belongs here too: Groq returns it for "request too large for this
  // model's TPM budget," which is exactly a rate-limit-shaped problem the
  // NEXT candidate (larger TPM budget, or a different provider entirely)
  // may well handle fine — it is not a permanent, request-is-malformed error.
  if (status === 429 || status === 503 || status === 413) return true;
  // The openai SDK exposes these as structured fields on APIError, not
  // necessarily inside err.message's text — checking them directly is more
  // reliable than string-matching a human-readable message that varies by
  // provider and error shape.
  if (err?.code === "rate_limit_exceeded" || err?.type === "tokens") return true;
  const text = String(err?.message || err).toLowerCase();
  return ["rate limit", "rate_limit", "resource_exhausted", "quota", "unavailable", "overloaded", "high demand", "tokens per minute", "request too large"].some((marker) => text.includes(marker));
}

/**
 * @param {string} queryId - unique per HTTP request, NOT per session
 * @param {object[]} messages - the running conversation
 * @param {number} [temperature]
 */
export async function completeWithFallback({ queryId, messages, temperature }) {
  // Persisted before any candidate is even attempted, so the conversation
  // state is visible in Redis for the whole duration of this query,
  // regardless of which candidate ultimately answers it.
  await saveQueryContext(queryId, { messages });

  const candidates = buildCandidates();
  let lastErr;

  for (const candidate of candidates) {
    try {
      const message = await completeChatOnce({ ...candidate, messages, temperature });
      console.log(`[llmFallback] ${candidate.provider}/${candidate.model} answered.`);
      return message;
    } catch (err) {
      lastErr = err;
      if (!isRateLimited(err)) throw err;
      console.warn(`[llmFallback] ${candidate.provider}/${candidate.model} unavailable (${err.message}) — trying next candidate.`);
    }
  }
  throw lastErr;
}

export { clearQueryContext };
