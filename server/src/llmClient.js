import OpenAI from "openai";

/**
 * Groq's API is OpenAI-compatible, so the official `openai` SDK works
 * against it unmodified — just point baseURL at Groq's endpoint instead of
 * OpenAI's, and use a Groq API key.
 */
let client = null;

function getClient() {
  if (!client) {
    if (!process.env.GROQ_API_KEY) {
      throw new Error("GROQ_API_KEY is not set in server/.env");
    }
    client = new OpenAI({
      apiKey: process.env.GROQ_API_KEY,
      baseURL: "https://api.groq.com/openai/v1",
    });
  }
  return client;
}

export async function completeChat(messages) {
  const response = await getClient().chat.completions.create({
    model: process.env.GROQ_MODEL || "openai/gpt-oss-20b",
    temperature: 0.3,
    messages,
  });
  return response.choices[0].message.content ?? "";
}
