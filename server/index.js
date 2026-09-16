import "dotenv/config";
import crypto from "node:crypto";
import express from "express";
import cors from "cors";

import { classifyIntent } from "./src/intents.js";
import { runResearch } from "./src/handlers/research.js";
import { runWrite } from "./src/handlers/write.js";
import { runGithub } from "./src/handlers/github.js";
import { runBrowse } from "./src/handlers/browse.js";
import { runGoogle } from "./src/handlers/google.js";
import { startSessionCleanupListener } from "./src/sessionCleanup.js";
import { clearQueryContext } from "./src/queryContext.js";
import { getAuthUrl, exchangeCodeForTokens } from "./src/tools/googleAuth.js";
import { saveGoogleTokens, getGoogleTokens } from "./src/googleSession.js";
import { planFlow, reviseFlow, isObviouslySingleStep, detectRequiredIntents } from "./src/flowPlanner.js";
import { harvestCredentials, redactCredentials, extractFields, isBareCredentialReply } from "./src/intentShapes.js";
import { checkCredentials, looksLikeGoAhead } from "./src/credentialGate.js";
import { getSessionState, saveSessionState } from "./src/session.js";
import { saveFlow, getFlow, updateFlow } from "./src/flows.js";
import { runFlow } from "./src/flowExecutor.js";
import { toLevels } from "./src/flowGraph.js";
import { attachWebSocketServer } from "./src/ws.js";

const app = express();
const PORT = process.env.PORT || 8000;

// One entry per real intent. Adding a new intent means: add its patterns to
// intents.js, add its handler function here, and add one line to this map —
// nothing else in this route changes. Every handler receives (prompt,
// sessionId, queryId) — research/write/browse ignore sessionId; all four
// use queryId internally via the LLM fallback chain.
const HANDLERS = {
  research: runResearch,
  write: runWrite,
  github: runGithub,
  browse: runBrowse,
  google: runGoogle,
};

startSessionCleanupListener();

app.use(cors({ origin: process.env.CLIENT_ORIGIN || "http://localhost:5173" }));
app.use(express.json());

app.get("/health", (req, res) => {
  res.json({ status: "ok" });
});

// Google OAuth: `state` is always the caller's own sessionId, round-tripped
// unchanged by Google, which is how the resulting token gets tied to the
// right session with no other state needed. See handlers/google.js for how
// a missing-token session ends up with this link in a chat reply.
app.get("/auth/google", (req, res) => {
  const state = (req.query.state || "").trim();
  if (!state) return res.status(400).send("Missing state (session id).");
  try {
    res.redirect(getAuthUrl(state));
  } catch (err) {
    res.status(503).send(err.message);
  }
});

app.get("/auth/google/status", async (req, res) => {
  const state = (req.query.state || "").trim();
  if (!state) return res.status(400).json({ error: "Missing state (session id)." });
  const tokens = await getGoogleTokens(state);
  res.json({ connected: Boolean(tokens) });
});

app.get("/auth/google/callback", async (req, res) => {
  const { code, state } = req.query;
  if (!code || !state) return res.status(400).send("<h3>Missing code or state.</h3>");
  try {
    const tokens = await exchangeCodeForTokens(code);
    await saveGoogleTokens(state, tokens);
    res.send("<h3>Google account connected. You can close this tab and return to AgentForge.</h3>");
  } catch (err) {
    console.error("Google OAuth callback failed:", err);
    res.status(400).send("<h3>Could not connect your Google account. Please close this tab and try again.</h3>");
  }
});

/**
 * The original single-intent path: classify, dispatch, respond. Factored out
 * of the /api/tasks route so /api/pipelines can reuse it verbatim when a
 * request turns out not to need a pipeline at all.
 *
 * Returns either {status, body} for an error case or {body} to send as-is.
 */
