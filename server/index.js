import "dotenv/config";
import express from "express";
import cors from "cors";

import { classifyIntent } from "./src/intents.js";
import { runResearch } from "./src/handlers/research.js";

const app = express();
const PORT = process.env.PORT || 8000;

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
      content: "This request doesn't match any supported intent yet. Try a direct knowledge question (e.g. \"what is...\", \"explain...\").",
    });
  }

  if (classification.intent === "research") {
    try {
      const result = await runResearch(prompt);
      return res.json({ ...result, reason: classification.reason });
    } catch (err) {
      console.error("research handler failed:", err);
      return res.status(502).json({ error: "The AI provider could not complete this request. Please retry shortly." });
    }
  }

  // Unreachable today (only "research" and "unclassified" exist), but kept
  // explicit so a future intent with no handler wired yet fails loudly
  // instead of silently falling through.
  return res.status(500).json({ error: `No handler wired for intent "${classification.intent}".` });
});

app.listen(PORT, () => {
  console.log(`AgentForge server listening on http://localhost:${PORT}`);
});
