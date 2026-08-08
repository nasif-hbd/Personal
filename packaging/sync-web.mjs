#!/usr/bin/env node
/**
 * Copy the web app into every packaging target.
 *
 * The repo root is the single source of truth. Nothing here is hand-maintained
 * — run this before any store build so Android, iOS, Windows, macOS and the
 * website can never ship different versions of the same app.
 *
 *   node packaging/sync-web.mjs
 *   node packaging/sync-web.mjs --check    # exit 1 if a target is stale (CI)
 */
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");

// sw.js is included everywhere: the app only registers it over http(s), so the
// file:// desktop build ignores it while the localhost-served mobile shell
// still gets offline caching. One file list, no per-platform special cases.
const ASSETS = [
  "index.html",
  "manifest.json",
  "courses.json",
  "sw.js",
  "icon.svg",
  "icon-192.png",
  "icon-512.png",
  "icon-512-maskable.png",
];

const TARGETS = [
  join(HERE, "mobile", "www"),
  join(HERE, "desktop", "app"),
];

const check = process.argv.includes("--check");
let stale = 0;

const digest = (path) =>
  existsSync(path) ? createHash("sha256").update(readFileSync(path)).digest("hex") : null;

for (const target of TARGETS) {
  if (!check) {
    // Wipe first so a file deleted from the repo can't survive in a bundle.
    rmSync(target, { recursive: true, force: true });
    mkdirSync(target, { recursive: true });
  }
  for (const asset of ASSETS) {
    const from = join(ROOT, asset);
    const to = join(target, asset);
    if (!existsSync(from)) {
      console.error(`missing source asset: ${asset}`);
      process.exit(1);
    }
    if (check) {
      if (digest(from) !== digest(to)) {
        console.error(`stale: ${to.replace(ROOT + "/", "")}`);
        stale++;
      }
      continue;
    }
    copyFileSync(from, to);
  }
}

if (check) {
  if (stale) {
    console.error(`\n${stale} file(s) out of date — run: node packaging/sync-web.mjs`);
    process.exit(1);
  }
  console.log("packaging targets are up to date");
} else {
  console.log(`synced ${ASSETS.length} files into ${TARGETS.length} targets`);
}
