import { useEffect, useRef, useState } from "react";
import FlowView from "./FlowView.jsx";
import { renderWithLinks } from "../linkify.jsx";
import "./Chat.css";

const SERVER_URL = import.meta.env.VITE_SERVER_URL || "http://localhost:4000";
const WS_URL = SERVER_URL.replace(/^http/, "ws");

// One hex session id per page load, reused for every request — this is what
// ties a follow-up message (e.g. supplying a PAT after being asked) back to
// the same server-side session state for GitHub tasks.
const SESSION_ID = crypto.randomUUID().replace(/-/g, "");

const emptyLive = { phase: "review", nodeState: {}, results: {}, activity: {}, willRun: null };

/**
 * One continuous conversation with one persistent flow.
 *
 * The important difference from a plain prompt box: a follow-up does NOT
 * start over. Once a flow exists, what you type edits that same flow, and
 * running it again resumes — nodes that already succeeded keep their
 * results. That is what makes "connect Gmail, then press run" finish the
 * one blocked node instead of re-planning and re-sending everything.
 */
export default function Chat() {
  const [entries, setEntries] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [flow, setFlow] = useState(null);
  const [live, setLive] = useState(emptyLive);
  const socketRef = useRef(null);
  const bottomRef = useRef(null);

  // Both effects use explicit blocks rather than concise arrow bodies: a
  // concise body returns whatever the expression evaluates to, and React
  // tries to call that as the cleanup function ("destroy is not a
  // function"), which blanks the whole component.
  useEffect(() => {
    return () => {
      socketRef.current?.close();
    };
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries, flow, live]);

  const say = (role, kind, payload) => setEntries((prev) => [...prev, { role, kind, ...payload }]);

  async function post(path, body) {
    const res = await fetch(`${SERVER_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return { ok: res.ok, data: await res.json().catch(() => ({})) };
  }

  async function handleSubmit(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;

    setInput("");
    say("user", "text", { text });
    setBusy(true);

    try {
      // With a flow already on screen, a message is an EDIT to it rather
      // than a new request — the whole point of the continuous chat.
      if (flow) {
        const { ok, data } = await post(`/api/flows/${flow.flowId}/edit`, { instruction: text });
        if (!ok) say("agent", "text", { text: data.error || "Could not apply that change." });
        else {
          setFlow(data);
          // Results are kept server-side; clear only the transient view so
          // edited nodes read as pending again.
          setLive((prev) => ({ ...prev, phase: "review", activity: {} }));
          say("agent", "text", { text: "Updated the flow. Review it and run when you're ready." });
        }
        return;
      }

      const { ok, data } = await post("/api/flows", { prompt: text, sessionId: SESSION_ID });
      if (!ok) {
        say("agent", "text", { text: data.error || "Something went wrong." });
      } else if (data.flowId) {
        setFlow(data);
        setLive(emptyLive);
        say("agent", "text", { text: "Here's the agentic flow I'd run. Edit it in plain English, or approve it." });
      } else {
        // A single-capability request never becomes a flow — it just answers.
        say("agent", "text", { text: data.content, intent: data.intent });
      }
    } catch {
      say("agent", "text", { text: `Could not reach the backend at ${SERVER_URL}.` });
    } finally {
      setBusy(false);
    }
  }

  function applyEvent(event) {
    setLive((prev) => {
      const next = { ...prev, nodeState: { ...prev.nodeState }, results: { ...prev.results }, activity: { ...prev.activity } };
      const push = (nodeId, kind, text) => {
        next.activity[nodeId] = [...(next.activity[nodeId] || []), { kind, text }];
      };

      switch (event.type) {
        case "flow_start":
          next.phase = "running";
          next.willRun = event.willRun;
          break;
        case "node_spawned":
          next.nodeState[event.nodeId] = "running";
          push(
            event.nodeId,
            "spawn",
            event.alongside?.length
              ? `spawned alongside ${event.alongside.map((id) => `#${id}`).join(", ")}`
              : event.waitedFor?.length
                ? `started after ${event.waitedFor.map((id) => `#${id}`).join(", ")}`
                : "spawned"
          );
          break;
        case "llm_call":
          push(event.nodeId, "llm", `${event.provider}/${event.model} ${event.outcome}`);
          break;
        case "tool_call":
          push(event.nodeId, "tool", `${event.tool}.${event.action}${event.url ? ` → ${event.url}` : ""}${event.recipient ? ` → ${event.recipient}` : ""}${event.query ? ` "${event.query}"` : ""}`);
          break;
        case "tool_result":
          push(event.nodeId, event.failed ? "fail" : "tool", `↳ ${event.summary}`);
          break;
        case "agent_step":
          push(event.nodeId, "step", event.label);
          break;
        case "node_result":
          next.nodeState[event.nodeId] = event.failed ? "failed" : "done";
          next.results[event.nodeId] = event.result;
          break;
        case "node_skipped":
          next.nodeState[event.nodeId] = "reused";
          next.results[event.nodeId] = event.result;
          push(event.nodeId, "skip", "reused earlier result");
          break;
        case "flow_done":
          next.phase = "done";
          next.answer = event.answer;
          break;
        case "flow_failed":
          next.phase = "done";
          next.answer = `Flow failed: ${event.error}`;
          break;
        default:
          break;
      }
      return next;
    });

    // Deliberately NOT pushed into the transcript. The flow stays pinned
    // below the messages as the thing being worked on, so a chat bubble
    // would land ABOVE it — meaning you'd scroll up to read the result of
    // something you just ran. It renders inside the flow instead, keeping
    // the reading order flow → answer → input.
  }

  async function handleRun() {
    if (!flow || busy) return;
    setBusy(true);
    setLive((prev) => ({ ...prev, phase: "running", activity: {} }));

    // Connected before the run is requested so no early event is missed.
    socketRef.current?.close();
    const socket = new WebSocket(`${WS_URL}/ws?flowId=${flow.flowId}`);
    socketRef.current = socket;

    socket.onmessage = (message) => {
      const event = JSON.parse(message.data);
      applyEvent(event);
      if (event.type === "flow_done" || event.type === "flow_failed") {
        setBusy(false);
        socket.close();
      }
    };
    socket.onerror = () => setBusy(false);

    const start = () => post(`/api/flows/${flow.flowId}/run`).catch(() => setBusy(false));
    if (socket.readyState === WebSocket.OPEN) start();
    else socket.onopen = start;
  }

  function handleNewTask() {
    socketRef.current?.close();
    setFlow(null);
    setLive(emptyLive);
    setEntries([]);
    setInput("");
  }

  return (
    <div className="chat">
      <div className="chat-scroll">
        {entries.length === 0 && !flow && (
          <div className="chat-empty">
            <p>Ask for something that takes more than one step.</p>
            <code>search the web for two testing libraries, write a comparison, then email it to me@example.com</code>
          </div>
        )}

        {entries.map((entry, i) => (
          <div className={`turn turn-${entry.role}`} key={i}>
            {entry.intent && <span className={`badge badge-${entry.intent}`}>{entry.intent}</span>}
            <p>{renderWithLinks(entry.text)}</p>
          </div>
        ))}

        {flow && (
          <FlowView
            flow={flow}
            live={live}
            busy={busy}
            onRun={handleRun}
            onCancelEdit={handleNewTask}
          />
        )}

        <div ref={bottomRef} />
      </div>

      <form className="chat-input" onSubmit={handleSubmit}>
        <input
          id="chat-message"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={flow ? "Change the flow in plain English, or paste a token…" : "Ask AgentForge to do something…"}
          disabled={busy}
          autoComplete="off"
        />
        <button type="submit" className="run-button" disabled={busy || !input.trim()}>
          {busy ? <span className="spinner" /> : flow ? "Edit" : "Send"}
        </button>
        {flow && (
          <button type="button" className="ghost-button" onClick={handleNewTask} disabled={busy}>
            New
          </button>
        )}
      </form>
    </div>
  );
}
