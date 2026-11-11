// Serves the static export the way `aldwin web` does (Starlette StaticFiles
// with html=True): a directory answers with its index.html, and a missing
// path answers 404 with 404.html. The Python preview server cannot do the second.
import { createReadStream, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize, resolve, sep } from "node:path";

const root = resolve(process.argv[2] ?? "out");
const port = Number(process.argv[3] ?? 4173);
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".webmanifest": "application/manifest+json",
  ".txt": "text/plain; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
};

function fileFor(pathname) {
  const target = normalize(join(root, decodeURIComponent(pathname)));
  // A bare prefix check would also accept a sibling folder such as `out-old`.
  if (target !== root && !target.startsWith(root + sep)) return null;
  try {
    const stat = statSync(target);
    if (stat.isFile()) return target;
    if (stat.isDirectory()) {
      const index = join(target, "index.html");
      if (statSync(index).isFile()) return index;
    }
  } catch {
    return null;
  }
  return null;
}

createServer((request, response) => {
  const { pathname } = new URL(request.url ?? "/", "http://127.0.0.1");
  const file = fileFor(pathname);
  const status = file ? 200 : 404;
  const body = file ?? join(root, "404.html");
  response.writeHead(status, { "content-type": TYPES[extname(body)] ?? "application/octet-stream" });
  createReadStream(body).pipe(response);
}).listen(port, "127.0.0.1", () => console.log(`serving ${root} on http://127.0.0.1:${port}`));
