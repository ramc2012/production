// F10: the built app must work without internet - fail the build if the bundle references
// an external URL (CDN script, web font, analytics). SVG/XML namespace URIs are allowed.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const out = new URL("../../backend/static/", import.meta.url).pathname;
const ALLOWED = [/^http:\/\/www\.w3\.org\//, /^https?:\/\/(localhost|127\.0\.0\.1)/, /^https?:\/\/react\.dev\//, /^https?:\/\/reactjs\.org\//];
const files = [];
const walk = (d) => readdirSync(d).forEach((f) => (statSync(join(d, f)).isDirectory() ? walk(join(d, f)) : files.push(join(d, f))));
walk(out);
const bad = [];
for (const f of files) {
  if (!/\.(js|css|html)$/.test(f)) continue;
  const txt = readFileSync(f, "utf8");
  // only URLs that are loaded: src/href attributes, url(), import(), fetch-like string literals
  for (const m of txt.matchAll(/(?:src=|href=|url\(|import\(|from\s*)["']?(https?:\/\/[^"')\s]+)/g)) {
    if (!ALLOWED.some((r) => r.test(m[1]))) bad.push(`${f.replace(out, "")}: ${m[1]}`);
  }
}
if (bad.length) {
  console.error("External URLs found in the bundle (F10 offline requirement):\n" + bad.join("\n"));
  process.exit(1);
}
console.log(`offline check: ${files.length} files, no external resource URLs`);
