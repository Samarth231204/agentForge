/**
 * The one tool the browse intent's agent loop gets: a headless-browser
 * controller that can both search the web and interact with whatever page
 * it's on — search and browse used to be two separate tools/modules
 * (web_search + browser_action), each with its own Playwright page. That
 * meant a query needing "search, then read the top result" paid for two
 * separate page loads on two separate pages, and cost the model two
 * separate tool-call turns to reason about, for what's really one
 * continuous session. Merged into one class, one tool, one persistent page
 * — searching and then navigating to a found result now happen on the
 * exact same page object, not just the same browser.
 *
 * One Chromium page persists for the tool's whole lifetime (one query) — a
 * fresh page per call would lose all navigation/search state between
 * actions, making a multi-step flow like search -> navigate -> click
 * impossible.
 *
 * Unlike the old Python project's version, there is no thread-affinity
 * workaround here at all: Playwright's Python bindings are a *sync* API
 * that requires every call to happen on the exact same OS thread that
 * started it (hence its ThreadPoolExecutor(max_workers=1)). Playwright's
 * JS API is natively async/await, and Node's single-threaded event loop
 * has no "wrong thread" to call from in the first place — that whole class
 * of complexity just doesn't exist here.
 */
import { chromium } from "playwright";

export class BrowserUnavailable extends Error {}

// Named "browser_action", not bare "browser" — confirmed via a real failure
// that gpt-oss models sometimes reach for an internal "browser.open"-style
// tool convention they were trained on (OpenAI's own hosted browsing tool
// naming) when a tool is just called "browser", producing a hallucinated
// call with the wrong shape entirely (cursor/id args instead of this
// schema). A more distinctive name reduces that collision.
export const BROWSER_TOOL_NAME = "browser_action";
export const BROWSER_TOOL_DESCRIPTION =
  "Control a real headless web browser. Use 'search' to find candidate pages for an open-ended " +
  "question — it returns a titled list of results (title, URL, snippet) from the public web. Use " +
  "'navigate' to open a specific URL directly, whether from a search result or a site named/implied " +
  "by the task. Call navigate before click/fill/get_text/get_links. Use get_text to read the page's " +
  "actual content, and get_links to extract the real href URL of anchor elements — get_text alone " +
  "can never give you a link's actual URL, only its visible text, so use get_links whenever a task " +
  "asks for a link/URL rather than guessing or giving up. That is how you verify what happened, not " +
  "by assuming. Never enter payment card details, passwords, or other account credentials into any " +
  "field — stop and report if a task requires them. EVERY call must include every field below; set " +
  "any field not needed by that action to an empty string (or 0 for max_results).";

export const BROWSER_TOOL_SCHEMA = {
  type: "object",
  properties: {
    action: { type: "string", enum: ["search", "navigate", "click", "fill", "get_text", "get_links"], description: "The operation to perform." },
    query: { type: "string", description: "Search query, for the search action, or an empty string." },
    max_results: { type: "integer", description: "How many results to return (1-5) for the search action, or 0.", minimum: 0, maximum: 5 },
    url: { type: "string", description: "URL for the navigate action, or an empty string." },
    selector: { type: "string", description: "CSS selector for click/fill/get_text/get_links (get_links defaults to 'a' — every link — if left empty), or an empty string." },
    text: { type: "string", description: "Text to type for the fill action, or an empty string." },
  },
  required: ["action", "query", "max_results", "url", "selector", "text"],
};

/** Bing wraps every result href in a `bing.com/ck/a?...&u=a1<base64url(realUrl)>...`
 * tracking redirect rather than linking directly — this recovers the real
 * destination so a later navigate call never has to bounce through Bing's
 * own redirector. */
function resolveBingHref(href) {
  try {
    const u = new URL(href).searchParams.get("u");
    if (u && u.startsWith("a1")) {
      return Buffer.from(u.slice(2), "base64").toString("utf-8");
    }
  } catch {
    // Not a wrapped Bing redirect — fall through and use the href as-is.
  }
  return href;
}

function extractSearchResults(els) {
  return els.map((el) => {
    const link = el.querySelector("h2 a");
    const snippetEl = el.querySelector(".b_caption p, .b_lineclamp2, p");
    return { title: link?.innerText || "", href: link?.href || "", snippet: snippetEl?.innerText || "" };
  });
}

