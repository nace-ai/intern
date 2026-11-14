// Tiny PNG reader (8-bit RGB/RGBA, non-interlaced: what Chrome screenshots are) used to prove that a canvas
// really drew something: a blank or single-colour frame is a rendering failure even if no error was thrown.
import { inflateSync } from "node:zlib";

export interface Image { width: number; height: number; data: Uint8Array } // RGBA

export function decodePng(buf: Buffer): Image {
  let off = 8, width = 0, height = 0, bitDepth = 0, colorType = 0, interlace = 0;
  const idat: Buffer[] = [];
  while (off + 8 <= buf.length) {
    const len = buf.readUInt32BE(off), type = buf.toString("ascii", off + 4, off + 8), data = buf.subarray(off + 8, off + 8 + len);
    if (type === "IHDR") { width = data.readUInt32BE(0); height = data.readUInt32BE(4); bitDepth = data[8]!; colorType = data[9]!; interlace = data[12]!; }
    else if (type === "IDAT") idat.push(data);
    else if (type === "IEND") break;
    off += 12 + len;
  }
  if (bitDepth !== 8 || interlace !== 0 || (colorType !== 2 && colorType !== 6)) throw new Error(`unsupported PNG (depth ${bitDepth}, colour type ${colorType}, interlace ${interlace})`);
  const bpp = colorType === 6 ? 4 : 3, stride = width * bpp, raw = inflateSync(Buffer.concat(idat));
  const out = new Uint8Array(width * height * 4);
  let prev = new Uint8Array(stride), cur = new Uint8Array(stride), pos = 0;
  for (let y = 0; y < height; y++) {
    const f = raw[pos++]!;
    for (let x = 0; x < stride; x++) {
      const a = x >= bpp ? cur[x - bpp]! : 0, b = prev[x]!, c = x >= bpp ? prev[x - bpp]! : 0;
      let v = raw[pos++]!;
      if (f === 1) v += a; else if (f === 2) v += b; else if (f === 3) v += (a + b) >> 1;
      else if (f === 4) { const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c); v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c; }
      cur[x] = v & 255;
    }
    for (let x = 0; x < width; x++) {
      const o = (y * width + x) * 4, i = x * bpp;
      out[o] = cur[i]!; out[o + 1] = cur[i + 1]!; out[o + 2] = cur[i + 2]!; out[o + 3] = bpp === 4 ? cur[i + 3]! : 255;
    }
    const t = prev; prev = cur; cur = t;
  }
  return { width, height, data: out };
}

export interface RegionStats { pixels: number; distinctColours: number; meanLuma: number; lumaStdDev: number; brightFraction: number; dominantShare: number }

/** Colour statistics of a rectangle (sampled every `step` pixels). */
export function regionStats(img: Image, r: { x: number; y: number; w: number; h: number }, step = 3): RegionStats {
  const x0 = Math.max(0, Math.floor(r.x)), y0 = Math.max(0, Math.floor(r.y));
  const x1 = Math.min(img.width, Math.floor(r.x + r.w)), y1 = Math.min(img.height, Math.floor(r.y + r.h));
  const seen = new Map<number, number>();
  let n = 0, sum = 0, sum2 = 0, bright = 0;
  for (let y = y0; y < y1; y += step) for (let x = x0; x < x1; x += step) {
    const o = (y * img.width + x) * 4, R = img.data[o]!, G = img.data[o + 1]!, B = img.data[o + 2]!;
    const key = ((R >> 3) << 10) | ((G >> 3) << 5) | (B >> 3);
    seen.set(key, (seen.get(key) ?? 0) + 1);
    const l = 0.2126 * R + 0.7152 * G + 0.0722 * B;
    n++; sum += l; sum2 += l * l; if (l > 140) bright++;
  }
  const mean = n ? sum / n : 0;
  let top = 0;
  for (const v of seen.values()) if (v > top) top = v;
  return { pixels: n, distinctColours: seen.size, meanLuma: Math.round(mean * 10) / 10, lumaStdDev: Math.round(Math.sqrt(Math.max(0, sum2 / Math.max(1, n) - mean * mean)) * 10) / 10, brightFraction: n ? Math.round((bright / n) * 1000) / 1000 : 0, dominantShare: n ? Math.round((top / n) * 1000) / 1000 : 1 };
}

/** Number of pixels within `radius` of (cx, cy) whose colour is within `tol` of `rgb`. */
export function countColourNear(img: Image, cx: number, cy: number, radius: number, rgb: [number, number, number], tol = 28): number {
  let hits = 0;
  for (let y = Math.max(0, Math.floor(cy - radius)); y < Math.min(img.height, Math.ceil(cy + radius)); y++) {
    for (let x = Math.max(0, Math.floor(cx - radius)); x < Math.min(img.width, Math.ceil(cx + radius)); x++) {
      const o = (y * img.width + x) * 4;
      if (Math.abs(img.data[o]! - rgb[0]) <= tol && Math.abs(img.data[o + 1]! - rgb[1]) <= tol && Math.abs(img.data[o + 2]! - rgb[2]) <= tol) hits++;
    }
  }
  return hits;
}
