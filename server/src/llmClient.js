import OpenAI from "openai";

/**
 * Every provider in the fallback chain (Groq, Gemini, OpenRouter) speaks
 * the same OpenAI-compatible chat-completions format, so one client class
 * covers all three — only baseURL and the API key differ per provider.
 */
const PROVIDERS = {
  groq: { baseURL: "https://api.groq.com/openai/v1", apiKeyEnv: "GROQ_API_KEY" },
  gemini: { baseURL: "https://generativelanguage.googleapis.com/v1beta/openai/", apiKeyEnv: "GEMINI_API_KEY" },
  openrouter: { baseURL: "https://openrouter.ai/api/v1", apiKeyEnv: "OPENROUTER_API_KEY" },
};

const clients = {};

function getClient(provider) {
  if (!clients[provider]) {
    const config = PROVIDERS[provider];
    if (!config) throw new Error(`Unknown LLM provider "${provider}"`);
    const apiKey = process.env[config.apiKeyEnv];
    if (!apiKey) throw new Error(`${config.apiKeyEnv} is not set in server/.env`);
    clients[provider] = new OpenAI({ apiKey, baseURL: config.baseURL });
  }
  return clients[provider];
}

/**
 * One completion call against one specific (provider, model) candidate.
 * `tools`, when supplied, requests OpenAI-format tool-calling — used by the
 * browse intent's agent loop; research/write/github's read-and-change
 * workflows call this with no tools at all (plain text completion).
 */
export async function completeChatOnce({ provider, model, messages, tools, temperature = 0.3 }) {
  const response = await getClient(provider).chat.completions.create({
    model,
    temperature,
    messages,
    ...(tools ? { tools } : {}),
  });
  return response.choices[0].message;
}
