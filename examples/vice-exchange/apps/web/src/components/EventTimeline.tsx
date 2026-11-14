import { useEffect, useMemo, useRef, useState } from "react";
import type { GameEvent } from "@vice/contracts";
import { useGame } from "../state/gameStore";
import { CATEGORY, clock, describeEvent, type EventCategory } from "../lib/format";

const CATS: Array<{ id: EventCategory; label: string }> = [
  { id: "world", label: "World" }, { id: "agent", label: "Agents" }, { id: "market", label: "Market" },
  { id: "capture", label: "Wanted / capture" }, { id: "system", label: "System" },
];
const NOISY = new Set(["WALLET_OPENED", "DECISION_REQUESTED", "AGENT_MESSAGE_DELIVERED"]);

/** Everything causally upstream and downstream of one event, via causationId. */
function chainOf(events: GameEvent[], id: string): Set<string> {
  const byId = new Map(events.map((e) => [e.eventId, e]));
  const kids = new Map<string, string[]>();
  for (const e of events) if (e.causationId) { const k = kids.get(e.causationId); if (k) k.push(e.eventId); else kids.set(e.causationId, [e.eventId]); }
  const out = new Set<string>([id]);
  let cur = byId.get(id);
  while (cur?.causationId && !out.has(cur.causationId)) { out.add(cur.causationId); cur = byId.get(cur.causationId); }
  const stack = [id];
  while (stack.length) { for (const k of kids.get(stack.pop()!) ?? []) if (!out.has(k)) { out.add(k); stack.push(k); } }
  return out;
}

export function EventTimeline() {
  const events = useGame((s) => s.events);
  const selectedEventId = useGame((s) => s.selectedEventId);
  const selectEvent = useGame((s) => s.selectEvent);
  const selectAgent = useGame((s) => s.selectAgent);
  const [off, setOff] = useState<Set<EventCategory>>(new Set(["system"]));
  const [detail, setDetail] = useState(false);
  const [follow, setFollow] = useState(true);
  const box = useRef<HTMLDivElement>(null);
  const st = useGame.getState();
  const chain = useMemo(() => (selectedEventId ? chainOf(events, selectedEventId) : null), [events, selectedEventId]);
  const story = useMemo(() => (selectedEventId ? events.find((e) => e.eventId === selectedEventId)?.correlationId ?? null : null), [events, selectedEventId]);
  const shown = useMemo(() => events.filter((e) => !off.has(CATEGORY[e.type] ?? "system") && (detail || !NOISY.has(e.type)) && (detail || e.type !== "MARKET_PRICE_UPDATED" || (e as GameEvent<"MARKET_PRICE_UPDATED">).payload.reason !== "SURVIVAL_TICK")).slice(-220), [events, off, detail]);
  const runId = useGame((s) => s.runId);
  useEffect(() => { if (!selectedEventId) setFollow(true); }, [selectedEventId, runId]);
  useEffect(() => { if (follow && !selectedEventId && box.current) box.current.scrollTop = box.current.scrollHeight; }, [shown, follow, selectedEventId]);
  const toggle = (c: EventCategory) => setOff((prev) => { const n = new Set(prev); if (n.has(c)) n.delete(c); else n.add(c); return n; });
  return (
    <div className="timeline">
      <div className="tl-bar">
        {CATS.map((c) => <button key={c.id} className={`fchip c-${c.id} ${off.has(c.id) ? "" : "on"}`} aria-pressed={!off.has(c.id)} onClick={() => toggle(c.id)}>{c.label}</button>)}
        <label className="chk"><input type="checkbox" checked={detail} onChange={(e) => setDetail(e.target.checked)} /> every step</label>
        <label className="chk"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow live</label>
        {selectedEventId && <button className="ghost small" onClick={() => selectEvent(null)}>Clear chain ({chain?.size ?? 0} linked)</button>}
        <span className="dim small">Click an event to trace its cause and effects.</span>
      </div>
      <div className="tl-list" ref={box} onScroll={(e) => { if (selectedEventId) return; const el = e.currentTarget; setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 40); }}>
        {shown.length === 0 && <div className="dim pad">Waiting for the city to do something…</div>}
        {shown.map((e) => {
          const inChain = chain?.has(e.eventId);
          const inStory = story && e.correlationId === story;
          const cls = `ev c-${CATEGORY[e.type] ?? "system"} ${chain ? (inChain ? "chain" : inStory ? "story" : "dimmed") : ""} ${e.eventId === selectedEventId ? "sel" : ""}`;
          return (
            <button key={e.eventId} className={cls} onClick={() => {
              selectEvent(e.eventId === selectedEventId ? null : e.eventId);
              if (st.npcs[e.actorId]) selectAgent(e.actorId);
            }}>
              <span className="ev-seq">#{e.serverSequence}</span>
              <span className="ev-time">{clock(e.time)}</span>
              <span className="ev-type">{e.type.replace(/_/g, " ")}</span>
              <span className="ev-text">{describeEvent(e, useGame.getState())}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
