#!/usr/bin/env node
// Zero-dependency server: serves the dashboard and proxies live decisions so API keys
// never reach the browser.   node server.js   ->  http://localhost:5173
const http = require("node:http"), fs = require("node:fs"), path = require("node:path");
try { process.loadEnvFile?.(path.join(__dirname, ".env")); } catch {}
const F = require("./src/engine.js");
const { buildState } = require("./bench/cases.js");
const C = require("./bench/contenders.js");
const { diagnosePhoto } = require("./src/photo.js");
const { runSwarm } = require("./src/swarm.js");
const { storeReport } = require("./src/cloud.js");

const PORT = Number(process.env.PORT || 5173);
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".json": "application/json", ".css": "text/css", ".csv": "text/csv", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".md": "text/markdown" };

const server = http.createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/api/status") {
    const lan = Object.values(require("node:os").networkInterfaces()).flat()
      .filter(n => n && n.family === "IPv4" && !n.internal && /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(n.address))
      .map(n => `http://${n.address}:${PORT}`);
    return json(res, 200, { drex: C.drex.enabled() && C.drex.label(), claude: C.claude.enabled() && C.claude.label(), lan });
  }
  if (req.method === "POST" && req.url === "/api/decide") {
    let body = ""; for await (const chunk of req) { body += chunk; if (body.length > 5e6) return json(res, 413, { error: "too large" }); }
    try {
      const { rows, contenders = ["drex"] } = JSON.parse(body);
      const state = buildState(rows);
      const out = {};
      await Promise.all(contenders.filter(n => ["drex", "claude", "rules"].includes(n)).map(async n => {
        if (!C[n].enabled()) { out[n] = { label: C[n].label(), error: "not configured" }; return; }
        try { out[n] = { label: C[n].label(), ...(await C[n](state)) }; } catch (e) { out[n] = { label: C[n].label(), error: e.message }; }
      }));
      return json(res, 200, out);
    } catch (e) { return json(res, 400, { error: e.message }); }
  }
  if (req.method === "POST" && req.url === "/api/report/store") {
    let body = ""; for await (const chunk of req) { body += chunk; if (body.length > 15e6) return json(res, 413, { error: "The PDF is too large." }); }
    try { return json(res, 200, await storeReport(JSON.parse(body))); }
    catch (e) { return json(res, 400, { error: e.message }); }
  }
  if (req.method === "POST" && req.url === "/api/swarm") {
    let body = ""; for await (const chunk of req) { body += chunk; if (body.length > 5e6) return json(res, 413, { error: "too large" }); }
    try { return json(res, 200, await runSwarm(JSON.parse(body))); }
    catch (e) { return json(res, 400, { error: e.message }); }
  }
  if (req.method === "POST" && req.url === "/api/photo") {
    let body = ""; for await (const chunk of req) { body += chunk; if (body.length > 4.2e6) return json(res, 413, { error: "Photos are too large together. Use fewer or smaller photos (about 3 MB total)." }); }
    try { return json(res, 200, await diagnosePhoto(JSON.parse(body))); }
    catch (e) { return json(res, 400, { error: e.message }); }
  }
  // static files
  const rel = decodeURIComponent((req.url.split("?")[0] === "/" ? "/index.html" : req.url.split("?")[0]));
  const file = path.normalize(path.join(__dirname, rel));
  if (!file.startsWith(__dirname) || /[\\/]\.(env|git|nace)/.test(file)) { res.writeHead(403); return res.end(); }
  fs.readFile(file, (err, data) => {
    if (err) { res.writeHead(404); return res.end("not found"); }
    res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream" }); res.end(data);
  });
});
function json(res, code, obj) { res.writeHead(code, { "Content-Type": "application/json" }); res.end(JSON.stringify(obj)); }
server.listen(PORT, () => console.log(`Muda Lens on http://localhost:${PORT}  (Drex: ${C.drex.enabled() ? "on" : "off"}, Claude: ${C.claude.enabled() ? "on" : "off"})`));
