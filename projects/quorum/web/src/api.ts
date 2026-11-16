import type { AttackDetail, AttackRecord, History, RoundRecord, Usage } from "./types";

export const DEMO = new URLSearchParams(window.location.search).get("demo") === "1";

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return (await r.json()) as T;
}

export function withKey(rec: AttackRecord): AttackRecord {
  return rec.key ? rec : { ...rec, key: `${rec.round}-${rec.id}` };
}

export async function fetchAttacks(): Promise<AttackRecord[]> {
  const list = await getJson<AttackRecord[]>("/api/arena/attacks");
  return Array.isArray(list) ? list.map(withKey) : [];
}

export async function fetchRounds(): Promise<RoundRecord[]> {
  const list = await getJson<RoundRecord[]>("/api/arena/rounds");
  return Array.isArray(list) ? list : [];
}

export function fetchAttack(key: string): Promise<AttackDetail> {
  return getJson<AttackDetail>(`/api/arena/attacks/${encodeURIComponent(key)}`);
}

export function pageUrl(key: string): string {
  return `/api/arena/attacks/${encodeURIComponent(key)}/page.png`;
}

/** Page image as an object URL plus its size in PDF points from the response headers. */
export async function fetchPage(key: string): Promise<{ url: string; w: number | null; h: number | null }> {
  const r = await fetch(pageUrl(key));
  if (!r.ok) throw new Error(`page.png: HTTP ${r.status}`);
  const w = Number(r.headers.get("X-Page-Width"));
  const h = Number(r.headers.get("X-Page-Height"));
  const blob = await r.blob();
  return { url: URL.createObjectURL(blob), w: w > 0 ? w : null, h: h > 0 ? h : null };
}

/** History summary for a supplier, or null if it has not been built (404). */
export async function fetchHistory(supplierId: string): Promise<History | null> {
  const r = await fetch(`/api/history/${encodeURIComponent(supplierId)}`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`/api/history/${supplierId}: HTTP ${r.status}`);
  return (await r.json()) as History;
}

export function fetchUsage(): Promise<Usage> {
  return getJson<Usage>("/api/usage");
}

export interface WsEvent {
  type: string;
  data?: unknown;
}

/** Connect to /ws and reconnect after a drop. Returns a function that closes it for good. */
export function connectLive(onEvent: (ev: WsEvent) => void, onStatus: (up: boolean) => void): () => void {
  let ws: WebSocket | null = null;
  let stopped = false;
  let timer: number | undefined;
  let delay = 1000;

  const open = () => {
    if (stopped) return;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${window.location.host}/ws`);
    ws.onopen = () => {
      delay = 1000;
      onStatus(true);
    };
    ws.onmessage = (m) => {
      try {
        const ev = JSON.parse(String(m.data)) as WsEvent;
        if (ev && typeof ev.type === "string") onEvent(ev);
      } catch {
        /* ignore bad frames */
      }
    };
    ws.onclose = () => {
      onStatus(false);
      if (stopped) return;
      timer = window.setTimeout(open, delay);
      delay = Math.min(delay * 2, 10000);
    };
    ws.onerror = () => ws?.close();
  };
  open();
  return () => {
    stopped = true;
    if (timer !== undefined) window.clearTimeout(timer);
    ws?.close();
  };
}
