import { completeChat } from "../llmClient.js";

/**
 * The research intent's whole job: answer directly from the model's own
 * knowledge. No web search, no browser, no repo access — just one LLM call.
 * (A retrieval-backed version, if ever added, would be a different intent,
 * not a variant of this one.)
 */
const SYSTEM_PROMPT =
  "You answer directly and concisely from your own knowledge. " +
  "If you are not confident about a fact, say so explicitly rather than guessing.";

export async function runResearch(prompt) {
  const content = await completeChat([
    { role: "system", content: SYSTEM_PROMPT },
    { role: "user", content: prompt },
  ]);
  return { intent: "research", content };
}