async function runSingleIntent(prompt, sessionId) {
  const classification = classifyIntent(prompt);

  if (classification.intent === "unclassified") {
    return {
      body: {
        intent: "unclassified",
        reason: classification.reason,
        content: "This request doesn't match any supported intent yet. Try a direct knowledge question (e.g. \"what is...\"), a writing request (e.g. \"write an email...\"), or a browsing/search request (e.g. \"go to...\", \"search for...\").",
      },
    };
  }

  const handler = HANDLERS[classification.intent];
  if (!handler) {
    // A pattern in intents.js matched an intent name with no handler
    // registered above — fail loudly rather than silently falling through.
    return { status: 500, body: { error: `No handler wired for intent "${classification.intent}".` } };
  }

  // A fresh id per request, not per session — this is what the LLM
  // fallback chain uses as its Redis key, so its conversation context is
  // cleared the moment THIS query finishes, independent of how long the
  // browser session (and its own separate Redis state) goes on to live.
  const queryId = crypto.randomUUID().replace(/-/g, "");

  try {
    const result = await handler(prompt, sessionId, queryId);
    return { body: { ...result, reason: classification.reason } };
  } catch (err) {
    console.error(`${classification.intent} handler failed:`, err);
    return { status: 502, body: { error: "The AI provider could not complete this request. Please retry shortly." } };
  } finally {
    // Unconditional — cleared whether the query succeeded, failed, or an
    // exception propagated, so nothing lingers past its own request.
    await clearQueryContext(queryId);
  }
}

app.post("/api/tasks", async (req, res) => {
  const prompt = (req.body.prompt || "").trim();
  const sessionId = (req.body.sessionId || "").trim();
  if (!prompt) {
    return res.status(422).json({ error: "prompt cannot be blank" });
  }

  const { status, body } = await runSingleIntent(prompt, sessionId);
  return res.status(status || 200).json(body);
});

// ---------------------------------------------------------------------------
// Agentic flows — plan, edit in chat, run, then edit and run again.
//
// Unlike the pipeline routes these replace, a flow is long-lived: it is
// planned once and then edited and re-run across as many chat turns as the
// user wants. That is why nothing here 409s after a run — editing a
// finished flow and running it again is the main path, not an error, and
// it is how supplying a credential completes a blocked node without
// redoing the work that already succeeded.
// ---------------------------------------------------------------------------

/** Adds the derived execution levels, so the client never computes them. */
function withLevels(flowId, flow) {
  return { flowId, ...flow, levels: toLevels(flow.nodes).map((level) => level.map((node) => node.id)) };
}

app.post("/api/flows", async (req, res) => {
  const incoming = (req.body.prompt || "").trim();
  const sessionId = (req.body.sessionId || "").trim();
  if (!incoming) {
    return res.status(422).json({ error: "prompt cannot be blank" });
  }

  // Harvested FIRST, before anything branches on the prompt. A message that
  // is nothing but a pasted PAT is how the user answers "I need a token",
  // so the value has to reach session state even on paths that would
  // otherwise treat that message as an ordinary request.
  const credentials = harvestCredentials(incoming);
  if (Object.keys(credentials).length > 0 && sessionId) {
    const existing = (await getSessionState(sessionId)) || {};
    await saveSessionState(sessionId, { ...existing, ...credentials });
  }

  // When the previous turn stopped to ask for a credential, the request the
  // user actually wants run is the one they made before being interrupted —
  // their latest message is a pasted token or a "done", not a new task.
  const sessionState = (await getSessionState(sessionId)) || {};
  const answeringAnAsk =
    Boolean(sessionState.pendingFlowPrompt) &&
    (looksLikeGoAhead(incoming) || Object.keys(credentials).length > 0 || isBareCredentialReply("github", incoming, extractFields("github", incoming)));
  const prompt = answeringAnAsk ? sessionState.pendingFlowPrompt : incoming;

  // Ask for credentials BEFORE planning, not mid-run. Otherwise a flow gets
  // planned, displayed and approved, and only then does a node stop to say
  // Gmail was never connected — after earlier nodes have already executed,
  // and after a planning call was spent on a request that could not finish.
  const needed = detectRequiredIntents(prompt);
  if (needed.length > 0 && sessionId) {
    const { satisfied, asks, intents } = await checkCredentials(needed, sessionId, prompt);
    if (!satisfied) {
      await saveSessionState(sessionId, { ...sessionState, pendingFlowPrompt: prompt });
      return res.json({
        needsCredentials: intents,
        intent: intents[0],
        content:
          `Before I can plan this, I need ${asks.join(" and ")}.` +
          (asks.length > 1 ? " Send them here and I'll build the flow." : " Send it here and I'll build the flow."),
      });
    }
  }

  // Everything required is present. Drop the stashed request so a later,
  // unrelated message is never answered with this one.
  if (sessionState.pendingFlowPrompt) {
    await saveSessionState(sessionId, { ...sessionState, pendingFlowPrompt: null });
  }

  // Cheap regex check: a request that clearly needs one capability and shows
  // no sequencing language never pays for a planning call at all, so simple
  // questions cost exactly what they did before flows existed.
  if (isObviouslySingleStep(prompt)) {
    const { status, body } = await runSingleIntent(prompt, sessionId);
    return res.status(status || 200).json(body);
  }

  // Scrubbed from everything this route persists or returns — generated
  // nodes never carry a token, and plan text is both displayed and stored.
  const safePrompt = redactCredentials(prompt);

  const planQueryId = crypto.randomUUID().replace(/-/g, "");
  let nodes;
  try {
    // The planner is given the redacted prompt: it has no legitimate use for
    // a token, and its output is displayed and stored.
    nodes = await planFlow(safePrompt, planQueryId);
  } catch (err) {
    console.error("flow planning failed:", err);
    // The planner's own message is user-facing and specific — it says
    // explicitly that nothing was run, which matters for a request that
    // would otherwise have sent mail or touched a repository.
    return res.status(502).json({ error: err.message || "Could not plan this request. Please retry shortly." });
  } finally {
    await clearQueryContext(planQueryId);
  }

  // Really one node after all — no flow to review, so behave like a normal
  // request and answer straight into the chat.
  if (nodes.length === 1) {
    const { status, body } = await runSingleIntent(prompt, sessionId);
    return res.status(status || 200).json(body);
  }

  const flowId = crypto.randomUUID().replace(/-/g, "");
  const flow = { sessionId, prompt: safePrompt, nodes, status: "pending_review", results: {}, ranNodes: [] };
  await saveFlow(flowId, flow);
  return res.json(withLevels(flowId, flow));
});

