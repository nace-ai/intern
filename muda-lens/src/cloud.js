// Stores shift-report PDFs in a Nace workspace (cloud), where Drex's document tools can search them later.
//   1. find or create the workspace "Muda Lens shift reports" (id cached in .nace-workspace.json, git-ignored)
//   2. POST /v1/documents/upload-grants  -> one-use upload_url + token
//   3. POST upload_url (multipart: file + metadata JSON)  -> stored file
const fs = require("node:fs"), path = require("node:path");
const BASE = () => (process.env.NACE_BASE_URL || process.env.DREX_BASE_URL || "https://console.nace.ai").replace(/\/$/, "");
const KEY = () => process.env.NACE_API_KEY || process.env.DREX_API_KEY;
const WS_NAME = "Muda Lens shift reports";
const CACHE = path.join(__dirname, "..", ".nace-workspace.json");

async function nace(method, p, body) {
  const res = await fetch(BASE() + p, {
    method, headers: { Authorization: `Bearer ${KEY()}`, "X-Nace-Client": "muda-lens/1", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  if (!res.ok) throw new Error(/Security Checkpoint|<!DOCTYPE/i.test(text) ? "Nace's firewall is blocking this network right now." : `Nace ${res.status}: ${text.slice(0, 240)}`);
  return text ? JSON.parse(text) : {};
}

async function workspaceId() {
  try { const c = JSON.parse(fs.readFileSync(CACHE, "utf8")); if (c.workspace_id) return c.workspace_id; } catch {}
  const list = await nace("GET", "/v1/workspaces");
  let ws = (list.items || []).find(w => w.name === WS_NAME);
  if (!ws) ws = await nace("POST", "/v1/workspaces", { name: WS_NAME });
  const id = ws.workspace_id || ws.id;
  fs.writeFileSync(CACHE, JSON.stringify({ workspace_id: id, name: WS_NAME }));
  return id;
}

async function storeReport({ pdfBase64, filename }) {
  if (!KEY()) throw new Error("No Nace API key on the server.");
  if (typeof pdfBase64 !== "string" || !pdfBase64.length) throw new Error("No PDF to store.");
  const bytes = Buffer.from(pdfBase64, "base64");
  if (bytes.subarray(0, 4).toString() !== "%PDF") throw new Error("That file isn't a PDF.");
  if (bytes.length > 10 * 1024 * 1024) throw new Error("The PDF is larger than 10 MB.");
  const safe = String(filename || "shift-report.pdf").replace(/[^A-Za-z0-9._-]/g, "-").slice(0, 100);
  const wsPath = `shift-reports/${safe}`;
  const wsId = await workspaceId();
  const grant = await nace("POST", "/v1/documents/upload-grants", { path: wsPath, ttl_seconds: 600, workspace_id: wsId });
  const form = new FormData();
  form.append("file", new Blob([bytes], { type: "application/pdf" }), safe);
  form.append("metadata", JSON.stringify({ path: wsPath, on_conflict: "new_version" }));
  const res = await fetch(grant.upload_url, { method: "POST", headers: { "X-Upload-Token": grant.token }, body: form });
  const text = await res.text();
  if (!res.ok) throw new Error(`Upload ${res.status}: ${text.slice(0, 240)}`);
  const job = text ? JSON.parse(text) : {};
  const file = job.result?.file || job.file || {};
  const fileId = file.file_id || file.id || null;
  const ws = grant.workspace_id || wsId;
  // A signed download link the engineers can open (Nace decides how long it lasts).
  let downloadUrl = null;
  if (fileId) { try { downloadUrl = (await nace("GET", `/v1/workspaces/${ws}/files/${fileId}`)).download_url || null; } catch {} }
  return { stored: true, workspace: WS_NAME, workspace_id: ws, path: wsPath, file_id: fileId, bytes: bytes.length, status: job.status || "accepted", download_url: downloadUrl };
}

module.exports = { storeReport };
