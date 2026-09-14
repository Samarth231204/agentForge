import "dotenv/config";
import express from "express";
import cors from "cors";

import { classifyIntent } from "./src/intents.js";
import { runResearch } from "./src/handlers/research.js";
import { runWrite } from "./src/handlers/write.js";

const app = express();
const PORT = process.env.PORT || 8000;

// One entry per real intent. Adding a new intent means: add its patterns to
// intents.js, add its handler function here, and add one line to this map —
// nothing else in this route changes.
const HANDLERS = {
  research: runResearch,
  write: runWrite,
};

app.use(cors({ origin: process.env.CLIENT_ORIGIN || "http://localhost:5173" }));
app.use(express.json());

app.get("/health", (req, res) => {
  res.json({ status: "ok" });
});

app.post("/api/tasks", async (req, res) => {
  const prompt = (req.body.prompt || "").trim();
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

  try {
    const result = await handler(prompt);
    return res.json({ ...result, reason: classification.reason });
  } catch (err) {
    console.error(`${classification.intent} handler failed:`, err);
    return res.status(502).json({ error: "The AI provider could not complete this request. Please retry shortly." });
  }
});

app.listen(PORT, () => {
  console.log(`AgentForge server listening on http://localhost:${PORT}`);
});
