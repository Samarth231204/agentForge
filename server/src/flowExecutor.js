/**
 * Runs an agentic flow: a dependency graph of nodes, each dispatched to the
 * intent handler it names.
 *
 * Scheduling is topological rather than stage-by-stage. Any node whose
 * dependencies have finished is eligible immediately, so parallelism falls
 * out of the graph's shape instead of being decided up front — which is
 * what makes an arbitrarily rewired graph runnable without the executor
 * needing to know how it was rewired.
 *
 * Two things are deliberately enforced here rather than in the graph:
 *
 *   - `browse` concurrency is capped at one. The user's rule is that browse
 *     never runs beside another browse, and edges alone can't guarantee
 *     that once the graph is user-editable — rewiring two browse nodes into
 *     siblings would quietly start two browsers. A limit in the scheduler
 *     holds regardless of what the edges say.
 *   - Resume. Nodes that already succeeded keep their results and are
 *     skipped, so pressing run again after supplying a credential completes
 *     the one blocked node instead of re-sending every email the flow
 *     already sent.
 */
import { runResearch } from "./handlers/research.js";
import { runWrite } from "./handlers/write.js";
import { runGithub } from "./handlers/github.js";
import { runBrowse } from "./handlers/browse.js";
import { runGoogle } from "./handlers/google.js";
import { getFlow, updateFlow } from "./flows.js";
import { getSessionState, saveSessionState } from "./session.js";
import { sessionKeyFor } from "./intentShapes.js";
import { clearQueryContext } from "./queryContext.js";
import { emitFlowEvent } from "./ws.js";
import { dirtySet, SERIAL_INTENTS } from "./flowGraph.js";

const HANDLERS = {
  research: runResearch,
  write: runWrite,
  github: runGithub,
  browse: runBrowse,
  google: runGoogle,
};

/** Caps how many nodes of a serial intent may run at once. */
function createLimiter(limit) {
  let active = 0;
  const waiting = [];
  return {
    async acquire() {
      if (active < limit) {
        active++;
        return;
      }
      await new Promise((resolve) => waiting.push(resolve));
      active++;
    },
    release() {
      active--;
      const next = waiting.shift();
      if (next) next();
    },
  };
}

/**
 * Fields the planner resolved up front (a recipient, a repo URL) are written
 * into session state before the node runs, rather than relying on the
 * handler's own regex to re-find them in the task text. This is what removes
 * the failure mode where a task mentions two email addresses and the pattern
 * picks the wrong one.
 */
async function preseedSessionFields(node, sessionId) {
  const resolved = {};
  for (const fieldName of ["recipient", "repoUrl"]) {
    const value = node[fieldName];
    if (!value) continue;
    const key = sessionKeyFor(node.intent, fieldName);
    if (key) resolved[key] = value;
  }
  if (Object.keys(resolved).length === 0) return;

  const state = (await getSessionState(sessionId)) || {};
  await saveSessionState(sessionId, { ...state, ...resolved });
}

/** Everything this node transitively waits for, nearest first. */
function ancestorResults(node, nodes, results) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const ordered = [];
  const seen = new Set();
  let frontier = [...(node.dependsOn || [])];

  while (frontier.length > 0) {
    const next = [];
    for (const id of frontier) {
      if (seen.has(id)) continue;
      seen.add(id);
      if (results[id]) ordered.push({ id, intent: byId.get(id)?.intent || "?", ...results[id] });
      next.push(...(byId.get(id)?.dependsOn || []));
    }
    frontier = next;
  }
  return ordered;
}

/**
 * Context caps. Unbounded accumulation genuinely broke a real ten-node run:
 * every prior result was prepended in full, so a late node carried two
 * research answers, two page summaries, two written explainers and two
 * README dumps, and the provider rejected the call outright. Long flows
 * failed at the END, after all the expensive work was already done.
 *
 * A node that explicitly reuses one output via bodyFromNode receives that
 * output verbatim regardless, so trimming shared context never truncates
 * content that is actually being sent somewhere.
 */
const MAX_CONTEXT_CHARS_PER_NODE = 1200;
const MAX_CONTEXT_CHARS_TOTAL = 6000;

function buildNodePrompt(node, nodes, results) {
  const ancestors = ancestorResults(node, nodes, results);
  if (ancestors.length === 0) return node.task;

  const lines = [];
  let budget = MAX_CONTEXT_CHARS_TOTAL;
  for (const { id, intent, result } of ancestors) {
    if (budget <= 0) break;
    const text = String(result ?? "");
    const allowance = Math.min(MAX_CONTEXT_CHARS_PER_NODE, budget);
    const clipped = text.length > allowance ? `${text.slice(0, allowance)}…[truncated]` : text;
    lines.push(`- Node ${id} (${intent}): ${clipped}`);
    budget -= clipped.length;
  }

  return `${node.task}\n\nCONTEXT FROM EARLIER NODES — use this rather than redoing their work:\n${lines.join("\n")}`;
}

