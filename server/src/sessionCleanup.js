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
import { removeSessionVolume, listSessionVolumes } from "./tools/githubTools.js";

const SESSION_KEY_PREFIX = "agentforge:session:";

/**
 * How often to reconcile docker volumes against live sessions. Ten minutes
 * is far more often than needed for correctness — the volumes are small —
 * but keeps a leak bounded on a long-running host.
 */
const SWEEP_INTERVAL_MS = Number(process.env.VOLUME_SWEEP_INTERVAL_MS || 10 * 60 * 1000);

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

  startVolumeSweep(configClient);
}

/**
 * The backstop: reconcile what exists against what should exist.
 *
 * The event-driven path above is precise but conditional. It needs
 * `notify-keyspace-events`, which is a `CONFIG SET` — and some managed
 * Redis providers refuse that command, in which case no expiry event ever
 * arrives and the failure is silent: one warning at boot, then a leaked
 * volume per GitHub session until the disk fills. (Measured against
 * Upstash specifically, `CONFIG SET` IS accepted and expiry events DO
 * arrive, so there the event path is live and this is pure redundancy.)
 *
 * It earns its place regardless, because reconciliation doesn't care why a
 * volume is orphaned and so covers what the event path structurally cannot:
 * volumes left behind by a crash mid-session, or by a restart while Redis
 * was down — nobody is subscribed when those keys expire. Two such volumes
 * were found on this sweep's very first run.
 */
function startVolumeSweep(redis) {
  const sweep = async () => {
    let sessionIds;
    try {
      sessionIds = await listSessionVolumes();
    } catch (err) {
      // Docker missing or not running is legitimate on a host that only
      // serves the non-github intents — not worth logging on a timer.
      return;
    }
    if (sessionIds.length === 0) return;

    for (const sessionId of sessionIds) {
      try {
        // The session key is the authority on whether a session is alive,
        // exactly as it is everywhere else in the app.
        if (await redis.exists(`${SESSION_KEY_PREFIX}${sessionId}`)) continue;
        await removeSessionVolume(sessionId);
        console.log(`[sessionCleanup:sweep] Removed orphaned volume for session ${sessionId.slice(0, 8)}…`);
      } catch (err) {
        console.error(`[sessionCleanup:sweep] Could not reconcile session ${sessionId.slice(0, 8)}…:`, err.message);
      }
    }
  };

  // unref() so a pending timer never holds the process open on shutdown.
  setInterval(sweep, SWEEP_INTERVAL_MS).unref();
  sweep().catch(() => {});
}
