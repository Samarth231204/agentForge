/**
 * A GitHub session's Docker volume must live exactly as long as its Redis
 * session state does (point 15: session-scoped, removed once the session
 * is over) — and "the session is over" is defined the same way everywhere
 * else in this app: the Redis key's TTL expiring. Redis keyspace
 * notifications turn that expiry into an event this process can react to,
 * so the volume's lifetime stays genuinely tied to the session's, not to
 * some separate timer that could drift out of sync with it.
 *
 * Redis going away and coming back is a real scenario, not a hypothetical —
 * it happened during this project's own testing (the dev Redis container
 * was removed outright, image and all). Two things specifically break if
 * reconnection isn't handled deliberately:
 *   1. `notify-keyspace-events` is a runtime CONFIG SET, not something that
 *      persists across a Redis restart — a fresh Redis process means the
 *      setting is gone, even though the TCP connection itself comes back.
 *      It has to be re-applied on every reconnect, not just once at boot.
 *   2. An unhandled connection-error event crashes the process by default
 *      in Node — every client here gets an explicit `error` handler so a
 *      Redis outage degrades to "cleanup paused, logged" instead of taking
 *      the whole backend down.
 */
import Redis from "ioredis";
import { removeSessionVolume } from "./tools/githubTools.js";

const SESSION_KEY_PREFIX = "agentforge:session:";

function makeClient(redisUrl, label) {
  const client = new Redis(redisUrl, {
    // Exponential backoff, capped at 10s, retried forever — a Redis outage
    // should pause cleanup, not give up on it permanently.
    retryStrategy: (attempt) => Math.min(attempt * 500, 10000),
    reconnectOnError: () => true,
  });

  client.on("error", (err) => {
    console.error(`[sessionCleanup:${label}] Redis error: ${err.message}`);
  });
  client.on("reconnecting", (delayMs) => {
    console.warn(`[sessionCleanup:${label}] Redis connection lost, retrying in ${delayMs}ms...`);
  });
  client.on("ready", () => {
    console.log(`[sessionCleanup:${label}] Redis connected.`);
  });

  return client;
}

export function startSessionCleanupListener() {
  const redisUrl = process.env.REDIS_URL || "redis://localhost:6379";
  const configClient = makeClient(redisUrl, "config");
  const subscriber = makeClient(redisUrl, "subscriber");

  // Re-applied on every successful (re)connect, not just once at startup —
  // this is what survives a Redis restart, where the setting itself would
  // otherwise silently revert to disabled.
  configClient.on("ready", () => {
    configClient.config("SET", "notify-keyspace-events", "Ex").catch((err) => {
      console.error("[sessionCleanup] Could not enable keyspace notifications — session volumes will not be auto-removed:", err.message);
    });
  });

  // ioredis re-subscribes automatically after a reconnect, but re-issuing
  // it explicitly on "ready" too costs nothing and removes any doubt.
  subscriber.on("ready", () => {
    subscriber.subscribe("__keyevent@0__:expired").catch((err) => {
      console.error("[sessionCleanup] Could not subscribe to key-expiry events:", err.message);
    });
  });

  subscriber.on("message", (_channel, expiredKey) => {
    if (!expiredKey.startsWith(SESSION_KEY_PREFIX)) return;
    const sessionId = expiredKey.slice(SESSION_KEY_PREFIX.length);
    removeSessionVolume(sessionId).catch((err) => {
      console.error(`[sessionCleanup] Failed to remove docker volume for expired session ${sessionId}:`, err.message);
    });
  });
}
