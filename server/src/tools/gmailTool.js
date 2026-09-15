/**
 * Gmail send tool: sends real email via the Gmail API using a session's
 * connected OAuth token. Never sends anything without a token present for
 * that session — the google.js handler is what gates on that before this
 * is ever called.
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
