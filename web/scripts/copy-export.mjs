// Replaces src/aldwin/web/static/ wholesale with the fresh static export, so
// stale hashed files never ship. Never hand-edit files in that folder.
import { cp, rm, stat } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = join(webRoot, "out");
const target = join(webRoot, "..", "src", "aldwin", "web", "static");

await stat(join(source, "index.html"));
await rm(target, { recursive: true, force: true });
await cp(source, target, { recursive: true });
console.log(`Copied static export to ${target}`);
