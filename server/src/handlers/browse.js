/**
 * The browse intent's entry point — thin adapter matching the standard
 * (prompt, sessionId, queryId) -> {intent, content} handler shape every
 * intent uses. All the actual browse-specific logic (step breaking,
 * per-step sub-loops, the single shared browser/page) lives in
 * browseAgentLoop.js — a dedicated file, deliberately not the generic
 * agentLoop.js, since browse's step-sequenced control flow doesn't match
 * any other intent's shape.
 */
import { runBrowseQuery } from "../browseAgentLoop.js";

export async function runBrowse(prompt, sessionId, queryId, options = {}) {
  const { answer, stepsUsed, turnsUsed } = await runBrowseQuery(prompt, queryId, { onEvent: options.onEvent });
  return { intent: "browse", stepsUsed, turnsUsed, content: answer };
}
