const state = {
  shipments: [],
  selected: null,
  doc: "cargo_manifest",
  track: "decisions",
  mode: "mock",
  decisions: null,
  documents: null,
  health: null,
  run: null,
};

const queue = document.querySelector("#queue");
const documentPane = document.querySelector("#document");
const banner = document.querySelector("#banner");
const scoreboard = document.querySelector("#scoreboard");
const verdicts = document.querySelector("#verdict-panels");
const fields = document.querySelector("#fields");
const previews = document.querySelector("#previews");
const art = document.querySelector("#packet-art");
const inspectButton = document.querySelector("#inspect");
const scanButton = document.querySelector("#scan");
const scanNote = document.querySelector("#scan-note");

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(response.status + " " + url);
  return response.json();
}

function metric(label, value) {
  const node = el("div", "metric");
  node.append(el("span", null, label), el("strong", null, value));
  return node;
}

function ratio(block) {
  if (!block) return "not yet measured";
  return block.numerator + "/" + block.denominator;
}

function latency(block) {
  if (!block || block.p50 === null || block.p50 === undefined) return "not measured";
  return Math.round(block.p50) + " / " + Math.round(block.p95) + " ms";
}

function renderScore() {
  scoreboard.replaceChildren();
  const pack = state.track === "documents" ? state.documents : state.decisions;
  const measured = pack && (pack.measurements === "measured" || (pack.summary && pack.summary.measurements === "measured"));
  const simulated = !measured;
  banner.textContent = simulated
    ? "MOCK results. Speed, cost, and accuracy here are not measured model performance."
    : "Recorded live replay " + ((pack.summary && pack.summary.finished_at) || pack.run_id || "");
  if (!pack || !pack.summary) {
    scoreboard.append(metric("Results", "not yet measured"));
    return;
  }
  const summary = pack.summary;
  const drex = summary.drex || summary.drex_on_extracted_evidence;
  const router = summary.openrouter;
  const attempted = drex ? drex.attempted + "/" + drex.planned : String(summary.attempted || 0);
  const knownCost = summary.known_cost_usd || (drex && drex.cost_usd && drex.cost_usd.known_total) || "unknown";
  scoreboard.append(
    metric("Mode", simulated ? "MOCK" : "LIVE"),
    metric("Models", (state.health.drex_model || "Drex") + " / " + (state.health.openrouter_model || "frontier")),
    metric("Completed", attempted),
    metric(simulated ? "Simulated agreement" : "Accuracy", drex ? ratio(drex.accuracy) : "not yet measured"),
    metric("False clearance", drex ? ratio(drex.false_clearance) : "not yet measured"),
    metric("P50 / P95", drex && !simulated ? latency(drex.latency_ms.valid_response) : "not measured"),
    metric("Frontier/Drex", !simulated && summary.speedup && summary.speedup.median_right_over_left !== null ? summary.speedup.median_right_over_left.toFixed(2) + "x" : "not measured"),
    metric("Known cost", !simulated ? knownCost : "not measured")
  );
}

function selectedCase() {
  return state.shipments.find((item) => item.case_id === state.selected);
}

function renderQueue() {
  queue.replaceChildren();
  document.querySelector("#queue-count").textContent = state.shipments.length + " shown";
  state.shipments.forEach((item) => {
    const button = el("button", "shipment" + (item.case_id === state.selected ? " selected" : ""));
    button.type = "button";
    const copy = el("span");
    copy.append(el("strong", null, item.reference), el("div", "muted", item.case_id));
    button.append(el("span", "badge", item.species), copy);
    button.addEventListener("click", () => {
      state.selected = item.case_id;
      state.doc = "cargo_manifest";
      state.run = null;
      render();
    });
    queue.append(button);
  });
}

function renderPacket() {
  const item = selectedCase();
  inspectButton.disabled = !item || (state.run && state.run.status === "running");
  scanButton.disabled = !item;
  if (!item) {
    document.querySelector("#packet-title").textContent = "Select a shipment";
    documentPane.textContent = "No shipment selected.";
    art.replaceChildren();
    previews.replaceChildren();
    fields.replaceChildren();
    return;
  }
  document.querySelector("#packet-title").textContent = item.species + " · " + item.reference;
  const documentRecord = item.documents.find((entry) => entry.kind === state.doc) || item.documents[0];
  documentPane.textContent = documentRecord.text;
  art.replaceChildren();
  const image = document.createElement("img");
  image.alt = "Specimen artwork";
  image.src = "/media/artwork/specimen_0" + ((item.reference.charCodeAt(item.reference.length - 1) % 3) + 1) + ".png";
  art.append(image, el("div", "badge", item.species));
  previews.replaceChildren();
  item.previews.forEach((src) => {
    const preview = document.createElement("img");
    preview.alt = "Packet page preview";
    preview.src = src;
    previews.append(preview);
  });
  renderFields(item);
}

