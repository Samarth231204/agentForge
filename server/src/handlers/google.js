/**
 * The "google" intent's entry point. Structured to hold multiple Google
 * sub-intents over time (gmail today; calendar/drive/etc. later would each
 * get their own classifyGoogleSubIntent branch and handler function) — only
 * "gmail" (send an email) is implemented so far.
 *
 * Credential model, deliberately split across two different stores:
 *   - The Google OAuth access/refresh tokens (who is allowed to send, on
 *     whose behalf) live in googleSession.js, a dedicated Redis key per
 *     session with the same session-persistent sliding TTL as session.js.
 *   - The recipient address for THIS email (a per-request detail, not a
 *     credential) is asked for and held in session.js's existing generic
 *     per-session blob, the same pendingQuery pattern github.js already
 *     uses for its own missing-PAT/missing-repo flow.
 * A request can be missing either, both, or neither independently.
 */
import { getSessionState, saveSessionState } from "../session.js";
import { getGoogleTokens } from "../googleSession.js";
import { getAuthUrl } from "../tools/googleAuth.js";
import { sendEmail, GmailUnavailable } from "../tools/gmailTool.js";
import { completeWithFallback } from "../llmFallback.js";

const EMAIL_PATTERN = /\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b/i;

function classifyGoogleSubIntent(_prompt) {
  // Only one sub-intent exists today; kept as a function (instead of a bare
  // constant) so adding "calendar"/"drive" later is a one-branch change here,
  // not a restructure.
  return "gmail";
}

// ---------------------------------------------------------------------------
// "gmail" sub-workflow
// ---------------------------------------------------------------------------

function parseEmailPlan(raw) {
  let text = (raw || "").trim().replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "").trim();
  try {
    return JSON.parse(text);
  } catch {
    const match = text.match(/\{[\s\S]*\}/);
    if (!match) return null;
    try {
      return JSON.parse(match[0]);
    } catch {
      return null;
    }
  }
}

const COMPOSE_SYSTEM_PROMPT =
  "You write the subject and body of an email the user wants sent. Return ONLY a valid JSON " +
  'object, no markdown, no extra text, in this exact format: {"subject":"<subject line>","body":"<email body>"}. ' +
  "Match the tone and length implied by the request. The body should be plain text, ready to send as-is.";

async function composeEmail(prompt, queryId) {
  const message = await completeWithFallback({
    queryId,
    messages: [
      { role: "system", content: COMPOSE_SYSTEM_PROMPT },
      { role: "user", content: prompt },
    ],
  });
  const plan = parseEmailPlan(message.content);
  if (plan?.subject && plan?.body) return plan;
  // Parsing failed — fall back to the raw model output as the body rather
  // than blocking the send entirely.
  return { subject: "Message from AgentForge", body: message.content ?? "" };
}

async function handleGmail(prompt, sessionId, recipient, tokens, queryId) {
  const { subject, body } = await composeEmail(prompt, queryId);
  try {
    const result = await sendEmail(sessionId, tokens, { to: recipient, subject, body });
    return { intent: "google", content: `${result}\n\nSubject: ${subject}\n\n${body}` };
  } catch (err) {
    if (err instanceof GmailUnavailable) {
      return { intent: "google", content: `Gmail send failed: ${err.message}` };
    }
    throw err;
  }
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export async function runGoogle(prompt, sessionId, queryId) {
  if (!sessionId) {
    return { intent: "google", content: "A session id is required for Google tasks." };
  }

  const subIntent = classifyGoogleSubIntent(prompt);
  if (subIntent !== "gmail") {
    return { intent: "google", content: `The "${subIntent}" Google workflow isn't implemented yet.` };
  }

  const state = (await getSessionState(sessionId)) || {};

  const foundRecipient = prompt.match(EMAIL_PATTERN)?.[0];
  const suppliedRecipientThisTurn = Boolean(foundRecipient);
  if (foundRecipient) state.gmailRecipient = foundRecipient;

  const tokens = await getGoogleTokens(sessionId);

  const missing = [];
  if (!tokens) missing.push("your Google account connected");
  if (!state.gmailRecipient) missing.push("the recipient's email address");

  if (missing.length > 0) {
    if (!state.pendingGmailQuery) state.pendingGmailQuery = prompt;
    await saveSessionState(sessionId, state);

    const parts = [];
    if (!tokens) {
      const authUrl = getAuthUrl(sessionId);
      parts.push(`connect your Google account first — click this link, grant access, then come back and send your request again: ${authUrl}`);
    }
    if (!state.gmailRecipient) parts.push("tell me the recipient's email address");

    return { intent: "google", content: `Before I can send this email, I need you to ${parts.join(" and ")}.` };
  }

  const taskPrompt = suppliedRecipientThisTurn && state.pendingGmailQuery ? state.pendingGmailQuery : prompt;
  const recipient = state.gmailRecipient;
  state.pendingGmailQuery = null;
  await saveSessionState(sessionId, state);

  return await handleGmail(taskPrompt, sessionId, recipient, tokens, queryId);
}
