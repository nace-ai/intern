// Dev only: a fake Drex so the lanes can be seen before the conductor exists.

import * as Tone from "tone";
import { MUSICIANS, type MusicianId } from "@/orchestra/musicians";
import { type Segment, timeline } from "@/orchestra/timeline";

const NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const PENT = new Set(["A", "C", "D", "E", "G"]);

const midiName = (midi: number): string => `${NAMES[midi % 12]}${Math.floor(midi / 12) - 1}`;

function stepwise(scale: number[], from: number): number {
  const opts = scale.filter((m) => Math.abs(m - from) <= 2);
  const pool = opts.length > 0 ? opts : scale;
  return pool[Math.floor(Math.random() * pool.length)];
}

export function startFakeFeed(bpm = 96, members?: { id: string; instrument: MusicianId }[]): () => void {
  const beatSec = 60 / bpm;
  // default: the classic band, one member per instrument, member id = instrument id
  const roster = members ?? MUSICIANS.map((m) => ({ id: m.id, instrument: m.id }));
  const byInstrument = new Map(MUSICIANS.map((m) => [m.id, m]));
  const scales = new Map<string, number[]>(); // per instrument
  for (const m of MUSICIANS) {
    if (m.kind !== "melodic") continue;
    const notes: number[] = [];
    for (let midi = m.low; midi <= m.high; midi++) if (PENT.has(NAMES[midi % 12])) notes.push(midi);
    scales.set(m.id, notes);
  }
  const sounding = new Map<string, number>(); // per member id
  const prob = () => 0.3 + 0.65 * Math.random();
  const isTop = () => Math.random() < 0.8;
  let beat = 0;

  const tick = () => {
    const start = Tone.immediate() + 2 * beatSec;
    const segs: Segment[] = [];
    for (const member of roster) {
      const m = byInstrument.get(member.instrument);
      if (!m) continue;
      if (m.kind === "drums") {
        const pos = beat % 4;
        if ((m.id === "kick" && (pos === 0 || pos === 2)) || (m.id === "snare" && (pos === 1 || pos === 3))) {
          segs.push({
            musician: member.id,
            instrument: m.id,
            kind: "hit",
            label: "x",
            start,
            dur: 0.12,
            prob: prob(),
            isTop: isTop(),
          });
        }
        if (m.id === "hats") {
          for (const off of [0, beatSec / 2]) {
            const accent = Math.random() < 0.15;
            segs.push({
              musician: member.id,
              instrument: m.id,
              kind: accent ? "accent" : "hit",
              label: accent ? "X" : "x",
              start: start + off,
              dur: 0.12,
              prob: prob(),
              isTop: isTop(),
            });
          }
        }
        continue;
      }
      const scale = scales.get(m.id) ?? [];
      const cur = sounding.get(member.id);
      const isPad = m.id === "pad_low" || m.id === "pad_high";
      if (cur !== undefined && Math.random() < (isPad ? 0.85 : 0.3)) {
        segs.push({
          musician: member.id,
          instrument: m.id,
          kind: "hold",
          label: midiName(cur),
          start,
          dur: beatSec,
          prob: prob(),
          isTop: isTop(),
        });
        continue;
      }
      const restChance = m.id === "bells" ? 0.85 : m.id === "flute" ? 0.3 : 0.12;
      if (Math.random() < restChance) {
        sounding.delete(member.id);
        continue;
      }
      const midi = cur !== undefined ? stepwise(scale, cur) : scale[Math.floor(Math.random() * scale.length)];
      sounding.set(member.id, midi);
      segs.push({
        musician: member.id,
        instrument: m.id,
        kind: "note",
        label: midiName(midi),
        start,
        dur: beatSec,
        prob: prob(),
        isTop: isTop(),
      });
    }
    timeline.add(segs);
    beat++;
  };

  tick();
  const id = setInterval(tick, beatSec * 1000);
  return () => {
    clearInterval(id);
    timeline.clear();
  };
}
