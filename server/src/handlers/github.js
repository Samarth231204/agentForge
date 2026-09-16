/**
 * The GitHub intent, replacing the earlier agent-loop version entirely.
 * Instead of one generic multi-turn tool-calling agent for every request,
 * this routes deterministically — purely from the query's own text — into
 * one of two fixed, fast workflows:
 *
 *   - "read"   — answer a question about the repo (a named file, or a
 *                general "tell me about/summarize this part" query). No
 *                Docker, no LLM call at all for a plain single-file read;
 *                one LLM call only when the request actually needs
 *                synthesis (summarizing/explaining, not just fetching).
 *   - "change" — an old-Python-style deterministic script: clone, list
 *                files, read the ~5 most relevant (heuristic, no LLM), ONE
 *                LLM call asking for a JSON diff, write + push + PR. Exactly
 *                one LLM round trip regardless of task complexity — this is
 *                what made the original Python version fast, and what the
 *                multi-turn agent loop traded away for open-ended
 *                flexibility it turned out not to need for most requests.
 *
 * This file only ever runs once the TOP-LEVEL classifier (intents.js) has
 * already decided a prompt is "github" — intents.js checks "github" before
 * "research"/"write" specifically so read-shaped phrases sharing vocabulary
 * with research ("explain", "describe", "tell me about") still land here
 * whenever the prompt also names a repo/github context (a URL, "my repo",
 * the word "github", etc.). A prompt with NO such marker at all — e.g. a
 * bare "explain this part" with nothing repo-related in it, relying purely
 * on an already-connected session to supply that context — still can't
 * reach here; that would need the top-level classifier to be session-aware
 * (like the old Python project's has_repo_context bias), which is a
 * separate change from this file's own sub-intent routing.
 */
import { getSessionState, saveSessionState } from "../session.js";
import { completeWithFallback } from "../llmFallback.js";
import { extractFields, describeMissing, isBareCredentialReply } from "../intentShapes.js";
import * as githubTools from "../tools/githubTools.js";
import { GithubUnavailable } from "../tools/githubTools.js";

// ---------------------------------------------------------------------------
// Sub-intent classification — pure text matching, same "forced-keyword"
// pattern as the top-level classifier in intents.js. Read patterns are
// checked FIRST and deliberately cast wide: an accidental false-positive
// into "change" would mean opening an unwanted branch/PR for what was only
// ever meant to be a question, which is a far worse mistake than the
// reverse (a change request wrongly read as a question just gets a
// (unhelpful) answer instead of the edit — annoying, not damaging).
// ---------------------------------------------------------------------------
const READ_PATTERNS = [
  /\bread\b/i,
  /\bshow me\b/i,
  /\bopen\b[\s\S]*\bfile\b/i,
  /\bwhat'?s in\b/i,
  /\bwhat is in\b/i,
  /\bcontents? of\b/i,
  /\btell me about\b/i,
  /\bsummarize\b/i,
  /\bsummary\b/i,
  /\bexplain\b/i,
  /\bdescribe\b/i,
  /\bwhat does\b/i,
  /\bhow does\b[\s\S]*\bwork\b/i,
  /\bwalk me through\b/i,
  /\boverview\b/i,
  /\bunderstand\b/i,
  /\blook at\b/i,
];

