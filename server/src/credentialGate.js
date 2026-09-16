/**
 * Checks, BEFORE anything is planned, whether the session already holds the
 * credentials a request is going to need.
 *
 * Without this the credential problem surfaced in the worst possible place:
 * the flow was planned and displayed, the user approved it, and only then —
 * mid-run, with earlier nodes already executed — did a node report that
 * Gmail was never connected. That wastes a planning call on a request that
 * cannot complete, and it asks for consent at the moment the user is least
 * expecting to be interrupted.
 *
 * Asking up front restores what the single-intent handlers always did: find
 * out what is missing, ask for it plainly, and only start work once it is
 * all there.
 */
import { getSessionState } from "./session.js";
import { getGoogleTokens } from "./googleSession.js";
import { getAuthUrl } from "./tools/googleAuth.js";
import { INTENT_SHAPES, describeMissing, extractFields } from "./intentShapes.js";

/**
 * Credentials that cannot be read out of a prompt at all, because they are
 * an authorization state rather than a value someone can type. Google's
 * OAuth grant is the only one today.
 */
async function googleIsConnected(sessionId) {
  return Boolean(await getGoogleTokens(sessionId));
}

/**
 * @returns {Promise<{satisfied: boolean, asks: string[], intents: string[]}>}
 *   `asks` are ready-to-read phrases; `intents` names which capabilities
 *   were short, for the caller's own messaging.
 */
export async function checkCredentials(intents, sessionId, prompt = "") {
  if (!sessionId) return { satisfied: true, asks: [], intents: [] };

  const state = (await getSessionState(sessionId)) || {};
  const asks = [];
  const short = [];

  for (const intent of intents) {
    const shape = INTENT_SHAPES[intent];
    const intentAsks = [];

    // A requirement is satisfied if it is already in session state OR
    // present in the request itself.
    //
    // The second half matters: only credential-flagged fields get harvested
    // into session, so a repo URL the user typed sits in their prompt and
    // nowhere else. Checking session alone meant asking for a repo URL that
    // was right there in the request — and because that ask never got
    // answered, the flow could never be planned at all.
    if (shape) {
      const inPrompt = extractFields(intent, prompt);
      const missingFields = Object.entries(shape.fields)
        .filter(([name, field]) => !state[field.sessionKey] && !inPrompt[name])
        .map(([name]) => name);
      intentAsks.push(...describeMissing(intent, missingFields));
    }

    // Google's grant is checked separately: it lives in its own store and
    // is obtained by visiting a consent screen, not by typing a value.
    if (intent === "google" && !(await googleIsConnected(sessionId))) {
      let connectAsk = "your Google account connected";
      try {
        connectAsk = `your Google account connected — open this link, grant access, then come back here: ${getAuthUrl(sessionId)}`;
      } catch {
        // Server isn't configured for Google at all; the plain phrase is
        // still the honest thing to say.
      }
      intentAsks.unshift(connectAsk);
    }

    if (intentAsks.length > 0) {
      asks.push(...intentAsks);
      short.push(intent);
    }
  }

  return { satisfied: asks.length === 0, asks, intents: short };
}

/**
 * True when a message is just the user coming back after supplying
 * something — "done", "ok, connected" — rather than a fresh request.
 *
 * Needed because finishing the Google consent screen leaves the user with
 * nothing to type: the credential arrived out of band, via the callback, so
 * their next message is an acknowledgement and the request to actually run
 * is the one they made before being interrupted.
 */
const GO_AHEAD = /^(done|ok(ay)?|go|go ahead|ready|connected|continue|proceed|yes|yep|now|finished|complete[d]?|next)\b[\s.!]*$/i;

export function looksLikeGoAhead(prompt) {
  return GO_AHEAD.test(String(prompt ?? "").trim());
}