function renderFields(item) {
  fields.replaceChildren();
  const documentRun = state.documents && state.documents.rows && state.documents.rows.find((row) => row.case_id === item.case_id);
  const extracted = documentRun && state.track === "documents" ? documentRun.fields : null;
  const source = extracted || Object.entries(item.source_annotations.fields).map(([name, meta]) => ({
    path: "/" + name,
    status: meta.status,
    value: meta.expected,
    citations: meta.quotes.map((quote) => ({ source_text: quote.quote })),
    fixture: true,
  }));
  source.forEach((field) => {
    const row = el("div", "field");
    const label = (field.fixture ? "Quote " : "Extracted ") + (field.path || "");
    row.append(el("span", null, label + " · " + (field.status || "unknown") + (field.ambiguous ? " · ambiguous" : "")));
    const quotes = field.citations || [];
    if (quotes[0] && quotes[0].source_text) {
      const jump = el("button", null, "Show source");
      jump.type = "button";
      jump.addEventListener("click", () => focusQuote(quotes[0].source_text));
      row.append(jump);
    }
    fields.append(row);
  });
}

function focusQuote(quote) {
  const item = selectedCase();
  const owner = item && item.documents.find((document) => document.text.includes(quote));
  if (owner) state.doc = owner.kind;
  render();
  highlight(quote);
}

function highlight(quote) {
  const text = documentPane.textContent;
  const start = text.indexOf(quote);
  if (start < 0) {
    documentPane.classList.add("error");
    return;
  }
  documentPane.classList.remove("error");
  const before = text.slice(0, start);
  const match = text.slice(start, start + quote.length);
  const after = text.slice(start + quote.length);
  documentPane.replaceChildren(document.createTextNode(before), el("mark", "mark", match), document.createTextNode(after));
  documentPane.querySelector("mark").scrollIntoView({ block: "center" });
}

function providerCard(title, result) {
  const card = el("div", "card");
  card.append(el("h2", null, title));
  if (!result) {
    card.append(el("p", null, "not yet measured"));
    return card;
  }
  const stamp = el("div", "stamp " + String(result.verdict || "review").toLowerCase(), result.mode === "mock" ? "MOCK " + (result.verdict || result.status) : (result.verdict || result.status));
  card.append(stamp);
  const lines = [
    "Status " + result.status,
    result.probabilities ? "Probabilities " + JSON.stringify(result.probabilities) + " (not calibrated on this set)" : "Probabilities not supplied",
    "Latency " + (result.latency_ms === null || result.latency_ms === undefined ? "not measured" : Math.round(result.latency_ms) + " ms"),
    "Cost " + (result.cost_usd === null || result.cost_usd === undefined ? "unknown" : result.cost_usd),
  ];
  if (result.error) lines.push(result.error);
  lines.forEach((line) => card.append(el("p", null, line)));
  return card;
}

function renderVerdicts() {
  verdicts.replaceChildren();
  const item = selectedCase();
  const oracle = document.querySelector("#oracle");
  oracle.textContent = item ? "Privileged rule oracle: " + item.expected_verdict + ". This label is not printed on the packet." : "";
  if (state.run && state.run.case_id === (item && item.case_id)) {
    if (state.run.mode === "live") {
      verdicts.append(el("p", "muted", "LIVE inspection: real API calls. Latency and cost below were measured."));
    } else {
      verdicts.append(el("p", "muted", "MOCK inspection: simulated locally. No API call was made; latency and cost are not measurements."));
    }
    verdicts.append(providerCard("Drex", state.run.drex), providerCard("Frontier", state.run.openrouter));
    if (state.run.status === "error") verdicts.append(el("p", "error", state.run.error || "Inspection failed"));
    return;
  }
  const pack = state.track === "documents" ? state.documents : state.decisions;
  const row = pack && (pack.cases || pack.rows || []).find((entry) => entry.case_id === state.selected);
  if (!row) {
    verdicts.append(el("p", null, "No saved result for this shipment in the selected view."));
    return;
  }
  const drex = row.drex || (row.decisions && row.decisions.drex);
  const router = row.openrouter || (row.decisions && row.decisions.openrouter);
  verdicts.append(providerCard(state.track === "documents" ? "Drex on extracted evidence" : "Drex", drex));
  if (router) verdicts.append(providerCard(state.track === "documents" ? "Frontier on the same extraction" : "Frontier", router));
}

