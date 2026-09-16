/**
 * The browse intent's own, dedicated agent loop — deliberately NOT the
 * generic agentLoop.js, and not shared with any other intent. The control
 * flow here is fundamentally different from a single flat loop running
 * until one final_answer: the query is broken into steps up front
 * (queryStepBreaker.js), and this file runs ONE SUB-LOOP PER STEP,
 * sequentially — each sub-loop works only toward its own step's
 * completionCriteria (not the whole task), and each step's confirmed
 * result is handed down as context to the next step's sub-loop. A simple,
 * single-fact query ("what is the time in India right now") comes back as
 * exactly one step, so it runs exactly one sub-loop — the multi-step
 * machinery costs nothing extra for the simple case.
 *
 * Exactly ONE Chromium browser AND ONE page are launched for the entire
 * query — shared across every step's sub-loop, not one per step — so a
 * three-step task still only ever pays for one browser launch, and later
 * steps see whatever page state earlier steps left behind (still logged
 * in, still on the right site, etc.) rather than starting cold each time.
 */
import { chromium } from "playwright";
import { completeWithFallback, isInvalidToolCall } from "./llmFallback.js";
import { BrowserTool, BROWSER_TOOL_NAME, BROWSER_TOOL_DESCRIPTION, BROWSER_TOOL_SCHEMA } from "./tools/browserTool.js";
import { breakQueryIntoSteps } from "./tools/queryStepBreaker.js";

const STEP_COMPLETE_TOOL = {
  type: "function",
  function: {
    name: "step_complete",
    description: "Call this once THIS step's own completion criteria is genuinely satisfied, confirmed from what you actually read via browser_action — not assumed. Not for the whole task, just this one step.",
    parameters: {
      type: "object",
      properties: { result: { type: "string", description: "What this step found/accomplished, to hand to the next step (or return as the final answer, if this was the last step)." } },
      required: ["result"],
    },
  },
};

const TOOL_SCHEMAS = [
  { type: "function", function: { name: BROWSER_TOOL_NAME, description: BROWSER_TOOL_DESCRIPTION, parameters: BROWSER_TOOL_SCHEMA } },
  STEP_COMPLETE_TOOL,
];

// The GROUNDING_RULE exists because of a real, reproduced failure mode: a
// dynamically-rendered page (YouTube specifically) can still appear "empty"
// immediately after navigate() even with the settle-time fix in
// browserTool.js — and a model handed a thin/empty result has been
// observed filling the gap with plausible-sounding facts from its own
// training data instead of reporting the page as empty or reading again.
const GROUNDING_RULE =
  "GROUNDING RULE: every fact you report must come from a browser_action result you actually read " +
  "this conversation. If a result is empty, thin, or clearly not yet rendered (common on " +
  "JavaScript-heavy sites immediately after navigating), that means 'read again' or 'try a " +
  "different approach' — never 'fill the gap from what I already know.' Do not call step_complete " +
  "until this step's completion criteria is actually, verifiably satisfied.";

function buildStepSystemPrompt(step, priorContext) {
  return (
    `You are working on ONE step of a larger browsing task, using the browser_action tool. ` +
    `Stay focused on just this step — do not attempt the rest of the task.\n\n` +
    `STEP: ${step.description}\n\n` +
    `THIS STEP IS COMPLETE WHEN: ${step.completionCriteria}\n\n` +
    (priorContext ? `CONTEXT FROM EARLIER STEPS:\n${priorContext}\n\n` : "") +
    `${GROUNDING_RULE}`
  );
}

/** One step's own sub-loop — bounded independently so one hard step can't
 * silently consume the entire task's budget without the others ever
 * getting a turn. */
