import { getRedis } from "./redisClient.js";

/**
 * An agentic flow's state, in Redis only.
 *
 * A flow now outlives far more than the request that created it: it is
 * planned in one request, edited across however many chat turns, run, then
 * edited and re-run again after credentials arrive. Sliding TTL like
 * session.js, so a flow someone is actively working on never expires
 * underneath them while an abandoned one erases itself.
 *
 * Shape:
 *   { sessionId, prompt, nodes, status, results, ranNodes, answer? }
 *
 * `results` is keyed by node id and survives across runs — it is what makes
 * resume possible. `ranNodes` is a snapshot of the nodes as they were when
 * those results were produced, so the next run can tell which ones changed.
 */
const TTL_SECONDS = Number(process.env.SESSION_TTL_SECONDS || 3600);

const key = (flowId) => `agentforge:flow:${flowId}`;

export async function saveFlow(flowId, flow) {
  await getRedis().set(key(flowId), JSON.stringify(flow), "EX", TTL_SECONDS);
}

export async function getFlow(flowId) {
  const raw = await getRedis().get(key(flowId));
  if (!raw) return null;
  await getRedis().expire(key(flowId), TTL_SECONDS);
  return JSON.parse(raw);
}

/** Read-modify-write; returns the updated flow, or null if it's gone. */
export async function updateFlow(flowId, changes) {
  const flow = await getFlow(flowId);
  if (!flow) return null;
  const updated = { ...flow, ...changes };
  await saveFlow(flowId, updated);
  return updated;
}
