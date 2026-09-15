import Redis from "ioredis";

let client = null;

/**
 * Process-wide singleton, constructed lazily so importing this module never
 * requires Redis to be reachable — only actually connecting does.
 */
export function getRedis() {
  if (!client) {
    client = new Redis(process.env.REDIS_URL || "redis://localhost:6379");
  }
  return client;
}