async function runStepLoop({ step, priorContext, browserTool, queryId, maxIterations, onEvent }) {
  const systemPrompt = buildStepSystemPrompt(step, priorContext);
  const messages = [
    { role: "system", content: systemPrompt },
    { role: "user", content: step.description },
  ];

  for (let iteration = 0; iteration < maxIterations; iteration++) {
    let message;
    try {
      message = await completeWithFallback({ queryId, messages, tools: TOOL_SCHEMAS, temperature: 0.2, onEvent });
    } catch (err) {
      if (!isInvalidToolCall(err)) throw err;
      messages.push({
        role: "user",
        content: `Your last tool call was rejected: ${err.message}. The only tools that exist are: browser_action, step_complete. Call one of those instead.`,
      });
      continue;
    }
    messages.push(message);

    if (!message.tool_calls || message.tool_calls.length === 0) {
      const trimmedContent = (message.content || "").trim();
      if (trimmedContent) return { result: trimmedContent, turnsUsed: iteration + 1 };
      // Same recurring quirk found in the generic loop: a turn can drift
      // into completely empty content with no tool call. Nudge rather than
      // silently treating that as this step being done.
      messages.push({
        role: "user",
        content: "You returned an empty response with no tool call. If this step's completion criteria is actually satisfied, call step_complete now. Otherwise call browser_action to continue.",
      });
      continue;
    }

    for (const call of message.tool_calls) {
      let args = {};
      try {
        args = JSON.parse(call.function.arguments || "{}");
      } catch {
        // Malformed arguments — fed back as a tool error below so the model
        // can see the problem and retry with corrected JSON next turn.
      }

      if (call.function.name === "step_complete") {
        onEvent?.({ type: "agent_step", label: `completion criteria satisfied: ${step.completionCriteria}`, phase: "done" });
        return { result: args.result || "Step completed with no result given.", turnsUsed: iteration + 1 };
      }

      let result;
      try {
        result = await browserTool.run(args.action, args.query, args.max_results, args.url, args.selector, args.text);
      } catch (err) {
        result = `Error: ${err.message}`;
      }

      messages.push({
        role: "tool",
        tool_call_id: call.id,
        content: typeof result === "string" ? result : JSON.stringify(result),
      });
    }
  }

  return {
    result: `Reached the step's iteration limit before its completion criteria (${step.completionCriteria}) was confirmed. Best effort so far is above, but treat it as unverified.`,
    turnsUsed: maxIterations,
  };
}

/**
 * @returns {Promise<{answer: string, stepsUsed: number, turnsUsed: number}>}
 */
export async function runBrowseQuery(prompt, queryId, { maxIterationsPerStep = 8, onEvent } = {}) {
  onEvent?.({ type: "agent_step", label: "breaking the browsing task into steps", phase: "planning" });
  const steps = await breakQueryIntoSteps(prompt, queryId);
  onEvent?.({ type: "agent_step", label: `${steps.length} browsing step${steps.length === 1 ? "" : "s"} planned`, phase: "planned" });

  // One browser, one page, for the whole query — every step's sub-loop
  // shares it, so a three-step task pays for one launch, not three, and
  // later steps see whatever page state earlier ones left behind.
  const browser = await chromium.launch({ headless: true });
  const browserTool = new BrowserTool(browser, onEvent);

  try {
    let context = "";
    let lastResult = "";
    let totalTurnsUsed = 0;

    for (const [index, step] of steps.entries()) {
      onEvent?.({ type: "agent_step", label: step.description, phase: "running", step: index + 1, of: steps.length });
      const { result, turnsUsed } = await runStepLoop({ step, priorContext: context, browserTool, queryId, maxIterations: maxIterationsPerStep, onEvent });
      lastResult = result;
      totalTurnsUsed += turnsUsed;
      context += `- Step "${step.description}": ${result}\n`;
    }

    return { answer: lastResult, stepsUsed: steps.length, turnsUsed: totalTurnsUsed };
  } finally {
    // Always runs — a mid-task error must not leave a headless Chromium
    // process orphaned. Closes exactly once, after every step is done.
    await browser.close().catch(() => {});
  }
}
