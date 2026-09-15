/**
 * Per-QUERY conversation state in Redis — distinct from session.js's
 * per-SESSION state (token/repoUrl/pendingQuery, which lives for the whole
 * browser session). A query id is minted fresh for every single /api/tasks
 * request; its context is written to Redis as the LLM fallback chain runs,
 * and deleted once that one request finishes — success or failure — not
 * tied to the session's much longer TTL at all.
 *
 * A short safety TTL is still set (rather than none) purely so a request
 * that crashes before its own cleanup runs doesn't leave the key forever;
 * the *normal* path is explicit deletion via clearQueryContext, not
 * waiting on this TTL.
 */
import { getRedis } from "./redisClient.js";

const SAFETY_TTL_SECONDS = 600;

const key = (queryId) => `agentforge:query:${queryId}`;

export async function saveQueryContext(queryId, context) {
  await getRedis().set(key(queryId), JSON.stringify(context), "EX", SAFETY_TTL_SECONDS);
}

export async function getQueryContext(queryId) {
  const raw = await getRedis().get(key(queryId));
  return raw ? JSON.parse(raw) : null;
}

export async function clearQueryContext(queryId) {
  await getRedis().del(key(queryId));
}
