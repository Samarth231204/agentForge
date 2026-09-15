/**
 * The cheap, deterministic classification layer. Every entry in INTENTS is
 * checked in order against the user's prompt; the first pattern that
 * matches wins. This runs before any LLM call — most prompts never need
 * one just to figure out *what kind* of request they are.
 *
 * Adding a new intent later means adding one more entry here, nothing else
 * in this file needs to change.
 *
 * ORDER MATTERS, deliberately, and has been fixed twice after real
 * failures found in testing:
 *   1. "github" first — several github-read phrasings ("tell me about
 *      this repo", "explain...") share verbs with "research" ("explain",
 *      "describe", "tell me about"); a github.com URL was being swallowed
 *      by research before github's own patterns got a chance.
 *   2. "browse" before "research" — "what is the current version of
 *      node.js" was being answered from the model's own stale training
 *      data by research's broad "what is" pattern, before browse (which
 *      would actually run a live search) ever got a chance to run at all.
 *   3. "google" before "write" — "draft a mail" / "send an email" share the
 *      draft/write/compose verbs write's catch-all patterns match against;
 *      without google checked first, a genuine send request would be
 *      silently downgraded into write's plain content-generation reply
 *      instead of ever reaching the Gmail send workflow.
 */
const INTENTS = [
  {
    name: "github",
    description:
      "A request to read, change, or otherwise act on a GitHub repository — routes to a read-only lookup or a sandboxed change+PR workflow depending on the query's own wording.",
    patterns: [
      /\bmake\b[\s\S]*\bchanges?\b[\s\S]*\b(my|this|the) (project|repo|repository|code|codebase)\b/i,
      /\badd\b[\s\S]*\b(functionality|feature)\b[\s\S]*\b(my|this|the) (project|repo|repository)\b/i,
      // Broader than the functionality/feature-specific pattern above:
      // "add a navbar to my project" names a concrete thing to add, not
      // the word "functionality" itself — found missing this in testing.
      /\badd\b[\s\S]*\bto\b[\s\S]*\b(my|this|the) (project|repo|repository|code|codebase)\b/i,
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
    // Checked before "write": see ORDER MATTERS note above.
    name: "google",
    description:
      "A request to send an email through a connected Google account — Gmail today, other Google sub-intents (calendar, drive) may be added later behind the same top-level intent.",
    patterns: [
      /\b(send|draft|compose|write)\b[\s\S]*\b(email|mail|gmail)\b/i,
      /\bemail\b[\s\S]*\bto\b[\s\S]*@/i,
      /\bmail\b[\s\S]*\bto\b[\s\S]*@/i,
      /\bsend\b[\s\S]*\bto\b[\s\S]*@[\w-]+\.[a-z]{2,}\b/i,
      /\bconnect (my )?google( account)?\b/i,
      /\bconnect gmail\b/i,
      /\bgmail\b/i,
      // A short reply that's essentially just an email address (optionally
      // with a few filler words like "the recipient is") is itself an
      // unambiguous signal — the natural reply to "tell me the recipient's
      // email address" is often JUST the address. Same reasoning as
      // github's bare-PAT pattern: bounded prefix/suffix length keeps this
      // from matching a genuinely different, longer message that merely
      // happens to mention an email address mid-sentence.
      /^[\s\S]{0,50}[\w.+-]+@[\w-]+\.[a-z]{2,}[\s\S]{0,10}$/i,
    ],
  },
  {
    // Checked before "research": see ORDER MATTERS note above.
    name: "browse",
    description:
      "Web search and/or interacting with a specific site — one continuous agent loop that searches, navigates, reads, and follows links as needed, from a one-shot lookup up to a multi-hop research task.",
    patterns: [
      // A navigation verb followed by anything other than a generic filler
      // word is an unambiguous signal — whether the target is a literal
      // URL ("go to https://...") or a site named by brand ("open
      // YouTube") — even though the underlying goal can look like a plain
      // research question on the surface. The negative lookahead is what
      // rejects "open a file" while still accepting "open youtube".
      /\b(?:go to|navigate to|open|visit)\s+(?!(?:a|an|the|this|that|it|new|my|your|some|another|file|files)\b)[a-z0-9]/i,
      /\bsearch for\b/i,
      /\bsearch the web\b/i,
      /\blook up\b/i,
      /\bbook a\b/i,
      /\bbooking\b/i,
      /\breservation\b/i,
      /\breserve a\b/i,
      /\bcheck availability\b/i,
      // Broad catch-all: "search mars on youtube", "search X", etc. — any
      // other phrasing of "search" at all still counts, same reasoning as
      // write's catch-all (missed "search mars on YouTube" in testing
      // before this was added — "search for"/"search the web" alone
      // didn't cover "search X on Y").
      /\bsearch\b/i,
      /\bfind (out )?(information|info) (about|on)\b/i,
      // Live/current-info signals — deliberately phrase-level, not a bare
      // /\bcurrent\b/i, since "current" alone is a common homonym (e.g.
      // "explain the current through this circuit" is a stable-physics
      // research question, not a live-data one).
      /\bcurrent version\b/i,
      /\blatest version\b/i,
      /\blatest news\b/i,
      /\bup[- ]to[- ]date\b/i,
      /\bright now\b/i,
      /\bas of (today|now)\b/i,
      /\btoday'?s\b/i,
      /\bthis (week|month|year)'?s\b/i,
      /\bnewest\b/i,
      /\bcurrent (price|status|state)\b/i,
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
