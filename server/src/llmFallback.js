/**
 * Shared LLM-completion resilience, used by every workflow that calls an
 * LLM (research, write, github, and browse). Tries a prioritized list of
 * (provider, model) candidates for the exact same call, advancing to the
 * next one only on a transient error (rate limit / 429 / 503 / quota
 * exhausted) — a permanent error (bad request, invalid args) fails
 * immediately instead of cascading through every candidate identically.
 *
 * Ordered best to worst:
 *   1. Gemini — primary for everything, moved to the front after live
 *      testing on the browse intent's multi-turn tool-calling agent loop
 *      showed it calling tools cleanly and stopping reliably every time,
 *      while gpt-oss-120b (the previous default) repeatedly drifted into
 *      many empty, wasted turns on the same task (confirmed directly:
 *      10+ consecutive empty turns on one query, a 2+ minute response).
 *   2-6. Groq's free tier — as many genuinely usable chat models as the
 *        API actually offers right now (checked live against GET
 *        /v1/models; excludes groq/compound[-mini] — confirmed separately
 *        that they don't support user-defined tool calling despite
 *        appearing to — and excludes the audio/guard/classifier models,
 *        which aren't chat models at all).
 *   7. OpenRouter — final cross-provider fallback, picked from its public
 *      /api/v1/models listing, live-verified against a real request.
 */
import { completeChatOnce } from "./llmClient.js";
import { saveQueryContext, clearQueryContext } from "./queryContext.js";

// Checked against GET /v1/models — qwen/qwen3.6-27b was in this list but no
// longer exists on the account, and a retired model is worse than a missing
// one: it answers 404, which is not a capacity problem, so it used to throw
// and kill the whole request instead of moving to the next candidate.
const GROQ_FALLBACK_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b", "allam-2-7b"];

function buildCandidates() {
  const primaryGroqModel = process.env.GROQ_MODEL || "openai/gpt-oss-120b";
  const groqModels = [primaryGroqModel, ...GROQ_FALLBACK_MODELS.filter((m) => m !== primaryGroqModel)];

  return [
    { provider: "gemini", model: process.env.GEMINI_MODEL || "gemini-3.5-flash-lite" },
    ...groqModels.map((model) => ({ provider: "groq", model })),
    { provider: "openrouter", model: process.env.OPENROUTER_MODEL || "nvidia/nemotron-3-super-120b-a12b:free" },
  ];
}

/**
 * Renamed from isRateLimited: it's no longer purely about rate limits.
 * Confirmed via a real failure — a multi-turn browse conversation that
 * cascaded Gemini -> Groq (Gemini rate-limited) -> back to Gemini (limit
 * reset a few turns later) crashed with a 400 "missing a thought_signature
 * in functionCall parts": Gemini requires its own metadata on prior
 * tool-call turns for conversation continuity, which isn't present when an
 * earlier turn in the SAME growing conversation was actually answered by a
 * different provider. That's not a malformed-request problem the next
 * candidate would hit identically — it's a THIS-PROVIDER-specific
 * incompatibility with this conversation's history, so it belongs in the
 * same "try the next candidate" bucket as an actual rate limit.
 */
function isRetryableCandidateError(err) {
  const status = err?.status;
  // A model that no longer exists, or that this account can't reach, is a
  // problem with THIS candidate only — the next one may be perfectly fine.
  // Found the hard way: a retired model left in the chain returned 404,
  // which matched none of the checks below, so it threw and failed a whole
  // pipeline step instead of cascading one place down the list. Model names
  // get retired by providers without notice, so the chain has to tolerate
  // it rather than depend on the list being permanently accurate.
  if (status === 404) return true;
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
  return ["rate limit", "rate_limit", "resource_exhausted", "quota", "unavailable", "overloaded", "high demand", "tokens per minute", "request too large", "thought_signature"].some((marker) => text.includes(marker));
}

/**
 * @param {string} queryId - unique per HTTP request, NOT per session
 * @param {object[]} messages - the running conversation
 * @param {object[]} [tools] - OpenAI-format tool schemas, if this call offers tools
 * @param {number} [temperature]
 */
/**
 * @param {function} [onEvent] - optional live-progress sink. Reports which
 *   candidate is being tried and which one answered, information this
 *   function already computes for its own logging. Purely additive: it is
 *   called alongside work that happens regardless and can never change the
 *   outcome of a call.
 */
export async function completeWithFallback({ queryId, messages, tools, temperature, onEvent }) {
  // Persisted before any candidate is even attempted, so the conversation
  // state is visible in Redis for the whole duration of this query,
  // regardless of which candidate ultimately answers it.
  await saveQueryContext(queryId, { messages, tools: tools?.map((t) => t.function.name) });

  const candidates = buildCandidates();
  let lastErr;

  for (const candidate of candidates) {
    try {
      const message = await completeChatOnce({ ...candidate, messages, tools, temperature });
      console.log(`[llmFallback] ${candidate.provider}/${candidate.model} answered.`);
      onEvent?.({ type: "llm_call", provider: candidate.provider, model: candidate.model, outcome: "answered" });
      return message;
    } catch (err) {
      lastErr = err;
      if (!isRetryableCandidateError(err)) throw err;
      console.warn(`[llmFallback] ${candidate.provider}/${candidate.model} unavailable (${err.message}) — trying next candidate.`);
      onEvent?.({ type: "llm_call", provider: candidate.provider, model: candidate.model, outcome: "unavailable" });
    }
  }
  throw lastErr;
}

/**
 * The model called a tool name that isn't in the offered schema at all
 * (hallucinated) — the provider's own server-side validator rejects this
 * before any message comes back. Not a capacity problem a different
 * candidate would necessarily avoid; the agent loop uses this to inject a
 * corrective nudge on the SAME conversation instead of crashing the task.
 */
export function isInvalidToolCall(err) {
  return err?.code === "tool_use_failed" || (err?.status === 400 && err?.type === "invalid_request_error" && String(err?.message || "").toLowerCase().includes("tool"));
}

export { clearQueryContext };
