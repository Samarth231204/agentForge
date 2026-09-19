/**
 * Gmail send tool: sends real email using a session's connected OAuth token.
 * Never sends anything without a token present for that session — the
 * google.js handler gates on that before this is ever called.
 *
 * This process talks to the Gmail API itself, which is why the server needs
 * GOOGLE_CLIENT_SECRET: refreshing an expired access token requires it.
 *
 * There was briefly a second "relay" mode that POSTed to a service on the
 * operator's own machine so the client secret could stay off the public
 * server. It was removed once the server got its own TLS certificate — the
 * relay existed because Google refuses a non-HTTPS redirect URI, and with a
 * real certificate the callback can land here directly. Keeping it would
 * have meant Gmail silently breaking whenever that machine slept.
 */
import { google } from "googleapis";
import { credentialsToClient } from "./googleAuth.js";
import { saveGoogleTokens } from "../googleSession.js";

export class GmailUnavailable extends Error {}

function encodeMessage({ to, subject, body }) {
  const message = [`To: ${to}`, `Subject: ${subject}`, "Content-Type: text/plain; charset=utf-8", "", body].join("\r\n");
  return Buffer.from(message).toString("base64url");
}

export async function sendEmail(sessionId, tokens, { to, subject, body }) {
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
