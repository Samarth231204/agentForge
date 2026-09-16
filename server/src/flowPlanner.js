/**
 * The TOP-LEVEL query breaker — one layer above the intents.
 *
 * Deliberately separate from tools/queryStepBreaker.js, which is browse's
 * own internal breaker and stays exactly as it is: that one splits a single
 * browsing task into fine-grained steps with completion criteria, INSIDE
 * the browse intent, after this planner has already decided a given node
 * belongs to browse at all. Two levels, two files, no shared code:
 *
 *   this file        "research X, write it up, email it to jane@..."
 *                     → [{research}, {write}, {google}] as a dependency graph
 *   queryStepBreaker  a single browse node → its own internal steps
 *
 * What each node's task sentence must contain is not invented here — it
 * comes from intentShapes.js, the same declaration the handlers themselves
 * use to extract those fields. That's what keeps a generated node runnable
 * unattended: the planner is told `google` needs a literal email address,
 * so it writes one, so google.js's own extraction finds it with nobody
 * sitting there to be asked.
 *
 * The planner emits an ORDERED LIST; the edges between nodes are then
 * assigned deterministically by flowGraph.assignDefaultEdges. Asking the
 * model to invent the graph from scratch would put the user's scheduling
 * rule at the mercy of a language model on every single request; deriving
 * it in code means the default wiring is always exactly the rule. Edits are
 * different — there the model does move edges, because that is the point,
 * and repairGraph catches whatever it breaks.
 */
import { completeWithFallback } from "./llmFallback.js";
import { INTENTS, classifyIntent } from "./intents.js";
import { plannerShape } from "./intentShapes.js";
import { assignDefaultEdges, validateGraph } from "./flowGraph.js";

const KNOWN_INTENTS = INTENTS.map((intent) => intent.name);

/**
 * Multi-step markers. Their absence, combined with a clean single-intent
 * regex match, is what lets a simple request skip the planner call entirely
 * — otherwise "what is the speed of light" would cost two LLM calls (one to
 * discover it's a single node, one to answer it) where today it costs one.
 */
const MULTI_STEP_MARKERS = [
  /\bthen\b/i,
  /\bafter (that|which)\b/i,
  /\band (also|then)\b/i,
  /\bonce (that|you|it)\b/i,
  /\bfollowed by\b/i,
  /\bnext,\b/i,
  /\bfinally\b/i,
  /\bfor each\b/i,
  /\balso (send|email|open|write|research|search)\b/i,
  // "separately" was missing and caused a real miss: "email the update to
  // alice@…, and separately email it to bob@…" classified as one google
  // intent with no sequencing word, short-circuited to a single send, and
  // bob's email was silently lost.
  /\bseparately\b/i,
  /\bin parallel\b/i,
  /\bat the same time\b/i,
  /\bas well as\b/i,
  /\beach of\b/i,
  /\bboth\b[\s\S]*\band\b/i,
];

/**
 * Deterministic coverage signals: phrasing that unambiguously requires a
 * particular intent, because no other intent can carry out that action.
 *
 * This exists because the planner really does drop actions on longer
 * requests, confirmed in testing: a five-action request ("research X,
 * search for Y, write it up, open a PR, email it") came back as three
 * nodes with the PR and the email silently missing, and a request ending
 * in two emails came back with neither. A prompt rule alone was not
 * enough, so the plan is now checked in code and the planner is told
 * exactly what it left out.
 */