function render() {
  renderScore();
  renderQueue();
  renderPacket();
  renderVerdicts();
  document.querySelectorAll("[data-doc]").forEach((button) => {
    button.classList.toggle("active", button.dataset.doc === state.doc);
  });
  document.querySelector("#track-text").classList.toggle("active", state.track === "decisions");
  document.querySelector("#track-doc").classList.toggle("active", state.track === "documents");
}

async function loadResults() {
  const [liveDecisions, mockDecisions, liveDocs, mockDocs] = await Promise.all([
    getJson("/api/results?track=decisions&mode=live"),
    getJson("/api/results?track=decisions&mode=mock"),
    getJson("/api/results?track=documents&mode=live"),
    getJson("/api/results?track=documents&mode=mock"),
  ]);
  state.decisions = liveDecisions.run || liveDecisions.summary ? liveDecisions : mockDecisions;
  state.documents = liveDocs.run || liveDocs.summary ? liveDocs : mockDocs;
  state.mode = state.decisions.measurements === "measured" ? "live" : "mock";
}

async function poll(runId) {
  for (let attempt = 0; attempt < 300; attempt += 1) {
    const body = await getJson("/api/runs/" + runId);
    state.run = body;
    render();
    if (body.status !== "running") return;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

function applyInspectMode(mode) {
  const note = document.querySelector("#inspect-note");
  if (mode === "live") {
    inspectButton.textContent = "Run live inspection";
    note.textContent = "The button calls both providers for real. Each click makes live API calls and spends a little of the configured budget.";
  } else {
    inspectButton.textContent = "Run mock inspection";
    note.textContent = "The button replays the local mock. It does not spend the live budget.";
  }
}

document.querySelectorAll("[data-doc]").forEach((button) => {
  button.addEventListener("click", () => {
    state.doc = button.dataset.doc;
    render();
  });
});
document.querySelector("#track-text").addEventListener("click", () => { state.track = "decisions"; render(); });
document.querySelector("#track-doc").addEventListener("click", () => { state.track = "documents"; render(); });
inspectButton.addEventListener("click", async () => {
  if (!state.selected || (state.run && state.run.status === "running")) return;
  inspectButton.disabled = true;
  try {
    const response = await fetch("/api/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case_id: state.selected }),
    });
    const body = await response.json();
    if (body.status === "completed" || body.status === "error") {
      state.run = body;
      render();
    } else if (response.status === 409 || response.status === 202) await poll(body.run_id);
    else verdicts.append(el("p", "error", body.detail || "Inspection was refused"));
  } catch (error) {
    verdicts.append(el("p", "error", error.message));
  } finally {
    if (state.run && state.run.status === "running") state.run = null;
    inspectButton.disabled = !state.selected;
    render();
  }
});
scanButton.addEventListener("click", async () => {
  if (!state.selected) return;
  scanButton.disabled = true;
  try {
    const response = await fetch("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case_id: state.selected }),
    });
    const body = await response.json();
    if (response.ok) {
      documentPane.textContent = body.text;
      scanNote.textContent = "Ultrasonic evidence version. Not a benchmark result.";
    } else {
      scanNote.textContent = body.detail || "Scan was refused";
    }
  } catch (error) {
    scanNote.textContent = error.message;
  } finally {
    scanButton.disabled = !state.selected;
  }
});

async function boot() {
  try {
    state.health = await getJson("/api/health");
    applyInspectMode(state.health.inspect_mode);
    const payload = await getJson("/api/shipments");
    state.shipments = payload.shipments;
    state.selected = state.shipments[0] ? state.shipments[0].case_id : null;
    await loadResults();
  } catch (error) {
    banner.textContent = "The terminal could not load local data.";
    documentPane.textContent = error.message;
  }
  render();
}

boot();
