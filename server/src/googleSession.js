import { getRedis } from "./redisClient.js";

/**
 * Per-session Google OAuth token storage, in Redis only — same
 * session-persistent contract as session.js (sliding TTL, same
 * SESSION_TTL_SECONDS, renewed on every read), just under its own key
 * namespace so a Gmail disconnect/expiry never touches the session's
 * separate GitHub state (token/repoUrl) living in session.js.
 */
const TTL_SECONDS = Number(process.env.SESSION_TTL_SECONDS || 3600);

const key = (sessionId) => `agentforge:session:google:${sessionId}`;

export async function getGoogleTokens(sessionId) {
  const raw = await getRedis().get(key(sessionId));
  if (!raw) return null;
  await getRedis().expire(key(sessionId), TTL_SECONDS);
  return JSON.parse(raw);
}

export async function saveGoogleTokens(sessionId, tokens) {
  await getRedis().set(key(sessionId), JSON.stringify(tokens), "EX", TTL_SECONDS);
}

export async function clearGoogleTokens(sessionId) {
  await getRedis().del(key(sessionId));
}
