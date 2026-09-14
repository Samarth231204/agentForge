import { completeChat } from "../llmClient.js";

/**
 * The write intent's job: produce the requested piece of writing directly —
 * an email, essay, story, poem, letter, whatever the user asked for. Same
 * "one LLM call, no tools" shape as research, just a different system
 * prompt tuned for generating content instead of answering a question.
 */
const SYSTEM_PROMPT =
  "You are a skilled writer. Produce exactly the piece of writing the user " +
  "asked for (email, essay, story, poem, letter, script, etc.), matching " +
  "the tone and length implied by the request. Return only the finished " +
  "piece — no preamble like \"Here is your essay:\", no meta-commentary, " +
  "unless the user explicitly asked for notes or options as well.";

export async function runWrite(prompt) {
  const content = await completeChat([
    { role: "system", content: SYSTEM_PROMPT },
    { role: "user", content: prompt },
  ]);
  return { intent: "write", content };
}
