/**
 * The Gmail relay — runs on a machine the operator controls (a laptop),
 * exposed through a tunnel, and is the ONLY place the Google client secret
 * exists.
 *
 * It owns the two operations that need that secret:
 *
 *   GET  /auth/google/callback   exchange the consent code for tokens
 *   POST /relay/send             send a message, refreshing the access
 *                                token if it has expired
 *
 * Everything else — planning, the flow graph, browse, github — stays on the
 * public server, which only ever holds the client ID and the redirect URI.
 *
 * Tokens travel through the shared Redis both sides can reach, never over
 * this API: the server sends a session id and a message, and the relay looks
 * the tokens up itself. That keeps refresh tokens off the wire.
 *
 * Run it with its own env (see relay/.env.example):
 *   node relay/gmailRelay.js
 */
// MUST be the first import: it loads this directory's .env before any
// module that reads process.env at import time is evaluated.
import "./loadEnv.js";
import express from "express";
import { google } from "googleapis";

import { exchangeCodeForTokens } from "../src/tools/googleAuth.js";
import { getGoogleTokens, saveGoogleTokens } from "../src/googleSession.js";
import { credentialsToClient } from "../src/tools/googleAuth.js";
import { encodeMessage } from "../src/tools/gmailTool.js";

const app = express();
app.use(express.json());

const PORT = process.env.RELAY_PORT || 4100;
const SECRET = process.env.GMAIL_RELAY_SECRET;

if (!SECRET) {
  console.error("GMAIL_RELAY_SECRET is not set. Refusing to start: this service can send mail as any consented user, so it must never be reachable unauthenticated.");
  process.exit(1);
}
if (!process.env.GOOGLE_CLIENT_SECRET) {
  console.error("GOOGLE_CLIENT_SECRET is not set. The relay exists specifically to hold it — nothing here works without it.");
  process.exit(1);
}

/**
 * Constant-time-ish comparison. Not defending against a serious timing
 * attack over a tunnel, but there's no reason to leak length either.
 */
function secretMatches(header) {
  const provided = String(header || "").replace(/^Bearer\s+/i, "");
  if (provided.length !== SECRET.length) return false;
  let mismatch = 0;
  for (let i = 0; i < provided.length; i++) mismatch |= provided.charCodeAt(i) ^ SECRET.charCodeAt(i);
  return mismatch === 0;
}

function requireSecret(req, res, next) {
  if (!secretMatches(req.headers.authorization)) {
    return res.status(401).json({ error: "Unauthorized." });
  }
  next();
}

app.get("/health", (req, res) => res.json({ status: "ok", role: "gmail-relay" }));

/**
 * The OAuth callback. Deliberately NOT behind the shared secret: the browser
 * arrives here from Google and has no way to present it. The protection is
 * that a code is single-use, short-lived, and only exchangeable with this
 * client secret.
 */
app.get("/auth/google/callback", async (req, res) => {
  const { code, state } = req.query;
  if (!code || !state) return res.status(400).send("<h3>Missing code or state.</h3>");
  try {
    const tokens = await exchangeCodeForTokens(code);
    await saveGoogleTokens(state, tokens);
    res.send("<h3>Google account connected. You can close this tab and return to AgentForge.</h3>");
  } catch (err) {
    console.error("[relay] OAuth callback failed:", err.message);
    res.status(400).send("<h3>Could not connect your Google account. Please close this tab and try again.</h3>");
  }
});

app.post("/relay/send", requireSecret, async (req, res) => {
  const { sessionId, to, subject, body } = req.body || {};
  if (!sessionId || !to) return res.status(422).json({ error: "sessionId and to are required." });

  const tokens = await getGoogleTokens(sessionId);
  if (!tokens) {
    return res.status(409).json({ error: "That session has no connected Google account." });
  }

  const authClient = credentialsToClient(tokens);
  const gmail = google.gmail({ version: "v1", auth: authClient });

  try {
    await gmail.users.messages.send({ userId: "me", requestBody: { raw: encodeMessage({ to, subject, body }) } });
    console.log(`[relay] sent to ${to} for session ${String(sessionId).slice(0, 8)}…`);
    return res.json({ result: `Email sent to ${to} with subject "${subject}".` });
  } catch (err) {
    console.error("[relay] send failed:", err.message);
    return res.status(502).json({ error: `Gmail could not send the message: ${err.message}` });
  } finally {
    // Refresh happens here now, so the refreshed token has to go back to the
    // shared store or the next send pays for another refresh.
    const refreshed = authClient.credentials;
    if (refreshed?.access_token) {
      await saveGoogleTokens(sessionId, { ...tokens, ...refreshed }).catch(() => {});
    }
  }
});

app.listen(PORT, () => {
  console.log(`[relay] Gmail relay listening on http://localhost:${PORT}`);
  console.log(`[relay] callback path: /auth/google/callback`);
  console.log(`[relay] expose this through your tunnel and set GOOGLE_REDIRECT_URI to <tunnel>/auth/google/callback on BOTH this machine and the server`);
});