/** A one-line gist of a tool result, for the live feed. */
function summarize(result) {
  const text = String(result ?? "").replace(/\s+/g, " ").trim();
  return text.length > 140 ? `${text.slice(0, 140)}…` : text;
}

export class BrowserTool {
  #browser = null;
  #page = null;
  #ownsBrowser = true;

  #onEvent = null;

  /**
   * @param {import("playwright").Browser} [browser] - an already-launched
   *   browser to use instead of launching a new one. When given, this
   *   instance never closes it — whoever launched it owns closing it.
   * @param {function} [onEvent] - optional live-progress sink. Reports each
   *   action and the URL it touched, so the running flow can be watched.
   *   Purely additive: it never changes what the browser does.
   */
  constructor(browser = null, onEvent = null) {
    if (browser) {
      this.#browser = browser;
      this.#ownsBrowser = false;
    }
    this.#onEvent = onEvent;
  }

  async run(action = "", query = "", maxResults = 0, url = "", selector = "", text = "") {
    // Reported before the work rather than after, because the interesting
    // part of a slow browser call is knowing what it is currently doing.
    this.#onEvent?.({ type: "tool_call", tool: "browser", action, ...(query ? { query } : {}), ...(url ? { url } : {}), ...(selector ? { selector } : {}) });
    try {
      const result = await this.#execute(action, query, maxResults, url, selector, text);
      this.#onEvent?.({ type: "tool_result", tool: "browser", action, summary: summarize(result) });
      return result;
    } catch (err) {
      this.#onEvent?.({ type: "tool_result", tool: "browser", action, failed: true, summary: err.message });
      if (err instanceof BrowserUnavailable) throw err;
      throw new BrowserUnavailable(`The browser could not complete '${action}': ${err.message}`);
    }
  }

