/**
 * Runs once, before the browse agent starts, specifically for the browse
 * intent. Takes the user's raw request and asks the LLM (via the same
 * shared fallback chain every other workflow uses) to break it into an
 * ordered list of steps, each with its own explicit completion criteria —
 * not just a numbered-text plan, but structured data the browse loop
 * (browseAgentLoop.js) can act on programmatically: run one step at a
 * time, check that step's own criteria before moving to the next, and
 * hand each step's confirmed result down as context for the next.
 *
 * The completion-criteria field exists because of a real, reproduced
 * failure: asked to sort/filter YouTube results, the agent attempted it,
 * never confirmed it actually took effect, and reported success anyway.
 * Making "what counts as done" an explicit, separately-checkable field per
 * step — authored by an isolated planning call with nothing else to react
 * to — is what a single freeform plan-as-text couldn't reliably enforce on
 * its own.
 */
import { completeWithFallback } from "../llmFallback.js";

const SYSTEM_PROMPT =
  "You break a web-browsing/search task into an ordered list of steps for a browser-automation " +
  "agent. A simple, single-fact task (e.g. \"what is the time in India right now\") is exactly ONE " +
  "step. A task with multiple distinct goals, or one that needs a claim verified (sorting, " +
  "filtering, ranking, comparing), is broken into as many steps as it genuinely needs — no more.\n\n" +
  "For EACH step, provide:\n" +
  "- \"description\": exactly what to do in this step (search for X, navigate to Y, read Z).\n" +
  "- \"completionCriteria\": a concrete, checkable condition for when this SPECIFIC step is done — " +
  "not the whole task. If the step involves a claim about order, filtering, or matching some " +
  "property (a date, a price, a category), the criteria MUST require actually reading and " +
  "confirming that property from the page — never just 'a filter was applied' or 'a URL parameter " +
  "was set,' since neither is proof anything actually happened.\n\n" +
  "Return ONLY a JSON object of this exact shape, no markdown, no extra text:\n" +
  '{"steps":[{"description":"...","completionCriteria":"..."}]}';

function parseSteps(raw) {
  let text = (raw || "").trim().replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "").trim();
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    const match = text.match(/\{[\s\S]*\}/);
    if (!match) return null;
    try {
      parsed = JSON.parse(match[0]);
    } catch {
      return null;
    }
  }
  if (!Array.isArray(parsed?.steps) || parsed.steps.length === 0) return null;
  const steps = parsed.steps
    .map((s) => ({ description: String(s?.description || "").trim(), completionCriteria: String(s?.completionCriteria || "").trim() }))
    .filter((s) => s.description);
  return steps.length > 0 ? steps : null;
}

/**
 * @returns {Promise<{description: string, completionCriteria: string}[]>}
 *   Always at least one step — falls back to a single step covering the
 *   whole raw query, with a generic completion criteria, if the planning
 *   call fails or returns something unparseable, so a step-breaker hiccup
 *   never blocks the browse intent entirely.
 */
export async function breakQueryIntoSteps(query, queryId) {
  try {
    const message = await completeWithFallback({
      queryId,
      messages: [
        { role: "system", content: SYSTEM_PROMPT },
        { role: "user", content: query },
      ],
    });
    const steps = parseSteps(message.content);
    if (steps) return steps;
  } catch {
    // Fall through to the single-step fallback below.
  }
  return [{ description: query, completionCriteria: "The request has been answered using information actually read from a tool result." }];
}
