// Draws every icon from the Aldwin mark's geometry, so the set never drifts:
//   src/app/icon.svg          follows the viewer's light or dark scheme
//   src/app/favicon.ico       32 and 16 px
//   src/app/apple-icon.png    180 px
//   public/icon-192.png, public/icon-512.png, public/icon-512-maskable.png
// Rasters cannot follow the scheme, so they are the white mark on a black tile.
// Keep the geometry in step with src/constants/brand-mark.ts.
//
//   node scripts/generate-icons.mjs
import { writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import sharp from "sharp";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const BLOB =
  "M128,36 C206,34 230,58 231,130 C232,204 210,226 128,226 C46,226 24,206 25,132 C26,60 50,38 128,36 Z";
const EYES = [
  [84, 88],
  [148, 88],
];
const EYE = { width: 24, height: 48 };

// The eyes are holes in the silhouette (even-odd), so they show whatever is behind.
const MARK_PATH =
  BLOB +
  EYES.map(([x, y]) => ` M${x},${y} h${EYE.width} v${EYE.height} h-${EYE.width} Z`).join("");

const SCHEME_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img"><title>Aldwin</title><style>path{fill:#0e1716}@media (prefers-color-scheme:dark){path{fill:#ffffff}}</style><path fill-rule="evenodd" d="${MARK_PATH}"/></svg>
`;

/** The white mark on a black tile; `inset` is the share of the tile the mark spans. */
function tileSvg(size, inset, radius) {
  const scale = (size * inset) / 256;
  const offset = (size - 256 * scale) / 2;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
  <rect width="${size}" height="${size}" rx="${radius}" fill="#000000"/>
  <g transform="translate(${offset} ${offset}) scale(${scale})"><path fill="#ffffff" fill-rule="evenodd" d="${MARK_PATH}"/></g>
</svg>`;
}

async function png(size, inset, radius) {
  return sharp(Buffer.from(tileSvg(size, inset, radius)), { density: 384 })
    .resize(size, size)
    .png()
    .toBuffer();
}

/** An ICO file holding PNG images, which every current browser reads. */
function ico(images) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(images.length, 4);
  const entries = [];
  let offset = 6 + 16 * images.length;
  for (const { size, data } of images) {
    const entry = Buffer.alloc(16);
    entry.writeUInt8(size >= 256 ? 0 : size, 0);
    entry.writeUInt8(size >= 256 ? 0 : size, 1);
    entry.writeUInt16LE(1, 4);
    entry.writeUInt16LE(32, 6);
    entry.writeUInt32LE(data.length, 8);
    entry.writeUInt32LE(offset, 12);
    entries.push(entry);
    offset += data.length;
  }
  return Buffer.concat([header, ...entries, ...images.map((image) => image.data)]);
}

const outputs = {
  "src/app/icon.svg": Buffer.from(SCHEME_SVG),
  "src/app/favicon.ico": ico([
    { size: 32, data: await png(32, 0.82, 7) },
    { size: 16, data: await png(16, 0.86, 3) },
  ]),
  "src/app/apple-icon.png": await png(180, 0.62, 0),
  "public/icon-192.png": await png(192, 0.66, 42),
  "public/icon-512.png": await png(512, 0.66, 112),
  // Maskable icons are cropped to a circle, so the mark stays inside the 80% safe zone.
  "public/icon-512-maskable.png": await png(512, 0.56, 0),
};

for (const [path, data] of Object.entries(outputs)) {
  await writeFile(join(ROOT, path), data);
  console.log(`wrote ${path}`);
}
