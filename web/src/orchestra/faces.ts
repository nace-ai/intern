// Faces for the orchestra.
// Toon Head avatars by Johan Melin (CC BY 4.0) via DiceBear
// (https://www.dicebear.com, style "Toon Head").

import { toonHead } from "@dicebear/collection";
import { createAvatar } from "@dicebear/core";

const BACKGROUNDS = [
  "00acc1",
  "039be5",
  "1e88e5",
  "43a047",
  "546e7a",
  "5e35b1",
  "6d4c41",
  "757575",
  "7cb342",
  "8e24aa",
  "c0ca33",
  "d81b60",
  "e53935",
  "f4511e",
  "fb8c00",
  "fdd835",
  "ffb300",
  "00897b",
  "3949ab",
];

const cache = new Map<string, string>();

/** A deterministic toon-head avatar for a seed, as a data:image/svg+xml URL. */
export function faceUrl(seed: string): string {
  const hit = cache.get(seed);
  if (hit) return hit;
  const raw = createAvatar(toonHead, {
    seed,
    size: 96,
    backgroundColor: BACKGROUNDS,
  }).toString();
  // DiceBear may prefix its own data-uri scheme; take the raw svg and encode it.
  const svg = raw.startsWith("data:") ? raw.slice(raw.indexOf(",") + 1) : raw;
  const url = `data:image/svg+xml,${encodeURIComponent(svg)}`;
  cache.set(seed, url);
  return url;
}
