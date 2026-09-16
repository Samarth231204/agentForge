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
import { extractFields, describeMissing, isBareCredentialReply } from "../intentShapes.js";

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

async function composeEmail(prompt, queryId, onEvent) {
  const message = await completeWithFallback({
    queryId,
    onEvent,
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

/**
 * `presetBody` is the cost optimization for pipelines: when an earlier step
 * already produced the exact text that should be sent (a report written by
 * the `write` intent, say), re-running composeEmail() would pay a whole LLM
 * call to author content that already exists. Given a preset body, this
 * skips that call entirely and only needs a subject, which the pipeline
 * planner supplies as a plain field.
 */
async function handleGmail(prompt, sessionId, recipient, tokens, queryId, presetBody = null, presetSubject = null, onEvent = null) {
  const { subject, body } = presetBody
    ? { subject: presetSubject || "Message from AgentForge", body: presetBody }
    : await composeEmail(prompt, queryId, onEvent);
  try {
    onEvent?.({ type: "tool_call", tool: "gmail", action: "send", recipient, subject });
    const result = await sendEmail(sessionId, tokens, { to: recipient, subject, body });
    onEvent?.({ type: "tool_result", tool: "gmail", action: "send", summary: result });
    return { intent: "google", content: `${result}\n\nSubject: ${subject}\n\n${body}` };
  } catch (err) {
    if (err instanceof GmailUnavailable) {
      // `failed` is passed through by the flow executor so the live view
      // colours this step as failed. Without it a send that never happened
      // renders identically to one that did.
      return { intent: "google", failed: true, content: `Gmail send failed: ${err.message}` };
    }
    throw err;
  }
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

/**
 * `options` is additive and optional — every existing caller passes three
 * arguments and behaves exactly as before. The flow executor uses it to
 * pass `body`/`subject` when an earlier step already wrote the content.
 */
export async function runGoogle(prompt, sessionId, queryId, options = {}) {
  if (!sessionId) {
    return { intent: "google", content: "A session id is required for Google tasks." };
  }

  const subIntent = classifyGoogleSubIntent(prompt);
  if (subIntent !== "gmail") {
    return { intent: "google", content: `The "${subIntent}" Google workflow isn't implemented yet.` };
  }

  const state = (await getSessionState(sessionId)) || {};

  // Recipient pattern and missing-field wording come from the shared
  // declaration in intentShapes.js, which the flow planner also reads.
  const found = extractFields("google", prompt);
  // Only a prompt that is purely the answer to "what's the recipient?" may
  // replay a stashed request; a complete new request must win over it.
  const isReplyOnly = isBareCredentialReply("google", prompt, found);
  if (found.recipient) state.gmailRecipient = found.recipient;

  const tokens = await getGoogleTokens(sessionId);

  if (!tokens || !state.gmailRecipient) {
    if (!state.pendingGmailQuery) state.pendingGmailQuery = prompt;
    await saveSessionState(sessionId, state);

    const parts = [];
    if (!tokens) {
      // Connecting an account is an OAuth redirect, not a field findable in
      // the prompt, so its phrasing stays here rather than in the shape.
      const authUrl = getAuthUrl(sessionId);
      parts.push(`connect your Google account first — click this link, grant access, then come back and send your request again: ${authUrl}`);
    }
    if (!state.gmailRecipient) {
      parts.push(...describeMissing("google", ["recipient"]).map((ask) => `tell me ${ask}`));
    }

    // `failed` matters beyond colouring the UI: the flow executor stores a
    // node's outcome and skips anything that already succeeded on a re-run.
    // Without this flag a blocked node counts as complete, so supplying the
    // missing credential and pressing run again skips the very node that
    // was waiting for it — which defeats resume for the one case it exists
    // to serve. Nothing was sent here, so this is a failure.
    return { intent: "google", failed: true, content: `Before I can send this email, I need you to ${parts.join(" and ")}.` };
  }

  const taskPrompt = isReplyOnly && state.pendingGmailQuery ? state.pendingGmailQuery : prompt;
  const recipient = state.gmailRecipient;
  state.pendingGmailQuery = null;
  await saveSessionState(sessionId, state);

  return await handleGmail(taskPrompt, sessionId, recipient, tokens, queryId, options.body || null, options.subject || null, options.onEvent || null);
}
