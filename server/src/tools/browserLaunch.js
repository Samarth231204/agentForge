/**
 * How this project launches Chromium, in one place.
 *
 * Both launch sites (browseAgentLoop for a whole query, browserTool when it
 * owns its own browser) used a bare `{ headless: true }`, which is fine on a
 * developer machine and unreliable on a small server.
 *
 * `--disable-dev-shm-usage` is the important one. Chromium puts shared
 * memory in /dev/shm, which defaults to 64 MB on many Linux hosts — far less
 * than a real page needs — and when it runs out Chromium dies mid-navigation
 * with an error that looks like a site problem rather than a memory one.
 * The flag moves that allocation to /tmp instead.
 *
 * Deliberately NOT included: `--no-sandbox`. It is the usual companion to
 * the flag above in container guides, but it exists to work around running
 * as root, and this app runs as an ordinary user. Turning off the sandbox
 * would mean pages we navigate to — arbitrary sites chosen by a model —
 * lose a real isolation boundary, which is not a trade worth making to save
 * a few megabytes.
 */
import { chromium } from "playwright";

const LAUNCH_ARGS = [
  // See above: the single most common cause of Chromium crashing on a small
  // instance, because /dev/shm is typically 64 MB.
  "--disable-dev-shm-usage",
  // Nothing here renders to a screen, so the GPU stack is pure overhead.
  "--disable-gpu",
  // Each extra renderer process is memory this box may not have.
  "--disable-extensions",
  "--disable-background-networking",
];

export function launchBrowser() {
  return chromium.launch({ headless: true, args: LAUNCH_ARGS });
}
