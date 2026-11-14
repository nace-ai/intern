import { DurableObject } from "cloudflare:workers";
import { makeRuntime } from "../server/byok";
import { PlayService } from "../server/play-service";
import { TrainingStore } from "./training-store";
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
    "Permissions-Policy",
    "camera=(), microphone=(), geolocation=()",
  );
  r.headers.set("Strict-Transport-Security", "max-age=31536000");
  r.headers.set(
    "Content-Security-Policy",
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
  );
  return r;
};
export default {
  async fetch(request: Request, env: Env) {
    const url = new URL(request.url);
    // Never let the SPA fallback turn private/source paths into public assets.
    if (
      /^\/(?:\.|server(?:\/|$)|worker(?:\/|$)|src(?:\/|$)|node_modules(?:\/|$))/.test(
        url.pathname,
      )
    )
      return secureHeaders(new Response("Not found", { status: 404 }));
    if (url.pathname.startsWith("/api/")) {
      if (!url.pathname.startsWith("/api/play/"))
        return secureHeaders(new Response("Not found", { status: 404 }));
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
// Training state survives eviction/redeployment in private SQLite storage.
// Live is enabled only by an explicit flag plus a server-side Groq secret.
export class Arena extends DurableObject<Env> {
  #service: PlayService;
  #store: TrainingStore;
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    const liveAvailable =
      env.ENABLE_LIVE === "true" && Boolean(env.GROQ_API_KEY);
    const quota = env.PROVIDER_QUOTA.get(
      env.PROVIDER_QUOTA.idFromName("groq-final-sprint-v1"),
    );
    this.#service = new PlayService(
      (key, allowDocuments) =>
        makeRuntime(key, allowDocuments, {
          groqKey: env.GROQ_API_KEY,
          offline: !liveAvailable,
          reserveGroq: async (units) => {
            const response = await quota.fetch(
              "https://quota.internal/reserve",
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ units }),
              },
            );
            if (!response.ok)
              throw Error(
                "Shared Groq request quota reached. No request sent.",
              );
          },
        }),
      liveAvailable,
    );
    this.#store = new TrainingStore(ctx.storage);
    ctx.blockConcurrencyWhile(async () => {
      this.#service.restoreTraining(this.#store.load());
    });
  }
  async fetch(request: Request) {
    // Bound the stream before entering the serialized state transition.
    let raw = "";
    if (request.method !== "GET") {
      const reader = request.body?.getReader();
      const decoder = new TextDecoder();
      let bytes = 0;
      if (reader)
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          bytes += chunk.value.byteLength;
          if (bytes > 12000) {
            await reader.cancel();
            return Response.json(
              { error: "Request exceeds 12 KB." },
              { status: 413, headers: { "Cache-Control": "no-store" } },
            );
          }
          raw += decoder.decode(chunk.value, { stream: true });
        }
      raw += decoder.decode();
    }
    const ip = request.headers.get("x-heist-ip") || "unknown";
    const digest = await crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(ip),
    );
    const bucket = Array.from(new Uint8Array(digest), (b) =>
      b.toString(16).padStart(2, "0"),
    ).join("");
    const bounded = new Request(request.url, {
      method: request.method,
      headers: request.headers,
      ...(request.method !== "GET" ? { body: raw } : {}),
    });
    return this.ctx.blockConcurrencyWhile(async () => {
      const response = await this.#service.handle(bounded, bucket);
      this.#store.save(this.#service.trainingCheckpoint());
      if (this.#service.hasExpiringState)
        await this.ctx.storage.setAlarm(Date.now() + 30000);
      return response;
    });
  }
  async alarm() {
    await this.ctx.blockConcurrencyWhile(async () => {
      this.#service.sweep();
      this.#store.save(this.#service.trainingCheckpoint());
      if (this.#service.hasExpiringState)
        await this.ctx.storage.setAlarm(Date.now() + 30000);
    });
  }
}
export class ProviderQuota extends DurableObject<Env> {
  async fetch(request: Request) {
    if (request.method !== "POST")
      return secureHeaders(new Response("Not found", { status: 404 }));
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
