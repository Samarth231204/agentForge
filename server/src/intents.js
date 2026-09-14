/**
 * The cheap, deterministic classification layer. Every entry in INTENTS is
 * checked in order against the user's prompt; the first pattern that
 * matches wins. This runs before any LLM call — most prompts never need
 * one just to figure out *what kind* of request they are.
 *
 * Adding a new intent later means adding one more entry here, nothing else
 * in this file needs to change.
 */
const INTENTS = [
  {
    name: "research",
    description:
      "A direct-knowledge question the LLM can answer on its own, with no external tool (no web search, no browser, no repo access).",
    patterns: [
      /\bwhat is\b/i,
      /\bwhat are\b/i,
      /\bwho is\b/i,
      /\bwho was\b/i,
      /\bexplain\b/i,
      /\bdefine\b/i,
      /\bdescribe\b/i,
      /\bhow does\b/i,
      /\bhow do\b/i,
      /\bwhy does\b/i,
      /\bwhy is\b/i,
      /\btell me about\b/i,
      /\bcompare\b/i,
      /\bdifference between\b/i,
    ],
  },
];

/**
 * classifyIntent(prompt) -> { intent, reason }
 *
 * Deliberately has no LLM fallback yet — a prompt matching nothing above
 * comes back "unclassified" rather than guessing, since "research" is the
 * only real intent implemented so far. As more intents (and eventually an
 * LLM-based fallback classifier) are added, this is the one function every
 * new capability plugs into.
 */
export function classifyIntent(prompt) {
  const text = prompt.toLowerCase().trim();

  for (const intent of INTENTS) {
    const matched = intent.patterns.some((pattern) => pattern.test(text));
    if (matched) {
      return {
        intent: intent.name,
        reason: `Matched a "${intent.name}" keyword pattern.`,
      };
    }
  }

  return {
    intent: "unclassified",
    reason: "No known intent pattern matched this prompt.",
  };
}
