/**
 * Gmail send tool: sends real email using a session's connected OAuth token.
 * Never sends anything without a token present for that session — the
 * google.js handler gates on that before this is ever called.
 *
 * Two modes, chosen by whether GMAIL_RELAY_URL is set:
 *
 *   direct (default)  this process talks to the Gmail API itself. Needs
 *                     GOOGLE_CLIENT_SECRET, because refreshing an expired
 *                     access token requires it.
 *
 *   relay             this process POSTs to a separate service that holds
 *                     the secret and does the sending. Used when the app
 *                     runs on a public server but the Google client secret
 *                     should stay on a machine the operator controls.
 *
 * Direct mode is the default so local development and single-box deploys
 * behave exactly as before — the relay is opt-in, not a new requirement.
 */
import { google } from "googleapis";
import { credentialsToClient } from "./googleAuth.js";
import { saveGoogleTokens } from "../googleSession.js";

export class GmailUnavailable extends Error {}

function encodeMessage({ to, subject, body }) {
  const message = [`To: ${to}`, `Subject: ${subject}`, "Content-Type: text/plain; charset=utf-8", "", body].join("\r\n");
  return Buffer.from(message).toString("base64url");
}

/** The raw MIME builder, exported so the relay service encodes identically. */
export { encodeMessage };

/**
 * Hands the send to the relay. Only the session id and the message travel —
 * never the tokens, which the relay reads from the shared store itself, and
 * never the secret, which the relay already has.
 *
 * The shared secret is not optional: an unauthenticated relay reachable on
 * the public internet would let anyone who discovers the URL send mail as
 * any consented user.
 */
async function sendViaRelay(sessionId, { to, subject, body }) {
  const url = process.env.GMAIL_RELAY_URL;
  const secret = process.env.GMAIL_RELAY_SECRET;
  if (!secret) {
    throw new GmailUnavailable("GMAIL_RELAY_URL is set but GMAIL_RELAY_SECRET is missing — refusing to call an unauthenticated relay.");
  }

  let response;
  try {
    response = await fetch(`${url.replace(/\/$/, "")}/relay/send`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${secret}` },
      body: JSON.stringify({ sessionId, to, subject, body }),
      // A closed laptop should fail in seconds, not hang a flow node for
      // the provider's default timeout.
      signal: AbortSignal.timeout(20000),
    });
  } catch (err) {
    throw new GmailUnavailable(
      `Could not reach the Gmail relay at ${url} (${err.message}). If it runs on your own machine, make sure it's running and its tunnel is up.`
    );
  }

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new GmailUnavailable(payload.error || `The Gmail relay returned ${response.status}.`);
  }
  return payload.result || `Email sent to ${to} with subject "${subject}".`;
}

/** Talks to the Gmail API from this process. */
async function sendDirect(sessionId, tokens, { to, subject, body }) {
  const authClient = credentialsToClient(tokens);
  const gmail = google.gmail({ version: "v1", auth: authClient });

  try {
    await gmail.users.messages.send({ userId: "me", requestBody: { raw: encodeMessage({ to, subject, body }) } });
  } catch (err) {
    throw new GmailUnavailable(`Gmail could not send the message: ${err.message}`);
  } finally {
    // The client library may have silently refreshed an expired access
    // token during the call above; persist it so the next send in this
    // session doesn't need to refresh again immediately.
    const refreshed = authClient.credentials;
    if (refreshed?.access_token) {
      await saveGoogleTokens(sessionId, { ...tokens, ...refreshed });
    }
  }

  return `Email sent to ${to} with subject "${subject}".`;
}

export async function sendEmail(sessionId, tokens, { to, subject, body }) {
  if (process.env.GMAIL_RELAY_URL) {
    return await sendViaRelay(sessionId, { to, subject, body });
  }
  return await sendDirect(sessionId, tokens, { to, subject, body });
}
