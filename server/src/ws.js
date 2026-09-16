/**
 * The live channel for watching an agentic flow run.
 *
 * Needed because execution is deliberately decoupled from the HTTP request
 * that approved it: the run route returns immediately and work continues in
 * the background, so there is no response left open to stream progress
 * down. Clients connect with the flow id they want to watch and receive
 * every event as it happens — nodes spawning, what they started beside and
 * waited for, each browser call and its URL, which provider answered.
 *
 * The registry is in-process memory rather than Redis on purpose: a socket
 * only exists within the process holding it, so there is nothing for
 * another process to usefully read.
 */
import { WebSocketServer } from "ws";

/** flowId -> Set<WebSocket> */
const watchers = new Map();

export function attachWebSocketServer(httpServer) {
  const wss = new WebSocketServer({ server: httpServer, path: "/ws" });

  wss.on("connection", (socket, request) => {
    const params = new URL(request.url, "http://localhost").searchParams;
    // `pipelineId` is accepted as well so an older client still connects.
    const flowId = params.get("flowId") || params.get("pipelineId");
    if (!flowId) {
      socket.close(1008, "flowId required");
      return;
    }

    if (!watchers.has(flowId)) watchers.set(flowId, new Set());
    watchers.get(flowId).add(socket);

    socket.on("close", () => {
      const set = watchers.get(flowId);
      if (!set) return;
      set.delete(socket);
      if (set.size === 0) watchers.delete(flowId);
    });
  });

  console.log("[ws] Flow event channel listening on /ws");
  return wss;
}

/**
 * Fire-and-forget: a run must never fail or stall because nobody is
 * watching, or because a watcher's socket died mid-send.
 */
export function emitFlowEvent(flowId, event) {
  const sockets = watchers.get(flowId);
  if (!sockets || sockets.size === 0) return;

  const payload = JSON.stringify({ ...event, at: Date.now() });
  for (const socket of sockets) {
    if (socket.readyState !== socket.OPEN) continue;
    try {
      socket.send(payload);
    } catch {
      // Dropped client — the close handler cleans it out of the registry.
    }
  }
}
