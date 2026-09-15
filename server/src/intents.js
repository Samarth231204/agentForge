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
    // Checked FIRST, deliberately: several github-read phrasings ("tell me
    // about this repo", "explain this part of the project", "summarize
    // this file") use the exact same verbs research's patterns look for
    // ("explain", "describe", "tell me about"). If research were checked
    // first, a prompt that literally contains a github.com URL or names
    // "this repo" would still get swallowed by research before github's
    // own patterns ever ran — confirmed happening in testing before this
    // reordering. Placing github first costs nothing for genuine research/
    // write prompts, since those essentially never mention repo/github/
    // pull-request vocabulary at all.
    name: "github",
    description:
      "A request to read, change, or otherwise act on a GitHub repository — routes to a read-only lookup or a sandboxed change+PR workflow depending on the query's own wording.",
    patterns: [
      /\bmake\b[\s\S]*\bchanges?\b[\s\S]*\b(my|this|the) (project|repo|repository|code|codebase)\b/i,
      /\badd\b[\s\S]*\b(functionality|feature)\b[\s\S]*\b(my|this|the) (project|repo|repository)\b/i,
      /\bfix\b[\s\S]*\bbug\b[\s\S]*\b(my|this|the) (project|repo|repository|code)\b/i,
      /\bopen a pull request\b/i,
      /\bpull request\b/i,
      /\bpush (this|these|the) changes?\b/i,
      /\bclone (my|this|the) repo(sitory)?\b/i,
      /\b(my|this|the) (repo|repository)\b/i,
      /github\.com\/[\w.-]+\/[\w.-]+/i,
      /\bgithub\b/i,
      // A bare PAT is itself an unambiguous github signal — the most
      // natural reply to "I need a token" is often JUST the token, with no
      // other github-flavored wording at all (confirmed missing this in
      // testing: such a reply fell through to "unclassified" and the token
      // was lost before this pattern was added).
      /\bghp_[A-Za-z0-9]{20,}\b/,
      /\bgithub_pat_[A-Za-z0-9_]{20,}\b/,
    ],
  },
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
  {
    name: "write",
    description:
      "Any content-generation request — drafting, composing, or creatively writing something for the user, as opposed to answering a factual question.",
    patterns: [
      // A writing-verb ... writing-noun, in EITHER order, with anything
      // (or nothing) in between — [\s\S]* is what makes "plot an
      // interesting story" match, not just the adjacent "plot a story".
      /\b(write|draft|compose|plot|create|generate|craft|invent|imagine|make up)\b[\s\S]*\b(essay|story|stories|poem|poetry|letter|email|mail|blog|article|script|speech|song|lyrics|report|summary|caption|post|proposal|paragraph|chapter|novel|note|bio|tagline|slogan|pitch|narrative|plot|plotline)\b/i,
      /\b(essay|story|poem|letter|email|blog|article|script|speech|song|lyrics|report|proposal|novel|narrative)\b[\s\S]*\b(write|draft|compose|plot|create|generate|craft|invent|imagine)\b/i,
      /\bhelp me write\b/i,
      /\bcome up with (a|an|some)\b[\s\S]*\b(story|poem|caption|tagline|slogan|title|name|idea)\b/i,
      /\bgive me (a|an)\b[\s\S]*\b(story|poem|essay|letter|email|script|speech|caption|tagline)\b/i,
      // Broadest catch-all last: anything else mentioning one of these verbs
      // at all still counts, since this intent is meant to cover "everything
      // related to write" per spec, not just the phrasings above.
      /\bwrite\b/i,
      /\bdraft\b/i,
      /\bcompose\b/i,
      /\bplot\b/i,
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
