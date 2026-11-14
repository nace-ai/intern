import QRCode from "qrcode";
import jsQR from "jsqr";
import { PNG } from "pngjs";
import { mkdirSync, readFileSync } from "node:fs";
import assert from "node:assert/strict";
const url = process.argv[2];
if (!url || !/^https:\/\/[a-z0-9.-]+\.workers\.dev\/?$/.test(url))
  throw Error("Supply the actual verified HTTPS workers.dev URL.");
const page = await fetch(url);
assert.equal(
  page.status,
  200,
  "Public website must be reachable before QR generation.",
);
assert.match(await page.text(), /AGENT HEIST/);
mkdirSync("public", { recursive: true });
const output = "public/agent-heist-qr.png";
await QRCode.toFile(output, url, {
  type: "png",
  width: 2048,
  margin: 4,
  errorCorrectionLevel: "H",
  color: { dark: "#000000", light: "#ffffff" },
});
const png = PNG.sync.read(readFileSync(output));
const scanned = jsQR(new Uint8ClampedArray(png.data), png.width, png.height);
assert.equal(
  scanned?.data,
  url,
  "QR image must decode to the exact verified public URL.",
);
assert.equal(
  (await fetch(scanned.data)).status,
  200,
  "Decoded QR URL must open the real website.",
);
console.log(
  `PASS: ${output} (${png.width}x${png.height}); decoded QR opens ${url}`,
);