function classifyGithubSubIntent(prompt) {
  const text = prompt.toLowerCase();
  if (READ_PATTERNS.some((pattern) => pattern.test(text))) return "read";
  return "change"; // the old-Python-style fallback — anything task/edit-shaped lands here
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

const PRIORITY_EXTENSIONS = new Set([".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".md", ".toml", ".yaml", ".yml", ".txt", ".html", ".css"]);

/** Same heuristic as the old Python project: shallow paths and recognized
 * source/doc/config extensions first — no LLM involved in the ranking itself. */
function pickRelevantFiles(allFiles, limit) {
  return [...allFiles]
    .sort((a, b) => {
      const depthDiff = a.split("/").length - b.split("/").length;
      if (depthDiff !== 0) return depthDiff;
      const extA = a.slice(a.lastIndexOf(".")).toLowerCase();
      const extB = b.slice(b.lastIndexOf(".")).toLowerCase();
      return (PRIORITY_EXTENSIONS.has(extA) ? 0 : 1) - (PRIORITY_EXTENSIONS.has(extB) ? 0 : 1);
    })
    .slice(0, limit);
}

function extractNamedFile(prompt, tree) {
  const match = prompt.match(/\b[\w./-]+\.\w{1,8}\b/);
  if (!match) return null;
  return tree.find((path) => path === match[0] || path.endsWith(`/${match[0]}`)) || null;
}

const WANTS_SYNTHESIS_PATTERN = /\b(explain|summarize|summary|tell me about|describe|what does|overview|understand|how does)\b/i;

// ---------------------------------------------------------------------------
// "read" sub-workflow
// ---------------------------------------------------------------------------

async function handleRead(prompt, repoUrl, token, queryId, onEvent) {
  onEvent?.({ type: "tool_call", tool: "github", action: "fetch_tree", url: repoUrl });
  const tree = await githubTools.fetchRepoTree(repoUrl, token);
  onEvent?.({ type: "tool_result", tool: "github", action: "fetch_tree", summary: `${tree.length} files` });
  const namedFile = extractNamedFile(prompt, tree);
  const wantsSynthesis = WANTS_SYNTHESIS_PATTERN.test(prompt);

  // A bare "read file X" naming exactly one real file, with no explain/
  // summarize verb — just return it directly. No LLM call needed at all.
  if (namedFile && !wantsSynthesis) {
    const content = await githubTools.fetchFileContent(repoUrl, token, namedFile);
    return { intent: "github", content: `\`${namedFile}\`:\n\n${content}` };
  }

  const filesToRead = namedFile ? [namedFile] : pickRelevantFiles(tree, 5);
  const contents = [];
  for (const path of filesToRead) {
    try {
      const raw = await githubTools.fetchFileContent(repoUrl, token, path);
      const capped = raw.length > 4000 ? raw.slice(0, 4000) + "\n...[truncated]" : raw;
      contents.push(`File: ${path}\n${capped}`);
    } catch {
      // Unreadable (binary, etc.) — skip rather than fail the whole request.
    }
  }

  const message = await completeWithFallback({
    queryId,
    onEvent,
    messages: [
      { role: "system", content: "You answer questions about a GitHub repository using only the file contents provided below. Be concise and specific, citing file names where relevant. If the provided files don't actually answer the question, say so plainly." },
      { role: "user", content: `Request: ${prompt}\n\nRepository files:\n${contents.join("\n\n")}` },
    ],
  });
  return { intent: "github", content: message.content ?? "" };
}

// ---------------------------------------------------------------------------
// "change" sub-workflow — deterministic, one LLM call, mirrors the old
// Python project's run_github_workflow exactly.
// ---------------------------------------------------------------------------

function parseJsonPlan(raw) {
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

async function handleChange(prompt, sessionId, repoUrl, token, branch, queryId, onEvent) {
  onEvent?.({ type: "tool_call", tool: "github", action: "clone", url: repoUrl, branch });
  await githubTools.cloneRepo(sessionId, repoUrl, token, branch);

  const allFilesRaw = await githubTools.listFiles(sessionId);
  const allFiles = allFilesRaw.split("\n").filter(Boolean);
  const filesToRead = pickRelevantFiles(allFiles, 5);

  const contents = [];
  for (const path of filesToRead) {
    try {
      contents.push(`File: ${path}\n${await githubTools.readFile(sessionId, path)}`);
    } catch {
      // Unreadable — skip, same as the read workflow.
    }
  }

  const systemPrompt =
    "You are an expert software engineer. Given a repo file listing and some file samples, " +
    'produce ONLY a valid JSON object — no markdown, no extra text — in this exact format:\n' +
    '{"summary":"<one sentence>","files":[{"path":"<relative/path>","content":"<full new content>"}],' +
    '"pr_title":"<title>","pr_body":"<body>"}\n\n' +
    "Rules: use safe relative paths only, provide COMPLETE file content (not diffs), never include credentials.";
  const userMessage = `Request: ${prompt}\n\nFiles in repo:\n${allFiles.slice(0, 50).join("\n")}\n\nSample file contents:\n${contents.join("\n\n")}`;

  const message = await completeWithFallback({
    queryId,
    onEvent,
    messages: [
      { role: "system", content: systemPrompt },
      { role: "user", content: userMessage },
    ],
  });

  const plan = parseJsonPlan(message.content);
  if (!plan || !plan.files || plan.files.length === 0) {
    return { intent: "github", content: `The agent could not determine what changes to make.\n\nModel response:\n${message.content || ""}` };
  }

  const written = [];
  for (const entry of plan.files) {
    if (!entry.path) continue;
    await githubTools.writeFile(sessionId, entry.path, entry.content ?? "");
    written.push(entry.path);
  }
  if (written.length === 0) {
    return { intent: "github", content: "The agent produced a plan but made no file changes." };
  }

  onEvent?.({ type: "tool_call", tool: "github", action: "push", branch });
  const pushResult = await githubTools.pushBranch(sessionId, repoUrl, token, branch);
  if (pushResult.includes("No changes to push")) {
    return { intent: "github", content: `${plan.summary || ""}\n\nNo changes were needed.` };
  }

  onEvent?.({ type: "tool_call", tool: "github", action: "open_pull_request", branch });
  const prResult = await githubTools.createPullRequest(repoUrl, token, branch, plan.pr_title, plan.pr_body);
  onEvent?.({ type: "tool_result", tool: "github", action: "open_pull_request", summary: prResult.message });
  return { intent: "github", content: `${plan.summary || ""}\n\nChanged files: ${written.join(", ")}\n\n${prResult.message}` };
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export async function runGithub(prompt, sessionId, queryId, options = {}) {
  if (!sessionId) {
    return { intent: "github", content: "A session id is required for GitHub tasks." };
  }

  const state = (await getSessionState(sessionId)) || {};

  // Field names, patterns and the missing-field wording all come from the
  // shared declaration in intentShapes.js — the same one the pipeline
  // planner reads, so what's checked here can't drift from what a
  // generated step was told to include.
  const found = extractFields("github", prompt);
  // Only a prompt that is purely a pasted credential may replay a stashed
  // request; a complete new request that happens to name a repo must win.
  const isReplyOnly = isBareCredentialReply("github", prompt, found);

  if (found.token) state.token = found.token;
  if (found.repoUrl) state.repoUrl = found.repoUrl;

  if (!state.token || !state.repoUrl) {
    if (!state.pendingQuery) state.pendingQuery = prompt;
    await saveSessionState(sessionId, state);
    const missingFields = [];
    if (!state.token) missingFields.push("token");
    if (!state.repoUrl) missingFields.push("repoUrl");
    const missing = describeMissing("github", missingFields);
    return {
      intent: "github",
      // Marked failed so a flow re-run retries this node once the
      // credential arrives — an unmet requirement is not a completed node.
      failed: true,
      content: `I need ${missing.join(" and ")} before I can work on this. Reply with ${missing.length > 1 ? "both" : "it"} and I'll continue.`,
    };
  }

  const taskPrompt = isReplyOnly && state.pendingQuery ? state.pendingQuery : prompt;
  state.pendingQuery = null;
  await saveSessionState(sessionId, state);

  const subIntent = classifyGithubSubIntent(taskPrompt);

  try {
    if (subIntent === "read") {
      return await handleRead(taskPrompt, state.repoUrl, state.token, queryId, options.onEvent);
    }
    // "change" — the branch name IS the session id, so repeated change
    // queries in the same session keep committing to the same branch/PR
    // instead of opening a new one each time (cloneRepo's own checkout
    // step is what makes this continuation actually happen).
    const branch = sessionId;
    return await handleChange(taskPrompt, sessionId, state.repoUrl, state.token, branch, queryId, options.onEvent);
  } catch (err) {
    if (err instanceof GithubUnavailable) {
      return { intent: "github", failed: true, content: `GitHub task failed: ${err.message}` };
    }
    throw err;
  }
}
