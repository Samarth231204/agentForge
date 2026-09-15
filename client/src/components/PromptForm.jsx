import { useState } from "react";
import "./PromptForm.css";

const SERVER_URL = import.meta.env.VITE_SERVER_URL || "http://localhost:4000";

const URL_PATTERN = /(https?:\/\/[^\s]+)/g;

// Splits response text around raw URLs (e.g. the Google OAuth consent link
// the google intent returns when a session isn't connected yet) so they
// render as real, clickable <a> tags instead of inert text.
function renderWithLinks(text) {
  // URL_PATTERN has a capturing group, so split() interleaves the matched
  // URLs into the result at odd indices — checking index parity avoids
  // re-testing against the same stateful global-flag regex (whose lastIndex
  // would otherwise make repeated .test() calls unreliable).
  return text.split(URL_PATTERN).map((part, i) =>
    i % 2 === 1 ? (
      <a key={i} href={part} target="_blank" rel="noopener noreferrer">
        {part}
      </a>
    ) : (
      part
    )
  );
}

// One hex session id per page load, reused for every request — this is what
// ties a follow-up message (e.g. supplying a PAT after being asked) back to
// the same server-side session state for GitHub tasks.
const SESSION_ID = crypto.randomUUID().replace(/-/g, "");

export default function PromptForm() {
  const [prompt, setPrompt] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!prompt.trim() || isRunning) return;

    setIsRunning(true);
    setError(null);
    setResult(null);

    try {
      const res = await fetch(`${SERVER_URL}/api/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: prompt.trim(), sessionId: SESSION_ID }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Something went wrong.");
      } else {
        setResult(data);
      }
    } catch {
      setError(`Could not reach the backend at ${SERVER_URL}.`);
    } finally {
      setIsRunning(false);
    }
  }

  return (
    <div className="prompt-card">
      <form onSubmit={handleSubmit}>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder='Try a direct knowledge question, e.g. "What is the speed of light?"'
          rows={3}
          disabled={isRunning}
        />
        <div className="prompt-footer">
          <span className="hint">Try "what is...", "explain...", "compare..."</span>
          <button type="submit" className="run-button" disabled={isRunning || !prompt.trim()}>
            {isRunning && <span className="spinner" />}
            {isRunning ? "Running" : "Run task"}
          </button>
        </div>
      </form>

      {error && <div className="error-box">{error}</div>}

      {result && (
        <div className="result-box">
          <div className="intent-row">
            <span className={`intent-badge ${result.intent === "unclassified" ? "unclassified" : ""}`}>
              {result.intent}
            </span>
            {result.reason && <span className="intent-reason">{result.reason}</span>}
          </div>
          <p className="result-content">{renderWithLinks(result.content)}</p>
        </div>
      )}
    </div>
  );
}
