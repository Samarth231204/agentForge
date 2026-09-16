/**
 * The single declaration of what each intent actually needs to function.
 *
 * Before this file existed, every intent kept that knowledge private inside
 * its own handler — github.js owned its PAT/repo regexes and its own
 * hand-written "I need a GitHub Personal Access Token" string, google.js
 * owned its email regex and its own wording. Nothing outside those files
 * could know what an intent required, which is fine while a human is
 * sitting in the chat to fill any gap, but breaks the moment a planner has
 * to WRITE a request for one of those intents without asking anyone.
 *
 * Three consumers read this one declaration:
 *   1. flowPlanner.js — serializes plannerHint/freeform into the
 *      planning prompt, so a generated step says "Email jane@example.com
 *      about X" rather than "Email the report to Jane" (which would stall
 *      unattended: the pattern finds no address and nobody is watching).
 *   2. handlers/github.js and handlers/google.js — extract their own
 *      required fields and build their own missing-field asks from here,
 *      so what they check for can never drift from what the planner was
 *      told to provide.
 *   3. Anything added later declares its shape once and every consumer
 *      picks it up for free.
 *
 * `fields` are things findable in a prompt via a pattern (and mirrored into
 * session state). `freeform` is the rest of what a good request for that
 * intent contains — never extracted, only described to the planner.
 */

/**
 * Fields marked `credential: true` are NEVER shown to the planner. Plan text
 * is rendered in the UI and persisted in Redis, so asking an LLM to produce
 * a token would put credentials in both. Credentials stay session-supplied,
 * exactly as they were before this file.
 */
