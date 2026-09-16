import { renderWithLinks } from "../linkify.jsx";
import "./FlowView.css";

const INTENT_ORDER = ["research", "browse", "write", "github", "google"];

/**
 * The agentic flow, drawn as a tree: depth is execution order, siblings run
 * at the same time.
 *
 * A dependency graph is not always a tree — a node can wait on two parents
 * at once — and drawing it as one would mean either duplicating that node in
 * two branches (misrepresenting one node as two) or silently hiding an edge.
 * Instead every node appears exactly once at its correct depth, and any
 * dependency that isn't simply "the level above" is shown explicitly as a
 * chip. So the picture is always honest about what waits for what.
 */
export default function FlowView({ flow, live, onRun, onCancelEdit, busy }) {
  const { nodes, levels } = flow;
  const byId = new Map(nodes.map((node) => [node.id, node]));

  return (
    <div className="flow">
      {levels.map((levelIds, depth) => (
        <div className="flow-level" key={depth}>
          {depth > 0 && <div className="flow-join" aria-hidden="true" />}

          <div className="flow-row">
            {levelIds.map((id) => {
              const node = byId.get(id);
              if (!node) return null;
              const state = live.nodeState[id];
              const result = live.results[id];
              const activity = live.activity[id] || [];

              return (
                <article className={`node node-${state || "idle"}`} key={id}>
                  <header className="node-head">
                    <span className={`badge badge-${node.intent}`}>{node.intent}</span>
                    <span className="node-id">#{node.id}</span>
                    {state === "running" && <span className="spinner" />}
                    {state === "done" && <span className="tick">done</span>}
                    {state === "failed" && <span className="cross">failed</span>}
                    {state === "reused" && <span className="reused">reused</span>}
                  </header>

                  <p className="node-task">{node.task}</p>

                  {(node.dependsOn?.length > 0 || node.bodyFromNode) && (
                    <div className="node-edges">
                      {node.dependsOn?.length > 0 && (
                        <span className="chip" title="Waits for these nodes to finish">
                          waits on {node.dependsOn.map((d) => `#${d}`).join(", ")}
                        </span>
                      )}
                      {node.bodyFromNode && (
                        <span className="chip chip-reuse" title="Sends that node's output directly — no extra model call">
                          body from #{node.bodyFromNode}
                        </span>
                      )}
                    </div>
                  )}

                  {(node.recipient || node.repoUrl) && <p className="node-field">{node.recipient || node.repoUrl}</p>}

                  {/* The live inference view: what this agent is doing right now. */}
                  {activity.length > 0 && (
                    <ul className="node-activity">
                      {activity.slice(-6).map((line, i) => (
                        <li key={i} className={`act act-${line.kind}`}>
                          {line.text}
                        </li>
                      ))}
                    </ul>
                  )}

                  {result !== undefined && <p className="node-result">{renderWithLinks(result)}</p>}
                </article>
              );
            })}
          </div>
        </div>
      ))}

      {live.answer && (
        <div className="flow-answer">
          <span className="answer-label">Result</span>
          <p>{renderWithLinks(live.answer)}</p>
        </div>
      )}

      <div className="flow-actions">
        <span className="flow-meta">
          {nodes.length} node{nodes.length === 1 ? "" : "s"} · {levels.length} level{levels.length === 1 ? "" : "s"}
          {live.willRun !== null && live.willRun !== undefined && ` · ${live.willRun} to run`}
        </span>
        {onCancelEdit && (
          <button type="button" className="ghost-button" onClick={onCancelEdit} disabled={busy}>
            Discard
          </button>
        )}
        <button type="button" className="run-button" onClick={onRun} disabled={busy}>
          {live.phase === "running" ? "Running" : live.phase === "done" ? "Run again" : "Approve & run"}
        </button>
      </div>
    </div>
  );
}

export { INTENT_ORDER };