app.post("/api/flows/:id/edit", async (req, res) => {
  const instruction = (req.body.instruction || "").trim();
  if (!instruction) {
    return res.status(422).json({ error: "instruction cannot be blank" });
  }

  const flow = await getFlow(req.params.id);
  if (!flow) {
    return res.status(404).json({ error: "That flow has expired or does not exist." });
  }
  if (flow.status === "running") {
    return res.status(409).json({ error: "This flow is still running. Wait for it to finish before editing it." });
  }

  const editQueryId = crypto.randomUUID().replace(/-/g, "");
  let revised;
  try {
    revised = await reviseFlow(flow.nodes, instruction, editQueryId);
  } finally {
    await clearQueryContext(editQueryId);
  }

  if (!revised) {
    // Keep the existing flow rather than replacing it with something
    // malformed — the user can rephrase.
    return res.status(422).json({ error: "Could not apply that change. Try rephrasing it." });
  }

  // Status returns to pending_review so the edited flow reads as awaiting
  // approval again, and `results`/`ranNodes` are deliberately preserved:
  // they are what let the next run skip whatever the edit didn't touch.
  const updated = await updateFlow(req.params.id, { nodes: revised, status: "pending_review" });
  return res.json(withLevels(req.params.id, updated));
});

app.post("/api/flows/:id/run", async (req, res) => {
  const flow = await getFlow(req.params.id);
  if (!flow) {
    return res.status(404).json({ error: "That flow has expired or does not exist." });
  }
  if (flow.status === "running") {
    return res.status(409).json({ error: "This flow is already running." });
  }

  // Deliberately not awaited: a run can take minutes, and progress is
  // reported over the WebSocket rather than in this response.
  runFlow(req.params.id).catch((err) => console.error(`flow ${req.params.id} failed:`, err));

  return res.json({ flowId: req.params.id, status: "running" });
});

app.get("/api/flows/:id", async (req, res) => {
  const flow = await getFlow(req.params.id);
  if (!flow) {
    return res.status(404).json({ error: "That flow has expired or does not exist." });
  }
  return res.json(withLevels(req.params.id, flow));
});

const httpServer = app.listen(PORT, () => {
  console.log(`AgentForge server listening on http://localhost:${PORT}`);
});

// Shares the HTTP server (and therefore the port) rather than opening a
// second listener — clients connect to ws://host:PORT/ws?flowId=...
attachWebSocketServer(httpServer);