export const INTENT_SHAPES = {
  github: {
    fields: {
      token: {
        pattern: /\b(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b/,
        sessionKey: "token",
        credential: true,
        missingAsk: "a GitHub Personal Access Token",
      },
      repoUrl: {
        // Protocol optional — people type "github.com/owner/repo" bare as
        // often as the full "https://" form; `normalize` adds it back.
        pattern: /\b(?:https:\/\/)?github\.com\/[\w.-]+\/[\w.-]+(?:\.git)?\b/i,
        sessionKey: "repoUrl",
        normalize: (url) => (url.startsWith("http") ? url : `https://${url}`),
        plannerHint: "the full repository URL (https://github.com/owner/repo)",
        missingAsk: "the repository URL (https://github.com/owner/repo)",
      },
    },
    freeform: {
      mode: "whether this is a read/question or a change that should end in a pull request",
      task: "what to read, or exactly what to change",
    },
  },

  google: {
    fields: {
      recipient: {
        pattern: /\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b/i,
        sessionKey: "gmailRecipient",
        plannerHint: "a literal email address to send to — never a person's name alone",
        missingAsk: "the recipient's email address",
      },
    },
    freeform: {
      topic: "what the email should say or be about",
    },
  },

  browse: {
    fields: {},
    freeform: {
      task: "a self-contained, unambiguous web task — what to find or do, and on which site if it matters",
    },
  },

  research: {
    fields: {},
    freeform: {
      question: "a direct, self-contained question answerable without any external tool",
    },
  },

  write: {
    fields: {},
    freeform: {
      request: "what to write, plus tone and length if they matter",
    },
  },
};

/**
 * Pulls whatever declared fields are present in `prompt`, normalized.
 * Returns only what was found — absent fields are simply omitted, so a
 * caller can spread this over existing session state without clobbering
 * values supplied on an earlier turn.
 */
export function extractFields(intent, prompt) {
  const shape = INTENT_SHAPES[intent];
  if (!shape) return {};

  const found = {};
  for (const [name, field] of Object.entries(shape.fields)) {
    const match = prompt.match(field.pattern)?.[0];
    if (match) found[name] = field.normalize ? field.normalize(match) : match;
  }
  return found;
}

/**
 * True when this prompt is essentially JUST the answer to "I need X" — a
 * pasted token, a bare email address, "the repo is <url>" — rather than a
 * fresh request that happens to contain one of those values.
 *
 * The distinction matters because both credential-gated handlers stash the
 * blocked request as a pending query and replay it once the missing piece
 * arrives. Without this check they replay it whenever a field shows up at
 * all, which is wrong and was caught sending a real email: a session that
 * had earlier stashed "connect my google account" received a brand-new
 * "send an email to <addr> saying <message>" request, matched on the
 * address, and silently sent the STALE query's content instead of the new
 * message. Replaying a stored request over an explicit new one is never
 * right, so the replay is now limited to prompts that carry no request of
 * their own.
 */
const REPLY_FILLER = /\b(the|my|is|are|here|here's|it's|its|use|using|this|that|and|please|thanks|ok|okay|sure|recipient|email|e-?mail|address|repo|repository|url|token|pat|account)\b/gi;

export function isBareCredentialReply(intent, prompt, foundFields) {
  const values = Object.values(foundFields || {});
  if (values.length === 0) return false;

  let remainder = String(prompt ?? "");
  for (const value of values) remainder = remainder.split(value).join(" ");
  // Also drop the raw matches, since a normalized value (e.g. an https://
  // prefix added to a bare repo URL) won't string-match the original text.
  const shape = INTENT_SHAPES[intent];
  if (shape) {
    for (const field of Object.values(shape.fields)) {
      remainder = remainder.replace(new RegExp(field.pattern.source, `${field.pattern.flags.replace("g", "")}g`), " ");
    }
  }

  const leftover = remainder.replace(REPLY_FILLER, " ").replace(/[^\w\s]/g, " ").trim().split(/\s+/).filter(Boolean);
  return leftover.length <= 2;
}

/** The session-state key a given field is mirrored into. */
export function sessionKeyFor(intent, fieldName) {
  return INTENT_SHAPES[intent]?.fields[fieldName]?.sessionKey;
}

/**
 * The user-facing phrases for missing fields, in declaration order so the
 * wording is stable rather than dependent on object iteration luck.
 * `extras` covers asks that aren't prompt-extractable fields at all — e.g.
 * google's "your Google account connected", which is an OAuth state, not
 * something findable in text.
 */
export function describeMissing(intent, missingFieldNames, extras = []) {
  const shape = INTENT_SHAPES[intent];
  if (!shape) return [...extras];

  const fromFields = Object.entries(shape.fields)
    .filter(([name]) => missingFieldNames.includes(name))
    .map(([, field]) => field.missingAsk);

  return [...extras, ...fromFields];
}

/** Every declared field marked as a credential, across all intents. */
function credentialFields() {
  return Object.entries(INTENT_SHAPES).flatMap(([intent, shape]) =>
    Object.entries(shape.fields)
      .filter(([, field]) => field.credential)
      .map(([name, field]) => ({ intent, name, field }))
  );
}

/**
 * Strips credentials out of text that is about to be stored or displayed.
 *
 * A pipeline's prompt lives in Redis on a sliding TTL and is rendered back
 * in the UI, which is a much longer and more visible life than a single
 * request's context had. If a user pastes a PAT into a compound request,
 * that token should be moved into session state (see harvestCredentials)
 * and scrubbed from everything that gets persisted or shown.
 */
export function redactCredentials(text) {
  let redacted = String(text ?? "");
  for (const { field } of credentialFields()) {
    // Rebuilt per call with /g so replace hits every occurrence without
    // mutating the shared declaration's own lastIndex.
    const global = new RegExp(field.pattern.source, field.pattern.flags.includes("g") ? field.pattern.flags : `${field.pattern.flags}g`);
    redacted = redacted.replace(global, "[redacted]");
  }
  return redacted;
}

/**
 * Pulls any credentials out of a prompt and returns them keyed by the
 * session field they belong in. The pipeline path needs this because a
 * generated step deliberately never carries a token, so without harvesting
 * it here the credential the user just supplied would be lost and the step
 * would ask for it again.
 */
export function harvestCredentials(prompt) {
  const harvested = {};
  for (const { name, field } of credentialFields()) {
    const match = String(prompt ?? "").match(field.pattern)?.[0];
    if (match) harvested[field.sessionKey || name] = field.normalize ? field.normalize(match) : match;
  }
  return harvested;
}

/**
 * The shape as the planner should see it: freeform expectations plus only
 * those fields safe to put in generated plan text (credentials excluded).
 */
export function plannerShape(intent) {
  const shape = INTENT_SHAPES[intent];
  if (!shape) return {};

  const requirements = {};
  for (const [name, field] of Object.entries(shape.fields)) {
    if (field.credential) continue;
    requirements[name] = field.plannerHint || field.missingAsk;
  }
  return { ...requirements, ...shape.freeform };
}
