import { getRedis } from "./redisClient.js";

/**
 * Per-session GitHub state, stored in Redis only — never in process memory.
 * Sliding TTL: every read renews the expiry, so an actively-used session's
 * state never disappears mid-conversation, but an abandoned one is erased
 * automatically once SESSION_TTL_SECONDS passes with no activity. This is
 * "the session ending" — there is no separate explicit end-of-session
 * signal from the frontend.
 */
const TTL_SECONDS = Number(process.env.SESSION_TTL_SECONDS || 3600);

const key = (sessionId) => `agentforge:session:${sessionId}`;

export async function getSessionState(sessionId) {
  const raw = await getRedis().get(key(sessionId));
  if (!raw) return null;
  await getRedis().expire(key(sessionId), TTL_SECONDS);
  return JSON.parse(raw);
}

export async function saveSessionState(sessionId, state) {
  await getRedis().set(key(sessionId), JSON.stringify(state), "EX", TTL_SECONDS);
}

export async function clearSessionState(sessionId) {
  await getRedis().del(key(sessionId));
}
