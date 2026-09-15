import "dotenv/config";
import crypto from "node:crypto";
import express from "express";
import cors from "cors";

import { classifyIntent } from "./src/intents.js";
import { runResearch } from "./src/handlers/research.js";
import { runWrite } from "./src/handlers/write.js";
import { runGithub } from "./src/handlers/github.js";
import { startSessionCleanupListener } from "./src/sessionCleanup.js";
import { clearQueryContext } from "./src/queryContext.js";

const app = express();
const PORT = process.env.PORT || 8000;

// One entry per real intent. Adding a new intent means: add its patterns to
// intents.js, add its handler function here, and add one line to this map —
// nothing else in this route changes. Every handler receives (prompt,
// sessionId, queryId) — research/write ignore sessionId; all three use
// queryId internally via the LLM fallback chain.
const HANDLERS = {
  research: runResearch,
  write: runWrite,
  github: runGithub,
};

startSessionCleanupListener();

app.use(cors({ origin: process.env.CLIENT_ORIGIN || "http://localhost:5173" }));
app.use(express.json());

app.get("/health", (req, res) => {
  res.json({ status: "ok" });
});

app.post("/api/tasks", async (req, res) => {
  const prompt = (req.body.prompt || "").trim();
  const sessionId = (req.body.sessionId || "").trim();
  if (!prompt) {
    return res.status(422).json({ error: "prompt cannot be blank" });
  }

  const classification = classifyIntent(prompt);

  if (classification.intent === "unclassified") {
    return res.json({
      intent: "unclassified",
      reason: classification.reason,
      content: "This request doesn't match any supported intent yet. Try a direct knowledge question (e.g. \"what is...\") or a writing request (e.g. \"write an email...\").",
    });
  }

  const handler = HANDLERS[classification.intent];
  if (!handler) {
    // A pattern in intents.js matched an intent name with no handler
    // registered above — fail loudly rather than silently falling through.
    return res.status(500).json({ error: `No handler wired for intent "${classification.intent}".` });
  }

  // A fresh id per request, not per session — this is what the LLM
  // fallback chain uses as its Redis key, so its conversation context is
  // cleared the moment THIS query finishes, independent of how long the
  // browser session (and its own separate Redis state) goes on to live.
  const queryId = crypto.randomUUID().replace(/-/g, "");

  try {
    const result = await handler(prompt, sessionId, queryId);
    return res.json({ ...result, reason: classification.reason });
  } catch (err) {
    console.error(`${classification.intent} handler failed:`, err);
    return res.status(502).json({ error: "The AI provider could not complete this request. Please retry shortly." });
  } finally {
    // Unconditional — cleared whether the query succeeded, failed, or an
    // exception propagated, so nothing lingers past its own request.
    await clearQueryContext(queryId);
  }
});

app.listen(PORT, () => {
  console.log(`AgentForge server listening on http://localhost:${PORT}`);
});