async function runNode(node, { flowId, sessionId, nodes, results, alongside }) {
  emitFlowEvent(flowId, {
    type: "node_spawned",
    nodeId: node.id,
    intent: node.intent,
    task: node.task,
    // These two are what let the UI draw threads forking and joining rather
    // than a flat list: who this node started beside, and what it waited for.
    alongside: alongside.filter((id) => id !== node.id),
    waitedFor: node.dependsOn || [],
  });

  const handler = HANDLERS[node.intent];
  if (!handler) {
    const result = `No handler is wired for intent "${node.intent}".`;
    emitFlowEvent(flowId, { type: "node_result", nodeId: node.id, intent: node.intent, result, failed: true });
    return { result, failed: true };
  }

  await preseedSessionFields(node, sessionId);

  // Its own query id, so each node's LLM context is tracked and cleaned up
  // independently — nodes running together sharing one id would overwrite
  // each other's context in Redis. Cleanup lives here rather than in a
  // route's finally block, since execution no longer sits inside a request.
  const nodeQueryId = `${flowId}-node${node.id}`;

  const options = {
    // Every tool call, internal agent step and provider selection inside the
    // handler comes back out through here and straight to the browser. This
    // is the live view: emitting alongside work that already happens, never
    // altering it.
    onEvent: (event) => emitFlowEvent(flowId, { ...event, nodeId: node.id }),
  };

  if (node.bodyFromNode) {
    const source = results[node.bodyFromNode];
    if (source && !source.failed) {
      options.body = source.result;
      if (node.subject) options.subject = node.subject;
    }
  }

  try {
    const outcome = await handler(buildNodePrompt(node, nodes, results), sessionId, nodeQueryId, options);
    const result = outcome?.content ?? "";
    // A handler can report failure by returning it rather than throwing — a
    // rejected Gmail send, for instance. That has to surface as a failed
    // node, or the live view shows it in the same colour as a success.
    const failed = Boolean(outcome?.failed);
    emitFlowEvent(flowId, { type: "node_result", nodeId: node.id, intent: node.intent, result, failed });
    return { result, failed };
  } catch (err) {
    const result = `Node failed: ${err.message}`;
    emitFlowEvent(flowId, { type: "node_result", nodeId: node.id, intent: node.intent, result, failed: true });
    return { result, failed: true };
  } finally {
    await clearQueryContext(nodeQueryId).catch(() => {});
  }
}

/**
 * Executes the flow and resolves once every runnable node is done. Callers
 * are expected NOT to await this in a request handler — progress is reported
 * over the WebSocket, and the return value is for tests and logging.
 */
export async function runFlow(flowId) {
  const flow = await getFlow(flowId);
  if (!flow) return null;

  const nodes = flow.nodes;
  const previousResults = flow.results || {};
  const dirty = dirtySet(nodes, flow.ranNodes || [], previousResults);

  await updateFlow(flowId, { status: "running" });
  emitFlowEvent(flowId, { type: "flow_start", total: nodes.length, willRun: dirty.size });

  // Clean nodes keep their stored results and are announced as reused, so
  // the graph shows them already complete instead of blank.
  const results = {};
  for (const node of nodes) {
    if (dirty.has(node.id)) continue;
    const stored = previousResults[node.id];
    if (!stored) continue;
    results[node.id] = stored;
    emitFlowEvent(flowId, { type: "node_skipped", nodeId: node.id, intent: node.intent, result: stored.result, reused: true });
  }

  const browseLimiter = createLimiter(1);
  const pending = new Set(nodes.filter((node) => dirty.has(node.id)).map((node) => node.id));
  const inFlight = new Map();

  try {
    while (pending.size > 0 || inFlight.size > 0) {
      const byId = new Map(nodes.map((n) => [n.id, n]));
      const ready = [...pending].filter((id) => {
        const node = byId.get(id);
        // Eligible once every dependency has produced a result. A
        // dependency that isn't in the flow at all counts as satisfied —
        // validateGraph rejects those, and treating one as unsatisfiable
        // here would hang the run instead of just running the node.
        return (node.dependsOn || []).every((dep) => !byId.has(dep) || results[dep] !== undefined);
      });

      // Start everything currently eligible, together.
      const startingNow = ready.filter((id) => !inFlight.has(id));
      for (const id of startingNow) {
        const node = nodes.find((n) => n.id === id);
        pending.delete(id);

        const task = (async () => {
          const serial = SERIAL_INTENTS.has(node.intent);
          if (serial) await browseLimiter.acquire();
          try {
            return await runNode(node, { flowId, sessionId: flow.sessionId, nodes, results, alongside: startingNow });
          } finally {
            if (serial) browseLimiter.release();
          }
        })();

        inFlight.set(id, task);
      }

      if (inFlight.size === 0) {
        // Nothing runnable and nothing running: the remainder is blocked by
        // a dependency that never produced a result (a cycle, or a node
        // whose dependency failed). Report them rather than hanging.
        for (const id of pending) {
          const node = nodes.find((n) => n.id === id);
          const result = "Skipped: a node it depends on did not complete.";
          results[id] = { result, failed: true };
          emitFlowEvent(flowId, { type: "node_result", nodeId: id, intent: node.intent, result, failed: true });
        }
        pending.clear();
        break;
      }

      // Wait for whichever finishes first, then re-evaluate what that frees.
      const [finishedId, outcome] = await Promise.race(
        [...inFlight.entries()].map(async ([id, task]) => [id, await task])
      );
      results[finishedId] = outcome;
      inFlight.delete(finishedId);
    }

    // The answer is whatever the terminal nodes produced — the ones nothing
    // else waits on.
    const dependedUpon = new Set(nodes.flatMap((node) => node.dependsOn || []));
    const answer = nodes
      .filter((node) => !dependedUpon.has(node.id))
      .map((node) => results[node.id]?.result)
      .filter(Boolean)
      .join("\n\n");

    await updateFlow(flowId, {
      status: "done",
      results,
      answer,
      // Snapshot of what was actually executed, so the next run can tell
      // which nodes changed since.
      ranNodes: nodes.map(({ id, intent, task, dependsOn, bodyFromNode, recipient, repoUrl }) => ({ id, intent, task, dependsOn, bodyFromNode, recipient, repoUrl })),
    });
    emitFlowEvent(flowId, { type: "flow_done", answer });
    return { answer, results };
  } catch (err) {
    await updateFlow(flowId, { status: "failed", error: err.message });
    emitFlowEvent(flowId, { type: "flow_failed", error: err.message });
    throw err;
  }
}
