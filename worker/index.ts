import { DurableObject } from "cloudflare:workers";
import { PlayService } from "../server/play-service";
import { makeRuntime } from "../server/byok";
interface Env {
  ASSETS: Fetcher;
  ARENA: DurableObjectNamespace<Arena>;
  PROVIDER_QUOTA: DurableObjectNamespace<ProviderQuota>;
  GROQ_API_KEY?: string;
  ENABLE_LIVE?: string;
}
const secureHeaders = (response: Response) => {
  const r = new Response(response.body, response);
  r.headers.set("X-Content-Type-Options", "nosniff");
  r.headers.set("Referrer-Policy", "no-referrer");
  r.headers.set(
    "Content-Security-Policy",
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
  );
  return r;
};
export default {
  async fetch(request: Request, env: Env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/")) {
      if (!url.pathname.startsWith("/api/play/"))
        return new Response("Not found", { status: 404 });
      // The edge supplies this header; never trust a browser-supplied IP header.
      const headers = new Headers(request.headers);
      headers.set(
        "x-heist-ip",
        request.headers.get("cf-connecting-ip") || "unknown",
      );
      return secureHeaders(
        await env.ARENA.get(env.ARENA.idFromName("arena-v1")).fetch(
          new Request(request, { headers }),
        ),
      );
    }
    return secureHeaders(await env.ASSETS.fetch(request));
  },
};
// Sessions and BYOK secrets live ONLY in memory. Eviction/restart requires a new session.
export class Arena extends DurableObject<Env> {
  #service: PlayService;
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    const enabled = env.ENABLE_LIVE === "true" && !!env.GROQ_API_KEY;
    this.#service = new PlayService(
      (key, documents) =>
        makeRuntime(key, documents, {
          groqKey: env.GROQ_API_KEY,
          offline: !enabled,
          reserveGroq: async (units) => {
            const response = await env.PROVIDER_QUOTA.get(
              env.PROVIDER_QUOTA.idFromName("groq-lifetime-v1"),
            ).fetch("https://internal/reserve", {
              method: "POST",
              body: JSON.stringify({ units }),
            });
            if (!response.ok) throw Error("Shared Groq quota exhausted.");
          },
        }),
      enabled,
    );
  }
  async fetch(request: Request) {
    // Alarms sweep expired secrets even when the arena is otherwise idle.
    await this.ctx.storage.setAlarm(Date.now() + 30000);
    return this.#service.handle(
      request,
      request.headers.get("x-heist-ip") || "unknown",
    );
  }
  async alarm() {
    this.#service.sweep();
    await this.ctx.storage.setAlarm(Date.now() + 30000);
  }
}
export class ProviderQuota extends DurableObject<Env> {
  async fetch(request: Request) {
    if (request.method !== "POST")
      return new Response("Not found", { status: 404 });
    // Synchronous SQLite statement makes reservation atomic across all sessions.
    this.ctx.storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS quota (id INTEGER PRIMARY KEY, used INTEGER NOT NULL, units INTEGER NOT NULL)",
    );
    this.ctx.storage.sql.exec("INSERT OR IGNORE INTO quota VALUES (1, 0, 0)");
    const { units } = (await request.json()) as { units: number };
    if (!Number.isInteger(units) || units < 1 || units > 15000)
      return new Response("Invalid reservation", { status: 400 });
    const row = this.ctx.storage.sql
      .exec<{ used: number; units: number }>(
        "SELECT used, units FROM quota WHERE id = 1",
      )
      .one();
    if (row.used >= 60 || row.units + units > 100000)
      return new Response("Quota exhausted", { status: 429 });
    this.ctx.storage.sql.exec(
      "UPDATE quota SET used = used + 1, units = units + ? WHERE id = 1",
      units,
    );
    return new Response("Reserved");
  }
}
