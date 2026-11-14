// The shared lane feed. The conductor adds the segments it just decided; the
// Lanes canvas reads them back every frame. Times are Tone.js context seconds.

import type { MusicianId } from "./musicians";

export type Segment = {
  musician: string; // member id
  instrument?: MusicianId; // the member's instrument (InstrumentId); missing = musician is the instrument id
  kind: "note" | "hold" | "hit" | "accent";
  label: string; // "E5", or "x"/"X" for drums
  start: number; // Tone time, seconds
  dur: number; // seconds
  prob: number; // Drex probability of the played option (0..1)
  isTop: boolean; // played option == Drex's top choice
};

const PRUNE_AFTER = 10; // seconds past a segment's end before it is dropped

const segments: Segment[] = [];
const subscribers = new Set<() => void>();
let maxDur = 0; // largest dur ever added; bounds the back-scan in recent()

function prune(cutoff: number): void {
  let w = 0;
  for (let r = 0; r < segments.length; r++) {
    if (segments[r].start + segments[r].dur >= cutoff) segments[w++] = segments[r];
  }
  segments.length = w;
}

export const timeline = {
  add(segs: Segment[]): void {
    if (segs.length === 0) return;
    for (const seg of segs) {
      if (seg.dur > maxDur) maxDur = seg.dur;
      // binary insert, stable for equal starts
      let lo = 0;
      let hi = segments.length;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (segments[mid].start <= seg.start) lo = mid + 1;
        else hi = mid;
      }
      segments.splice(lo, 0, seg);
    }
    prune(segments[segments.length - 1].start - PRUNE_AFTER);
    for (const fn of subscribers) fn();
  },

  clear(): void {
    segments.length = 0;
    maxDur = 0;
    for (const fn of subscribers) fn();
  },

  subscribe(fn: () => void): () => void {
    subscribers.add(fn);
    return () => {
      subscribers.delete(fn);
    };
  },

  /** Segments still alive at sinceTime or starting after it, in start order. */
  recent(sinceTime: number): Segment[] {
    // first index with start >= sinceTime
    let lo = 0;
    let hi = segments.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (segments[mid].start < sinceTime) lo = mid + 1;
      else hi = mid;
    }
    const out: Segment[] = [];
    // a few long notes may start before sinceTime but still be sounding; they
    // cannot begin earlier than sinceTime - maxDur
    while (lo > 0 && segments[lo - 1].start >= sinceTime - maxDur) {
      if (segments[lo - 1].start + segments[lo - 1].dur >= sinceTime) out.push(segments[lo - 1]);
      lo--;
    }
    out.reverse();
    if (lo < segments.length) out.push(...segments.slice(lo));
    return out;
  },
};
