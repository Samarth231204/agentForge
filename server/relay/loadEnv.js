/**
 * Loads the relay's OWN .env, resolved relative to this file rather than to
 * the current working directory.
 *
 * `import "dotenv/config"` resolves against cwd, so starting the relay from
 * the server directory (`node relay/gmailRelay.js`, the obvious way to run
 * it) silently loaded server/.env instead — and the relay then refused to
 * start because it couldn't see its own GMAIL_RELAY_SECRET.
 *
 * Imported FIRST by gmailRelay.js on purpose: ES module imports are
 * evaluated in source order before any module body runs, and at least one
 * module down the import graph reads process.env at import time
 * (googleSession's TTL), so the values have to be present by then.
 */
import dotenv from "dotenv";

dotenv.config({ path: new URL("./.env", import.meta.url) });