  async #ensurePage() {
    if (!this.#page) {
      if (!this.#browser) {
        this.#onEvent?.({ type: "tool_call", tool: "browser", action: "launch" });
        this.#browser = await chromium.launch({ headless: true });
      }
      this.#page = await this.#browser.newPage();
    }
    return this.#page;
  }

  async #execute(action, query, maxResults, url, selector, text) {
    const page = await this.#ensurePage();

    if (action === "search") {
      return this.#search(page, query, maxResults);
    }

    if (action === "navigate") {
      const target = safeUrl(url);
      // "domcontentloaded" fires before JS-heavy single-page sites (e.g.
      // YouTube) have actually rendered anything — an immediate get_text
      // right after navigate would see an empty page. "load" plus a short
      // fixed settle time is a pragmatic middle ground: "networkidle" would
      // be more thorough but some sites (YouTube included) never truly go
      // network-idle due to persistent analytics/ad connections, and would
      // just time out instead of helping.
      await page.goto(target, { waitUntil: "load", timeout: 30000 });
      await page.waitForTimeout(1500);
      const title = await page.title();
      const bodyText = title ? "" : (await page.innerText("body").catch(() => "")).trim();
      if (!title && !bodyText) {
        // Still nothing after settling — say so explicitly rather than
        // silently returning as if navigation fully succeeded, since an
        // agent handed an empty result here has been observed filling the
        // gap with plausible-looking facts from its own training data
        // instead of retrying.
        return `Navigated to ${target}, but the page appears empty so far (no title, no text). Call get_text again before trusting any content from this page — do not answer from general knowledge.`;
      }
      return `Navigated to ${target}. Title: ${title}`;
    }

    if (action === "click") {
      await page.click(safeSelector(selector), { timeout: 4000 });
      return `Clicked: ${selector}`;
    }

    if (action === "fill") {
      await page.fill(safeSelector(selector), text, { timeout: 4000 });
      return `Filled '${selector}'.`;
    }

    if (action === "get_text") {
      // A short timeout: a selector that never appears is far more likely
      // than a slow page, and a wrong guess should fail fast so the agent
      // can try a different selector within its iteration budget instead
      // of burning it on long waits.
      const content = await page.innerText(selector || "body", { timeout: 4000 });
      const collapsed = content.split(/\s+/).filter(Boolean).join(" ");
      return collapsed.length > 3000 ? collapsed.slice(0, 3000) + "...[truncated]" : collapsed;
    }

    if (action === "get_links") {
      // get_text can only ever return visible text, never an attribute
      // like href — this is the only way to get a link's actual
      // destination URL rather than just its label.
      const links = await page.$$eval(
        selector || "a",
        (els) => els.slice(0, 20).map((el) => ({ text: (el.innerText || "").trim().slice(0, 100), href: el.href })),
      );
      if (links.length === 0) return `No links matched selector '${selector || "a"}'.`;
      return links.map((link, i) => `${i + 1}. ${link.text || "(no text)"} -> ${link.href}`).join("\n");
    }

    throw new Error(`Unknown browser action: ${action}`);
  }

  async #search(page, query, maxResults) {
    const cleanedQuery = String(query || "").trim();
    if (!cleanedQuery) throw new Error("Search query cannot be blank.");
    const limit = Math.max(1, Math.min(Number(maxResults) || 5, 5));

    await page.goto(`https://www.bing.com/search?q=${encodeURIComponent(cleanedQuery)}`, { waitUntil: "load", timeout: 15000 });
    const raw = await this.#readSearchResults(page);

    const seen = new Set();
    const results = [];
    for (const item of raw) {
      if (!item.href) continue;
      const resolvedUrl = resolveBingHref(item.href);
      if (!/^https?:\/\//i.test(resolvedUrl) || seen.has(resolvedUrl)) continue;
      seen.add(resolvedUrl);
      results.push({ title: item.title.trim() || "Untitled result", url: resolvedUrl, snippet: item.snippet.trim().replace(/\s+/g, " ") });
      if (results.length === limit) break;
    }

    if (results.length === 0) return "No results found for that query.";
    return results.map((r, i) => `${i + 1}. ${r.title}\n   ${r.url}\n   ${r.snippet}`).join("\n\n");
  }

  /**
   * waitForSelector alone (container existence) turned out NOT to be a
   * strong enough signal — confirmed in testing: li.b_algo elements can
   * exist in the DOM with their h2/a title text still empty, rendered a
   * moment later. waitForFunction waits for that actual text content to be
   * non-empty, which is what genuinely fixed the "Untitled result" /
   * empty-snippet flakiness (~1 in 3 runs before this).
   *
   * A single waitForFunction check turned out not to be trustworthy on its
   * own either — confirmed directly in testing: the check can report the
   * title text as genuinely non-empty at that instant, then reading it
   * again immediately after comes back empty (the page re-renders/flickers
   * its result content after the check passes). Validating the actual
   * extracted results — not just one point-in-time DOM check — and
   * retrying with a short backoff if every title came back blank is what
   * actually made this reliable.
   */
  async #readSearchResults(page) {
    for (let attempt = 0; attempt < 3; attempt++) {
      const results = await this.#readSearchResultsOnce(page);
      const allBlank = results.length > 0 && results.every((r) => !r.title.trim());
      if (!allBlank) return results;
      await page.waitForTimeout(700);
    }
    return this.#readSearchResultsOnce(page);
  }

  async #readSearchResultsOnce(page) {
    await page
      .waitForFunction(
        () => {
          const first = document.querySelector("li.b_algo h2 a");
          return Boolean(first && first.innerText.trim().length > 0);
        },
        { timeout: 8000 },
      )
      .catch(() => {});
    // Bing occasionally does a secondary client-side navigation/reload
    // right after the initial load, which destroys the JS execution
    // context mid-evaluation ("Execution context was destroyed") —
    // confirmed happening live. One retry is enough.
    return page.$$eval("li.b_algo", extractSearchResults).catch(async (err) => {
      if (!String(err.message).includes("Execution context was destroyed")) throw err;
      return page.$$eval("li.b_algo", extractSearchResults);
    });
  }

  /** Safe to call even if no page was ever opened, and safe to call more than once. */
  async close() {
    if (!this.#browser) return;
    const browser = this.#browser;
    const ownsBrowser = this.#ownsBrowser;
    this.#browser = null;
    this.#page = null;
    // If this browser was handed in from outside, only its actual owner
    // closes it.
    if (!ownsBrowser) return;
    return browser.close().then(
      () => {},
      (err) => console.error("[browserTool] error closing browser:", err.message),
    );
  }
}

function safeUrl(url) {
  const trimmed = String(url || "").trim();
  if (!/^https?:\/\//i.test(trimmed)) {
    throw new Error("Only http:// and https:// URLs may be navigated to.");
  }
  return trimmed;
}

function safeSelector(selector) {
  const trimmed = String(selector || "").trim();
  if (!trimmed) throw new Error("A CSS selector is required for this action.");
  return trimmed;
}
