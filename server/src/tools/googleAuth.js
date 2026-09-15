/**
 * Gmail OAuth2 flow: connect once per session, refresh silently after.
 *
 * Least-privilege: send-only. Never request read/modify access to a
 * connected user's mailbox for a feature that only ever sends on their behalf.
 */
import { google } from "googleapis";

export const GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"];

function requireConfig() {
  const { GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI } = process.env;
  if (!GOOGLE_CLIENT_ID || !GOOGLE_CLIENT_SECRET || !GOOGLE_REDIRECT_URI) {
    throw new Error("Gmail integration is not configured on this server (missing GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET/GOOGLE_REDIRECT_URI).");
  }
  return { GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI };
}

function buildOAuthClient() {
  const { GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI } = requireConfig();
  return new google.auth.OAuth2(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI);
}

/**
 * `state` is the caller's session id — Google round-trips it back to the
 * callback unchanged, which is how the resulting token gets associated with
 * the right session with no other state needed.
 */
export function getAuthUrl(state) {
  const client = buildOAuthClient();
  return client.generateAuthUrl({
    access_type: "offline", // required to receive a refresh_token at all
    scope: GMAIL_SCOPES,
    include_granted_scopes: true,
    state,
    prompt: "consent", // forces a refresh_token even on a repeat authorization
  });
}

export async function exchangeCodeForTokens(code) {
  const client = buildOAuthClient();
  const { tokens } = await client.getToken(code);
  return tokens; // { access_token, refresh_token, scope, token_type, expiry_date }
}

/**
 * Returns an OAuth2 client hydrated with the session's stored tokens, ready
 * to pass to the Gmail API — the googleapis client library refreshes an
 * expired access_token transparently using the refresh_token when needed.
 */
export function credentialsToClient(tokens) {
  const client = buildOAuthClient();
  client.setCredentials(tokens);
  return client;
}
