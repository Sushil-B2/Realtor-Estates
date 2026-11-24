/**
 * Syntax-check every browser module + script with the real parser.
 * `npm run build` uses this instead of a bundler: the PWA ships unminified
 * ES2022 classic scripts, so there is nothing to compile — only to verify.
 */
import { readdirSync, statSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

const roots = ["web/js", "scripts"];
let checked = 0;
for (const root of roots) {
  let entries = [];
  try {
    entries = readdirSync(root);
  } catch {
    continue;
  }
  for (const name of entries) {
    const full = join(root, name);
    if (!statSync(full).isFile()) continue;
    if (!/\.(m?js)$/.test(name)) continue;
    if (name === "check-js.mjs") continue;
    try {
      execFileSync(process.execPath, ["--check", full], { stdio: "pipe" });
      checked++;
    } catch (err) {
      console.error(`FAIL ${full}\n${err.stderr?.toString() || err.message}`);
      process.exit(1);
    }
  }
}
console.log(`check-js: ${checked} file(s) parsed cleanly`);
