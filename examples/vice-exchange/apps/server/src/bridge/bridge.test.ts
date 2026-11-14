import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, describe, expect, it } from "vitest";
import { WebSocket } from "ws";
import { attachWorldBridge, bridgeOriginAllowed, type WorldBridge } from "./index";

let cleanup: Array<() => void> = [];
afterEach(() => { for (const c of cleanup) c(); cleanup = []; });

function boot(): Promise<number> {
  const http = createServer();
  // The origin gate runs before any room is touched, so an empty room manager is enough here.
  const bridge: WorldBridge = attachWorldBridge(http, { get: () => undefined } as never);
  cleanup.push(() => { bridge.close(); http.close(); });
  return new Promise((res) => http.listen(0, "127.0.0.1", () => res((http.address() as AddressInfo).port)));
}
const attempt = (port: number, origin?: string): Promise<"open" | "refused"> => new Promise((res) => {
  const ws = new WebSocket(`ws://127.0.0.1:${port}/bridge`, origin ? { origin } : {});
  ws.on("open", () => { ws.close(); res("open"); });
  ws.on("error", () => res("refused"));
});

describe("world bridge origin gate", () => {
  it("allows native clients and private origins, refuses public ones", () => {
    expect(bridgeOriginAllowed(undefined)).toBe(true);
    expect(bridgeOriginAllowed("http://localhost:5173")).toBe(true);
    expect(bridgeOriginAllowed("http://192.168.1.20:5173")).toBe(true);
    expect(bridgeOriginAllowed("https://evil.example")).toBe(false);
    expect(bridgeOriginAllowed("not a url")).toBe(false);
  });
  it("refuses an upgrade from a public Origin and accepts one with no Origin", async () => {
    const port = await boot();
    expect(await attempt(port, "https://evil.example")).toBe("refused");
    expect(await attempt(port)).toBe("open");
  });
});