const COVERAGE_SIGNALS = {
  google: [/\bemail\b/i, /\be-?mail\b/i, /\bsend\b[\s\S]*\bmail\b/i, /\bmail\b[\s\S]*\bto\b[\s\S]*@/i],
  github: [/\bpull request\b/i, /\bopen a pr\b/i, /\bcommit\b/i, /\bpush\b/i, /github\.com\//i],
  browse: [/\bsearch\b/i, /\blook up\b/i, /\bfind\b[\s\S]*\bonline\b/i, /\bon the web\b/i, /\blatest\b/i],
};

/**
 * Intents the prompt clearly demands. Only strong, unambiguous phrasing
 * counts — this drives an automatic correction, so a false positive would
 * add a node nobody asked for.
 */
export function detectRequiredIntents(prompt) {
  return Object.entries(COVERAGE_SIGNALS)
    .filter(([, patterns]) => patterns.some((pattern) => pattern.test(prompt)))
    .map(([intent]) => intent);
}

/**
 * True when this prompt clearly needs no planning at all: the existing
 * deterministic classifier matched exactly one intent, and there's no
 * sequencing language suggesting more than one thing is being asked for.
 */
export function isObviouslySingleStep(prompt) {
  const { intent } = classifyIntent(prompt);
  if (intent === "unclassified") return false;

  // A request that demands two different capabilities is multi-step by
  // definition, whatever words join them. This check matters more than the
  // marker list below and exists because that list kept leaking: "fix the
  // logging in <repo> and email me the result" has no sequencing word the
  // markers recognize ("and email" is not "and then"), so it was handled as
  // a single github request and the email was silently never sent. Relying
  // on which conjunction someone happened to type is the wrong mechanism;
  // what actually settles it is how many capabilities the request needs.
  if (detectRequiredIntents(prompt).length > 1) return false;

  return !MULTI_STEP_MARKERS.some((marker) => marker.test(prompt));
}

function missingIntents(prompt, nodes) {
  const planned = new Set(nodes.map((node) => node.intent));
  return detectRequiredIntents(prompt).filter((intent) => !planned.has(intent));
}

function buildIntentCatalogue() {
  return INTENTS.map((intent) => {
    const shape = plannerShape(intent.name);
    const requirements = Object.entries(shape)
      .map(([field, hint]) => `      - ${field}: ${hint}`)
      .join("\n");
    return `  ${intent.name} — ${intent.description}\n    The "task" SENTENCE of a ${intent.name} node must mention:\n${requirements}`;
  }).join("\n\n");
}

function buildSystemPrompt() {
  return (
    `You plan how to carry out a user's request using a fixed set of agents, one per intent.\n\n` +
    `AVAILABLE INTENTS AND WHAT EACH ONE'S TOOLS REQUIRE:\n\n${buildIntentCatalogue()}\n\n` +
    `First list every distinct ACTION the user asked for in the "actions" array. Then produce the FEWEST nodes that cover ALL of them, giving each node exactly one intent. Long requests are where actions get dropped, so check your nodes against your own actions list before answering.\n\n` +
    `RULES, in order of importance:\n` +
    `1. COVERAGE IS MANDATORY. Every action the user asked for must be covered by a node. Each intent above has exactly ONE capability and no others — no intent can do another's job:\n` +
    `     - Sending an email is ONLY possible with a google node. browse, write and research CANNOT send anything.\n` +
    `     - Changing a repo or opening a pull request is ONLY possible with a github node.\n` +
    `     - Reading anything live from the web is ONLY possible with a browse node.\n` +
    `   Example of a FORBIDDEN plan: for "search youtube for AI news and also email me a summary at me@example.com", returning only a browse node is WRONG — browse cannot send email, so the emailing was silently dropped. The correct plan is TWO nodes: a browse node to find the news, then a google node to email it. Dropping a requested action is the worst possible failure; using one node too many is far better.\n` +
    `2. Subject to rule 1, never split what a SINGLE intent can do in one call. "Research X and write a report on it" is ONE write node, not a research node plus a write node, because the write intent can do both. Extra nodes cost real time and money.\n` +
    `3. Every node's "task" must be a self-contained instruction that states everything its intent requires, as listed above. A node that omits a required detail (an email address, a repo URL) will fail when it runs, because nothing will be available to ask the user for it.\n` +
    `4. Never invent a required detail the user did not give you. If the user named no recipient, still write the node, but leave the address out rather than making one up.\n` +
    `5. Never include credentials, tokens, or passwords in any task, even if the user supplied them. They are provided separately at run time.\n` +
    `6. When a node should send or publish text that an EARLIER node produces, do not restate that text. Set "bodyFromNode" to that earlier node's id and give a "subject" instead — this avoids paying to write the same content twice.\n` +
    `7. List nodes in the order they should happen: anything depending on an earlier result comes after it.\n` +
    `8. The items listed under each intent above are things the "task" sentence must MENTION — they are NOT JSON keys. Every node carries its instruction in "task" and nothing else: write {"intent":"google","task":"Email the poem to jane@example.com","recipient":"jane@example.com"}, never {"intent":"google","topic":"the poem"}. A task is a plain sentence, never prefixed with a field name.\n\n` +
    `Return ONLY a JSON object of this exact shape, no markdown, no commentary:\n` +
    `{"actions":["<every distinct action the user asked for>"],"nodes":[{"id":1,"intent":"<one of: ${KNOWN_INTENTS.join(", ")}>","task":"<self-contained instruction>","recipient":"<optional: email address if this is a google node and the user gave one>","repoUrl":"<optional: repo URL if this is a github node and the user gave one>","bodyFromNode":<optional: id of the node whose output is the body>,"subject":"<optional: only with bodyFromNode>"}]}`
  );
}

/**
 * Reads a node's instruction text, tolerating the field name the model
 * actually used.
 *
 * This is the fix for the worst bug found in testing: because each intent's
 * shape is described with named requirements ("a google node must state
 * recipient and topic"), the model frequently emits that content under the
 * requirement's own name — `{intent:"google", recipient:"...", topic:"A poem
 * about entanglement"}` with no `task` key at all. The parser used to
 * require it and silently dropped every such node, which is how requested
 * emails and pull requests kept vanishing from otherwise correct plans.
 * Accepting the alternative names keeps the node instead of deleting the
 * user's intent over a key name.
 */
const TASK_KEYS = ["task", "subtask", "topic", "question", "request", "description", "instruction"];

function readTask(node) {
  for (const key of TASK_KEYS) {
    const value = node[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

/**
 * Parses a planner or editor reply into nodes.
 *
 * `dependsOn` is read when the model supplies it (which it does on edits,
 * where rewiring is the whole point) and left absent otherwise, so the
 * caller can decide between assigning the default wiring and preserving
 * what already exists.
 */
function parseFlow(raw) {
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

  // Tolerate `steps` as well: it is what the older prompt used, and a model
  // echoing that key should not cost the user their whole plan.
  const rawNodes = Array.isArray(parsed?.nodes) ? parsed.nodes : Array.isArray(parsed?.steps) ? parsed.steps : null;
  if (!rawNodes || rawNodes.length === 0) return null;

  const nodes = rawNodes
    .map((node, index) => {
      // An unrecognized intent would dispatch to nothing, so drop the node
      // rather than letting the executor fail on it later.
      if (!KNOWN_INTENTS.includes(node.intent)) return null;
      const bodyFrom = node.bodyFromNode ?? node.bodyFromStep;
      return {
        id: Number(node.id) || index + 1,
        intent: node.intent,
        task: readTask(node),
        ...(Array.isArray(node.dependsOn) ? { dependsOn: node.dependsOn.map(Number).filter((n) => Number.isFinite(n)) } : {}),
        ...(node.recipient ? { recipient: String(node.recipient).trim() } : {}),
        ...(node.repoUrl ? { repoUrl: String(node.repoUrl).trim() } : {}),
        ...(bodyFrom ? { bodyFromNode: Number(bodyFrom) } : {}),
        ...(node.subject ? { subject: String(node.subject).trim() } : {}),
      };
    })
    .filter((node) => node && node.task);

  return nodes.length > 0 ? sanitizeBodyReferences(normalizeIds(nodes)) : null;
}

/**
 * Renumbers nodes 1..N by position and remaps every reference to match.
 *
 * The model's own ids are not trustworthy: a real plan came back with two
 * different nodes both labelled id 2. Duplicates quietly corrupt several
 * things at once — `bodyFromNode` resolves to whichever matching node is
 * found first, the executor's result lookup becomes ambiguous, dependency
 * edges point at the wrong vertex, and the UI renders two elements with the
 * same React key. Since ids are purely internal plumbing for references,
 * deriving them from position removes the whole class of problem. Where an
 * old id was duplicated, references map to its first occurrence, which is
 * what a lookup would have found anyway.
 */
function normalizeIds(nodes) {
  const oldToNew = new Map();
  nodes.forEach((node, index) => {
    if (!oldToNew.has(node.id)) oldToNew.set(node.id, index + 1);
  });

  const remap = (id) => oldToNew.get(id);

  return nodes.map((node, index) => {
    const renumbered = { ...node, id: index + 1 };

    if (node.dependsOn) {
      renumbered.dependsOn = [...new Set(node.dependsOn.map(remap).filter((id) => id !== undefined && id !== index + 1))];
    }

    if (node.bodyFromNode !== undefined) {
      const mapped = remap(node.bodyFromNode);
      if (mapped === undefined) delete renumbered.bodyFromNode;
      else renumbered.bodyFromNode = mapped;
    }

    return renumbered;
  });
}

/**
 * `bodyFromNode` is the one field the model gets wrong in ways that matter,
 * and it got them wrong in real testing: after being asked to insert a node,
 * it renumbered the plan but left the old references behind — producing a
 * `write` node claiming its body came from a LATER `google` node, and a
 * `google` node pointing at the `browse` node instead of the `write` node
 * whose output was actually meant to be sent.
 *
 * Both are silent failures rather than crashes (the executor simply finds no
 * source and re-composes), so they are caught here:
 *   - only an intent that actually sends/publishes content can take a body
 *     from somewhere else; for anything else the field is meaningless
 *   - the referenced node must exist
 *
 * Where the reference is to a real node that this one simply does not wait
 * for, the dependency is ADDED rather than the reference dropped. That is
 * both what was obviously meant and free — dropping it would silently
 * downgrade the node into composing its own content instead.
 */
const INTENTS_THAT_SEND_CONTENT = new Set(["google"]);

function sanitizeBodyReferences(nodes) {
  const ids = new Set(nodes.map((node) => node.id));

  return nodes.map((node) => {
    if (node.bodyFromNode === undefined) return node;

    if (!INTENTS_THAT_SEND_CONTENT.has(node.intent) || !ids.has(node.bodyFromNode) || node.bodyFromNode === node.id) {
      const { bodyFromNode, ...rest } = node;
      return rest;
    }

    if (node.dependsOn && !node.dependsOn.includes(node.bodyFromNode)) {
      return { ...node, dependsOn: [...node.dependsOn, node.bodyFromNode] };
    }
    return node;
  });
}

/**
 * The graph repair agent loop.
 *
 * An edit that rewires edges can easily produce something unrunnable — a
 * cycle, a dependency on a node that no longer exists. Rather than refusing
 * the edit and making the user work out what went wrong, the specific
 * defects are handed back and the model is asked to correct them, the same
 * validate-and-nudge shape browseAgentLoop uses for a rejected tool call.
 *
 * The last known-good graph is always what gets returned if the loop can't
 * converge, so an unrunnable graph never reaches the executor.
 */
export async function repairGraph(nodes, queryId, { maxAttempts = 3, lastValid = null } = {}) {
  let current = nodes;

  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    const defects = validateGraph(current);
    if (defects.length === 0) return current;

    console.warn(`[flowPlanner] graph defects (attempt ${attempt + 1}): ${defects.join(" ")}`);

    try {
      const message = await completeWithFallback({
        queryId,
        messages: [
          {
            role: "system",
            content:
              `You fix dependency graphs. Each node has an "id", an "intent", a "task" and a "dependsOn" array of node ids it must wait for. ` +
              `Fix ONLY the listed problems, changing as little else as possible — keep every node, its intent and its task text. ` +
              `Return ONLY the JSON object {"nodes":[...]}, no prose.`,
          },
          {
            role: "user",
            content: `GRAPH:\n${JSON.stringify({ nodes: current }, null, 2)}\n\nPROBLEMS TO FIX:\n${defects.map((d) => `- ${d}`).join("\n")}`,
          },
        ],
      });
      const repaired = parseFlow(message.content);
      if (!repaired) break;
      current = repaired;
    } catch {
      break;
    }
  }

  // Could not converge. Prefer the caller's last good graph; failing that,
  // strip the edges entirely and fall back to the deterministic default
  // wiring, which is always valid by construction.
  if (validateGraph(current).length === 0) return current;
  if (lastValid && validateGraph(lastValid).length === 0) {
    console.warn("[flowPlanner] repair did not converge — keeping the previous valid graph.");
    return lastValid;
  }
  console.warn("[flowPlanner] repair did not converge — falling back to default wiring.");
  return assignDefaultEdges(current.map(({ dependsOn, ...node }) => node));
}

/**
 * @returns {Promise<Array<object>>} nodes with edges, always at least one.
 * A planning failure degrades to a single node for single-capability
 * requests and throws for multi-capability ones — see the reasoning below.
 */
export async function planFlow(prompt, queryId) {
  try {
    const messages = [
      { role: "system", content: buildSystemPrompt() },
      { role: "user", content: prompt },
    ];
    const message = await completeWithFallback({ queryId, messages });
    let nodes = parseFlow(message.content);

    // A candidate that ANSWERS but returns unusable content is not handled
    // by completeWithFallback, which only advances the chain on thrown
    // errors. That gap is real: once the capable models were rate-limited,
    // the weaker fallback answered with output this parser could not read,
    // and a ten-node request failed outright on a single malformed reply.
    // One corrective nudge on the same conversation — the same technique
    // browseAgentLoop uses for a rejected tool call — recovers most of
    // these, since it is usually a formatting slip rather than an inability
    // to do the task.
    if (!nodes) {
      console.warn("[flowPlanner] first plan was unparseable — asking once for clean JSON.");
      try {
        const retry = await completeWithFallback({
          queryId,
          messages: [
            ...messages,
            message,
            { role: "user", content: "That was not valid JSON. Reply with ONLY the JSON object described above — no prose, no markdown fences, nothing else." },
          ],
        });
        nodes = parseFlow(retry.content);
      } catch {
        // Leave nodes null; the failure handling below decides what is safe.
      }
    }

    // Coverage check in code, not on trust. If the request unambiguously
    // needs an intent the plan omitted, say so and let it try once more —
    // a dropped action is the worst failure this planner can produce, and
    // one extra call is cheap next to a silently incomplete run.
    if (nodes) {
      const missing = missingIntents(prompt, nodes);
      if (missing.length > 0) {
        console.warn(`[flowPlanner] plan omitted required intent(s): ${missing.join(", ")} — retrying once.`);
        // Isolated on purpose. The retry is a best-effort improvement on a
        // plan that already exists, so it must never be able to destroy it:
        // when the retry call itself failed (provider quota exhausted), the
        // exception used to escape to the outer catch and throw away a
        // perfectly good original plan, turning a partially-complete result
        // into a total failure exactly when capacity was scarcest.
        try {
          messages.push(message, {
            role: "user",
            content:
              `That plan is incomplete. The request also requires these intents, which your nodes are missing: ${missing.join(", ")}. ` +
              `Return the full corrected plan covering EVERY action, in the same JSON shape.`,
          });
          const retry = await completeWithFallback({ queryId, messages });
          const retried = parseFlow(retry.content);
          if (retried && missingIntents(prompt, retried).length < missing.length) nodes = retried;
        } catch {
          console.warn("[flowPlanner] coverage retry unavailable — keeping the original plan.");
        }
      }

      // Edges are assigned here, in code, so the user's scheduling rule is
      // guaranteed rather than left to the model on every request.
      return assignDefaultEdges(nodes.map(({ dependsOn, ...node }) => node));
    }
  } catch {
    // Fall through to the failure handling below.
  }

  // Planning genuinely failed (every provider exhausted, unparseable output).
  //
  // Collapsing to a single node here is only safe when the request needed
  // one capability anyway. When it clearly spans several, that fallback is
  // actively dangerous and was caught doing real damage in testing: a
  // ten-node, five-intent request hit a provider quota, fell back to one
  // node, got classified as `github`, and — with a PAT in session — was a
  // clone/diff/push/open-PR away from mutating a real repository on the
  // strength of a prompt the user never meant as a repo change. A transient
  // rate limit must never silently turn into one unintended side effect, so
  // multi-capability requests fail loudly instead.
  if (detectRequiredIntents(prompt).length > 1) {
    throw new Error("Could not plan this request — the model providers are unavailable right now. Nothing was run. Please retry shortly.");
  }

  const { intent } = classifyIntent(prompt);
  return [{ id: 1, intent: intent === "unclassified" ? "research" : intent, task: prompt, dependsOn: [] }];
}

/**
 * Applies a plain-English revision to an existing flow — including rewiring
 * the graph itself ("make the email wait on the search instead", "run those
 * two in parallel"), which is why the model is shown and allowed to change
 * `dependsOn` here where the initial plan derives it in code.
 *
 * Returns the revised nodes, or null if the instruction couldn't be applied,
 * so the caller can keep the current flow rather than replacing it with
 * something malformed.
 */
export async function reviseFlow(nodes, instruction, queryId) {
  const systemPrompt =
    `${buildSystemPrompt()}\n\n` +
    `You are now REVISING an existing flow rather than creating one. Every node also has a "dependsOn" array listing the ids of the nodes it waits for; nodes with no dependency between them run AT THE SAME TIME.\n` +
    `Apply exactly the requested change and nothing else — keep every other node's intent, task text and ids intact.\n` +
    `Return the full flow as {"nodes":[{"id":1,"intent":"...","task":"...","dependsOn":[...]}]}, including "dependsOn" on every node.\n` +
    `CRITICAL: dependencies must never form a cycle, and a node must never depend on itself. If you renumber anything, update every "dependsOn" and "bodyFromNode" to still point at the node it actually meant.`;

  try {
    const message = await completeWithFallback({
      queryId,
      messages: [
        { role: "system", content: systemPrompt },
        { role: "user", content: `CURRENT FLOW:\n${JSON.stringify({ nodes }, null, 2)}\n\nCHANGE TO MAKE:\n${instruction}` },
      ],
    });

    const revised = parseFlow(message.content);
    if (!revised) return null;

    // If the model dropped the wiring entirely, fall back to the default
    // rule rather than handing back an edgeless graph that would run
    // everything at once.
    const hasEdges = revised.some((node) => Array.isArray(node.dependsOn));
    const wired = hasEdges
      ? revised.map((node) => ({ ...node, dependsOn: node.dependsOn || [] }))
      : assignDefaultEdges(revised);

    return await repairGraph(wired, queryId, { lastValid: nodes });
  } catch {
    return null;
  }
}
