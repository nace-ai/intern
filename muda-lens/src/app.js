(() => {
  const F = window.MudaLens;
  const $ = id => document.getElementById(id);
  const costs = { ...F.DEFAULT_COSTS };
  const charts = {};
  let rows = [];

  const SCENARIOS = {
    baseline: { seed: 42, drift: {} },
    litho: { seed: 7, drift: { PH: { queue: 2.6, shift: 0.022, sd: 1.3, dpu: 2.2 } } },
    backend: { seed: 11, drift: { WB: { dpu: 4, sd: 1.6, shift: -1.2 }, DC: { queue: 1.8 } } },
    improved: { seed: 42, drift: Object.fromEntries(F.STEPS.map(s => [s.id, { queue: 0.45, dpu: 0.5, sd: 0.7 }])) },
  };

  const money = v => "$" + (v >= 1e6 ? (v / 1e6).toFixed(2) + "M" : v >= 1e3 ? (v / 1e3).toFixed(1) + "k" : v.toFixed(0));
  const pill = (v, good, warn, fmt = x => x.toFixed(2), higherBetter = true) => {
    const cls = higherBetter ? (v >= good ? "good" : v >= warn ? "warn" : "bad") : (v <= good ? "good" : v <= warn ? "warn" : "bad");
    return `<span class="pill ${cls}">${fmt(v)}</span>`;
  };

  Chart.defaults.color = "#4e473f"; Chart.defaults.borderColor = "#d5c9b6"; Chart.defaults.font.family = "IBM Plex Sans, system-ui, sans-serif";

  function chart(id, cfg) { if (charts[id]) charts[id].destroy(); charts[id] = new Chart($(id), cfg); }

  function render(res) {
    const k = res.kpi;
    $("kpis").innerHTML = [
      ["Process cycle efficiency", pill(k.pce * 100, 25, 10, x => x.toFixed(1) + "%")],
      ["Lead time", k.leadDays.toFixed(1) + " days"],
      ["Rolled throughput yield", pill(k.rty * 100, 95, 85, x => x.toFixed(1) + "%")],
      ["DPMO (per step)", k.dpmo.toFixed(0)],
      ["Sigma level", pill(k.sigma, 4.5, 3.5, x => x.toFixed(2) + "σ")],
      ["Waste cost / lot", money(k.wastePerLot)],
      ["Constraint", res.bottleneck.name],
    ].map(([l, v]) => `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`).join("");

    const maxLead = Math.max(...res.steps.map(s => s.va + s.queue));
    $("vsm").innerHTML = res.steps.map(s => `<div class="box"><div class="n">${s.name}</div>
      VA ${(s.va / 60).toFixed(1)}h · Wait ${(s.queue / 60).toFixed(1)}h<br>PCE ${(s.pce * 100).toFixed(0)}%
      <div class="bar" style="display:flex"><i style="width:${s.va / maxLead * 100}%;background:#2f7a6c"></i><i style="width:${s.queue / maxLead * 100}%;background:#c08a2e"></i></div></div>`).join("");

    chart("pareto", { data: { labels: res.pareto.map(p => p.name), datasets: [
      { type: "bar", label: "Cost", data: res.pareto.map(p => p.value), backgroundColor: res.pareto.map(p => F.WASTES[p.key].color), yAxisID: "y" },
      { type: "line", label: "Cumulative %", data: res.pareto.map(p => p.cum * 100), borderColor: "#1c1712", pointRadius: 3, yAxisID: "y1" } ] },
      options: { plugins: { legend: { display: false } }, scales: { y: { ticks: { callback: money } }, y1: { position: "right", min: 0, max: 100, grid: { display: false }, ticks: { callback: v => v + "%" } } } } });

    chart("stack", { type: "bar", data: { labels: res.steps.map(s => s.id), datasets: Object.entries(F.WASTES).map(([key, w]) => ({ label: w.name, data: res.steps.map(s => s.waste[key]), backgroundColor: w.color })) },
      options: { plugins: { legend: { labels: { boxWidth: 10, font: { size: 10 } } }, tooltip: { callbacks: { title: i => res.steps[i[0].dataIndex].name } } }, scales: { x: { stacked: true }, y: { stacked: true, ticks: { callback: money } } } } });

    chart("cpk", { type: "bar", data: { labels: res.steps.map(s => s.id), datasets: [
      { label: "Cpk", data: res.steps.map(s => s.cpk), backgroundColor: res.steps.map(s => s.cpk >= 1.33 ? "#2f6b55" : s.cpk >= 1 ? "#c08a2e" : "#9c341f") },
      { type: "line", label: "Target 1.33", data: res.steps.map(() => 1.33), borderColor: "#7a7268", borderDash: [5, 5], pointRadius: 0 } ] },
      options: { plugins: { legend: { display: false }, tooltip: { callbacks: { title: i => res.steps[i[0].dataIndex].name + " — " + res.steps[i[0].dataIndex].spec.param } } } } });

    $("recs").innerHTML = res.recommendations.map(r => `<div class="rec"><b>${F.WASTES[r.waste].name}</b><span class="tag">${r.phase} · ${r.step}</span><div>${r.text}</div></div>`).join("");

    $("tbl").innerHTML = `<tr><th>Step</th><th>Area</th><th>CTQ</th><th>Mean</th><th>σ</th><th>Cp</th><th>Cpk</th><th>DPMO</th><th>FPY</th><th>Sigma</th><th>Holds</th><th>Rework</th><th>Waste $</th></tr>` +
      res.steps.map(s => `<tr><td>${s.name}</td><td>${s.area}</td><td>${s.spec.param} [${s.spec.lsl}–${s.spec.usl}]</td><td>${s.mu.toFixed(3)}</td><td>${s.s.toFixed(3)}</td>
        <td>${s.cp.toFixed(2)}</td><td>${pill(s.cpk, 1.33, 1)}</td><td>${s.dpmo.toFixed(0)}</td><td>${(s.fpy * 100).toFixed(2)}%</td><td>${s.sigma.toFixed(2)}σ</td>
        <td>${s.holds}</td><td>${s.rework}</td><td>${money(s.wasteTotal)}</td></tr>`).join("");
  }

  // Latest results, kept for the shift report.
  const LAST = { res: null, decide: null, photo: null, swarm: null, recorded: {} };
  function run() { LAST.res = F.analyze(rows, costs); LAST.decide = null; LAST.swarm = null; render(LAST.res); }
  function loadScenario() { const s = SCENARIOS[$("scenario").value]; rows = F.generateLots({ lots: 200, seed: s.seed, drift: s.drift }); run(); }

  $("run").onclick = loadScenario;
  $("scenario").onchange = loadScenario;
  $("dl").onclick = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([F.toCSV(rows)], { type: "text/csv" })); a.download = "fab_lots_sample.csv"; a.click(); };
  $("csv").onchange = e => { const f = e.target.files[0]; if (!f) return; f.text().then(t => { try { rows = F.fromCSV(t); run(); } catch (err) { alert(err.message); } }); };
  $("cols").textContent = F.COLS.join(", ");
  $("costs").innerHTML = Object.entries(costs).map(([k, v]) => `<label>${k}<input type="number" step="any" data-k="${k}" value="${v}"></label>`).join("");
  $("costs").oninput = e => { const k = e.target.dataset.k; if (k) { costs[k] = Number(e.target.value) || 0; run(); } };

  // ---------- Drex live decisions (needs `npm start`, which proxies the API) ----------
  const Q = ["root_waste", "culprit_step", "countermeasure", "disposition"];
  const esc = s => String(s ?? "—").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  // Recorded real Drex runs (bench/recorded.json). Replayed, clearly labelled, only when the live
  // API can't be reached: on the public site, or if the venue network blocks the API mid-demo.
  let RECORDED = {};
  fetch("bench/recorded.json").then(r => r.ok ? r.json() : {}).then(j => { RECORDED = j || {}; }).catch(() => {});
  const recNote = when => `<p class="recnote">A recorded Drex run from ${esc(new Date(when).toLocaleString())}. The live API isn’t reachable from here right now.</p>`;
  const liveFailed = e => /fetch|Failed|NetworkError|HTTP 4|HTTP 5|403|429|5\d\d|not configured|Server returned/i.test(e.message);

  async function decide(contenders) {
    $("drexStatus").textContent = "Deciding…";
    try {
      const r = await fetch("api/decide", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rows, contenders }) });
      if (!r.ok) throw new Error("Server returned " + r.status + ". Start it with `npm start`.");
      let out = await r.json(), note = "";
      if (out.drex?.error && /firewall|HTTP (403|429|5)/i.test(out.drex.error) && !contenders.includes("claude")) {
        const r2 = await fetch("api/decide", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rows, contenders: ["claude", "rules"] }) });
        if (r2.ok) { const o2 = await r2.json(); if (o2.claude?.answers) { out = { ...out, ...o2 }; note = `<p class="recnote">⚠ Drex wasn’t reachable from this network, so the frontier LLM and rules answered as a fallback.</p>`; } }
      }
      if (out.drex?.error && !out.claude?.answers) throw new Error(out.drex.error);
      renderDecide(out, note);
      $("drexStatus").textContent = "";
    } catch (e) {
      const rec = RECORDED.line?.[$("scenario").value];
      if (rec && liveFailed(e)) { renderDecide(rec.result, recNote(rec.at)); $("drexStatus").textContent = ""; return; }
      $("drexStatus").textContent = /fetch|Failed|Server returned (404|405|501)/.test(e.message) ? "This public page can’t call Drex live. Live calls run on the team laptop." : e.message;
    }
  }
  // Drex's call on the week's line data: a plain-English headline, then the contenders side by side.
  const stepName = id => F.STEPS.find(s => s.id === id)?.name || id;
  const cellSay = (q, v) => q === "culprit_step" ? stepName(v) : say(v);
  function decideHeadline(d) {
    if (!d || d.error || !d.answers) return "";
    const a = d.answers, c = d.confidence || {};
    const sure = Math.min(c.root_waste ?? 1, c.culprit_step ?? 1);
    if (sure < 0.5) {
      return `<div class="call-card good"><div class="call-top"><span class="call-k">Drex’s call</span></div>
        <p class="call-h">Nothing clearly wrong this week.</p>
        <p class="call-s">Drex’s best guess, ${esc(say(a.root_waste).toLowerCase())} at ${esc(stepName(a.culprit_step))}, is only ${pct(sure)} sure. That’s noise, not a finding, so nobody gets pulled off the line.</p></div>`;
    }
    const act = { release: "Let the lots ship.", hold: "Hold the lots for review.", rework: "Rework the lots." }[a.disposition] || "";
    const bad = a.root_waste === "defects" || a.disposition !== "release";
    return `<div class="call-card ${bad ? "bad" : "good"}"><div class="call-top"><span class="call-k">Drex’s call</span><span class="sev ${bad ? "sev-high" : "sev-medium"}">${pct(sure)} sure</span></div>
      <p class="call-h">${esc(say(a.root_waste))} at ${esc(stepName(a.culprit_step))}.</p>
      <p class="call-s">${esc(act)} Run ${esc(say(a.countermeasure))}.</p></div>`;
  }
  function renderDecide(out, note = "") {
      LAST.decide = out.drex && !out.drex.error ? out.drex : null; LAST.recorded.decide = !!note;
      $("decideHead").innerHTML = decideHeadline(out.drex);
      $("decide").innerHTML = `<tr><th>Contender</th><th>Waste</th><th>Where</th><th>Fix</th><th>Lots</th><th>Time</th><th>Cost</th></tr>` +
        Object.values(out).map(o => o.error ? `<tr><td>${esc(o.label)}</td><td colspan="6" style="text-align:left;color:var(--warn)">${esc(o.error)}</td></tr>` :
          `<tr><td>${esc(o.label)}</td>${Q.map(q => { const c = o.confidence?.[q];
              return `<td>${esc(cellSay(q, o.answers[q]))}${c != null ? ` <span class="fp${c < 0.5 ? " low" : ""}">${pct(c)}</span>` : ""}</td>`; }).join("")}
           <td>${o.latencyMs.toLocaleString()} ms</td><td>$${o.costUsd.toFixed(5)}</td></tr>`).join("");
      $("decideNote").innerHTML = note;
  }
  $("askDrex").onclick = () => decide(["drex"]);
  $("askAll").onclick = () => decide(["drex", "claude", "rules"]);
  fetch("api/status").then(r => r.json()).then(s => { liveAvailable = !!s.drex; $("drexStatus").textContent = s.drex ? `Connected: ${s.drex}` : "Drex not configured: set DREX_BASE_URL in .env"; }).catch(() => { $("drexStatus").textContent = "Public demo: Ask Drex replays recorded live runs for each scenario."; $("photoStatus").textContent = "Public demo: pick a sample below to see Drex’s recorded answer. Your own photos need the team laptop."; });

  // ---------- Photo diagnosis (Drex Max reads images) ----------
  const photos = []; // { name, dataUrl }
  const MAX_PHOTOS = 8, MAX_SIDE = 1600, BUDGET = 3.2e6; // request body must stay under 4 MiB
  $("photoStep").innerHTML += F.STEPS.map(s => `<option value="${s.id}">${s.name}</option>`).join("");

  function shrink(file, side = MAX_SIDE, q = 0.85) {
    return new Promise((ok, fail) => {
      const img = new Image(), url = URL.createObjectURL(file);
      img.onload = () => {
        const k = Math.min(1, side / Math.max(img.width, img.height));
        const c = document.createElement("canvas"); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height); URL.revokeObjectURL(url);
        ok(c.toDataURL("image/jpeg", q));
      };
      img.onerror = () => { URL.revokeObjectURL(url); fail(new Error(`${file.name} could not be read as an image.`)); };
      img.src = url;
    });
  }
  function drawThumbs() {
    $("thumbs").innerHTML = photos.map((p, i) => `<div class="thumb"><img src="${p.dataUrl}" alt="${esc(p.name)}"><span class="n">Image ${i + 1}</span><button data-i="${i}" aria-label="Remove ${esc(p.name)}">✕</button></div>`).join("");
    $("diagnosePhotos").disabled = $("clearPhotos").disabled = !photos.length;
    const total = photos.reduce((a, p) => a + p.dataUrl.length, 0);
    $("photoStatus").textContent = photos.length ? `${photos.length} photo${photos.length > 1 ? "s" : ""} ready · ${(total / 1e6).toFixed(1)} MB` : "";
  }
  let currentSample = null, liveAvailable = false;
  async function addFiles(files) {
    currentSample = null;
    document.querySelectorAll("[data-sample],[data-photo]").forEach(b => b.setAttribute("aria-pressed", "false"));
    const ok = [...files].filter(f => /^image\/(png|jpeg|webp)$/.test(f.type));
    if (ok.length < files.length) $("photoStatus").textContent = "Skipped files that aren't PNG, JPEG or WebP.";
    for (const f of ok.slice(0, MAX_PHOTOS - photos.length)) {
      try { photos.push({ name: f.name, dataUrl: await shrink(f) }); } catch (e) { $("photoStatus").textContent = e.message; }
    }
    // keep the whole request under the API's body limit by shrinking further if needed
    for (let side = 1200; photos.reduce((a, p) => a + p.dataUrl.length, 0) > BUDGET && side >= 600; side -= 200)
      for (const p of photos) p.dataUrl = await shrink(await (await fetch(p.dataUrl)).blob(), side, 0.8);
    drawThumbs();
  }
  // Drex reads a photo as soon as it arrives; the answer scrolls into view.
  const readNow = () => { if (photos.length) $("diagnosePhotos").click(); };
  $("photos").onchange = async e => { const f = [...e.target.files]; e.target.value = ""; await addFiles(f); readNow(); };
  const drop = $("drop");
  ["dragenter", "dragover"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", async e => { await addFiles(e.dataTransfer.files); readNow(); });
  $("thumbs").onclick = e => { const i = e.target.dataset.i; if (i != null) { photos.splice(+i, 1); drawThumbs(); } };
  $("clearPhotos").onclick = () => { photos.length = 0; $("photoResult").innerHTML = ""; drawThumbs(); };

  // --- Take a photo: phone camera via capture input; laptop webcam via getUserMedia ---
  let stream = null;
  const isPhone = matchMedia("(pointer: coarse)").matches;
  const stopCam = () => { stream?.getTracks().forEach(t => t.stop()); stream = null; $("cam").hidden = true; };
  $("phoneCam").onchange = async e => { const f = [...e.target.files]; e.target.value = ""; await addFiles(f); readNow(); };
  $("takePhoto").onclick = async () => {
    if (isPhone || !navigator.mediaDevices?.getUserMedia || !isSecureContext) return $("phoneCam").click();
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment", width: { ideal: 1920 } }, audio: false });
      $("camVideo").srcObject = stream; $("cam").hidden = false; $("camMsg").textContent = "Point the camera at the part, chart or shelf, then capture.";
    } catch (e) { $("phoneCam").click(); }
  };
  $("snap").onclick = () => {
    const v = $("camVideo"); if (!v.videoWidth) return;
    const c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    c.toBlob(async b => { await addFiles([new File([b], `camera-${Date.now()}.jpg`, { type: "image/jpeg" })]); $("camMsg").textContent = "Captured. Drex is reading it."; readNow(); }, "image/jpeg", 0.9);
  };
  $("camClose").onclick = stopCam;

  // --- Sample photos (illustrations drawn in the browser, so the demo works with no real fab photos) ---
  const SAMPLES = {
    scratch: { step: "DC", note: "Seen on several dies after dicing this shift", draw(g) {
      g.fillStyle = "#23272e"; g.fillRect(0, 0, 900, 900); g.fillStyle = "#8f97a3"; g.fillRect(120, 120, 660, 660);
      g.fillStyle = "#c3c9d2"; for (let i = 0; i < 8; i++) { g.fillRect(150 + i * 78, 140, 46, 46); g.fillRect(150 + i * 78, 714, 46, 46); }
      g.strokeStyle = "#5d6672"; g.lineWidth = 2; for (let y = 230; y < 690; y += 22) { g.beginPath(); g.moveTo(200, y); g.lineTo(700, y); g.stroke(); }
      g.strokeStyle = "#eef2f6"; g.lineWidth = 6; g.beginPath(); g.moveTo(160, 290); g.bezierCurveTo(350, 380, 520, 520, 770, 650); g.stroke();
      g.strokeStyle = "#ffffff88"; g.lineWidth = 2; g.beginPath(); g.moveTo(175, 305); g.bezierCurveTo(360, 395, 525, 535, 760, 662); g.stroke();
      g.fillStyle = "#fff"; g.font = "24px sans-serif"; g.fillText("Optical inspection 50x · die R12C07", 120, 90); } },
    bond: { step: "WB", note: "Pull test failures rising on bonder 3 since Monday", draw(g) {
      g.fillStyle = "#1d2127"; g.fillRect(0, 0, 900, 900); g.fillStyle = "#6c7380"; g.fillRect(260, 260, 380, 380);
      g.fillStyle = "#d9b45a"; for (let i = 0; i < 6; i++) { g.fillRect(70, 150 + i * 110, 120, 50); g.fillRect(710, 150 + i * 110, 120, 50); }
      g.fillStyle = "#c9ced6"; for (let i = 0; i < 6; i++) { g.fillRect(280, 180 + i * 85, 28, 28); g.fillRect(592, 180 + i * 85, 28, 28); }
      g.strokeStyle = "#e9c46a"; g.lineWidth = 4;
      for (let i = 0; i < 6; i++) { g.beginPath(); g.moveTo(294, 194 + i * 85); g.quadraticCurveTo(220, 120 + i * 100, 130, 175 + i * 110); g.stroke();
        if (i === 3) continue; g.beginPath(); g.moveTo(606, 194 + i * 85); g.quadraticCurveTo(680, 120 + i * 100, 770, 175 + i * 110); g.stroke(); }
      g.beginPath(); g.moveTo(770, 505); g.quadraticCurveTo(690, 380, 650, 420); g.stroke(); // lifted bond, not attached to the die pad
      g.strokeStyle = "#ff5c5c"; g.lineWidth = 3; g.beginPath(); g.arc(640, 450, 48, 0, 7); g.stroke();
      g.fillStyle = "#fff"; g.font = "24px sans-serif"; g.fillText("Wire bond inspection 20x · unit 0412", 70, 80); } },
    wip: { step: "WS", note: "Lots waiting in front of wafer probe; testers down for calibration", draw(g) {
      g.fillStyle = "#e7e9ec"; g.fillRect(0, 0, 900, 900); g.fillStyle = "#9aa1ab";
      for (let s = 0; s < 4; s++) g.fillRect(60, 180 + s * 170, 780, 14);
      g.fillRect(60, 150, 14, 700); g.fillRect(826, 150, 14, 700);
      const cols = ["#3b6fd8", "#3b6fd8", "#d8893b", "#3b6fd8"];
      for (let s = 0; s < 4; s++) for (let i = 0; i < 9; i++) { g.fillStyle = cols[(i + s) % 4]; g.fillRect(84 + i * 84, 70 + s * 170, 70, 108); g.fillStyle = "#fff"; g.fillRect(96 + i * 84, 90 + s * 170, 46, 16); }
      g.fillStyle = "#c0392b"; g.font = "bold 26px sans-serif"; g.fillText("HOLD: awaiting wafer probe", 230, 870);
      g.fillStyle = "#222"; g.font = "22px sans-serif"; g.fillText("Rack B-4 · 36 lots · avg wait 31 h", 60, 40); } },
  };
  document.querySelectorAll("[data-sample]").forEach(b => b.onclick = async () => {
    const s = SAMPLES[b.dataset.sample], c = document.createElement("canvas"); c.width = c.height = 900; s.draw(c.getContext("2d"));
    const blob = await new Promise(r => c.toBlob(r, "image/png"));
    photos.length = 0; $("photoResult").innerHTML = "";
    await addFiles([new File([blob], `sample-${b.dataset.sample}.png`, { type: "image/png" })]);
    $("photoStep").value = s.step; $("photoNote").value = s.note;
    currentSample = b.dataset.sample; // set after addFiles, which clears it
    b.setAttribute("aria-pressed", "true");
    readNow();
  });

  // --- Real sample photos (Wikimedia Commons, see assets/samples/CREDITS.md) ---
  document.querySelectorAll("[data-photo]").forEach(b => b.onclick = async () => {
    const blob = await (await fetch(b.dataset.src)).blob();
    photos.length = 0; $("photoResult").innerHTML = "";
    await addFiles([new File([blob], b.dataset.src.split("/").pop(), { type: "image/jpeg" })]);
    $("photoStep").value = ""; $("photoNote").value = "";
    currentSample = "real-" + b.dataset.photo;
    b.setAttribute("aria-pressed", "true");
    readNow();
  });

  // --- Phone link: open the same page on a phone on this Wi-Fi to snap photos ---
  fetch("api/status").then(r => r.json()).then(s => {
    if (s.lan?.length && !isPhone) { $("phoneLink").hidden = false;
      $("phoneLink").innerHTML = `On a phone on the same Wi-Fi, open <code>${esc(s.lan[0])}</code> and tap “Take a photo”.`; }
  }).catch(() => {});

  // Bring the answer into view when it's off screen (narrow windows stack it under the photo bench).
  function showAnswer() {
    const r = $("photoResult").getBoundingClientRect();
    if (r.top > innerHeight - 120 || r.bottom < 0)
      $("photoResult").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
  }
  const NICE = s => String(s ?? "—").replace(/_/g, " ");
  $("diagnosePhotos").onclick = async () => {
    $("diagnosePhotos").disabled = true; $("photoStatus").textContent = "";
    $("photoResult").innerHTML = `<p class="drex-reading">Drex is reading the photo…</p>`; showAnswer();
    try {
      const r = await fetch("api/photo", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ images: photos.map(p => p.dataUrl), step: $("photoStep").value, note: $("photoNote").value }) });
      const o = await r.json().catch(() => ({ error: "Server returned " + r.status }));
      if (!r.ok || o.error) throw new Error(o.error || "Server returned " + r.status);
      renderPhoto(o); showAnswer();
      $("photoStatus").textContent = "";
    } catch (e) {
      const rec = currentSample && RECORDED.photo?.[currentSample];
      if (rec && liveFailed(e)) { renderPhoto(rec.result, recNote(rec.at)); showAnswer(); $("photoStatus").textContent = ""; }
      else { $("photoResult").innerHTML = ""; $("photoStatus").scrollIntoView({ block: "nearest" }); $("photoStatus").textContent = /fetch|Failed|Server returned (404|405|501)/.test(e.message)
        ? (RECORDED.photo ? "This public page can’t call Drex live. Pick one of the samples to see Drex’s recorded answer, or use the team laptop for your own photos."
                          : "This public page can’t call Drex live. Live photo diagnosis runs on the team laptop.")
        : e.message; }
    } finally { $("diagnosePhotos").disabled = !photos.length; }
  };
  // Plain-English names for Drex's option keys.
  const SAY = {
    wafer_or_die: "Wafer or die", package_or_bond: "Package or bonds", spc_or_data: "Chart or data", wip_or_queue: "Lots waiting",
    floor_or_layout: "Floor layout", equipment: "Equipment", other: "Something else",
    none: "No defect", particle_contamination: "Particles", scratch_or_handling: "Scratch", pattern_or_litho: "Pattern defect",
    crack_or_chipping: "Crack or chip", bond_defect: "Bond defect", void_or_delamination: "Void", corrosion_or_discoloration: "Corrosion",
    transportation: "Transportation", inventory: "Inventory", motion: "Motion", waiting: "Waiting", overprocessing: "Over-processing",
    overproduction: "Over-production", defects: "Defects", skills: "Unused skills",
    conwip_dbr: "CONWIP pacing", recenter_spc: "Re-center + SPC", doe_variation: "DOE on variation", root_cause_8d: "8D root cause",
    sampling_plan: "Sampling plan", standard_work: "Standard work", relayout_amhs: "Re-route moves",
    release: "Release", hold: "Hold", rework: "Rework",
  };
  const say = v => SAY[v] || NICE(v);
  const pct = c => c == null ? "" : `${Math.round(c * 100)}%`;

  function renderPhoto(o, note = "") {
    LAST.photo = { ...o, recorded: !!note, sample: currentSample, name: photos[0]?.name };
    const a = o.answers, c = o.confidence || {};
    const defect = a.defect_type && a.defect_type !== "none";
    const act = { release: "Release the lots.", hold: "Hold the lots for review.", rework: "Rework the lots." }[a.disposition] || "";
    const headline = defect ? `${say(a.defect_type)} found.` : "No defect in sight.";
    const wasteSure = c.root_waste == null || c.root_waste >= 0.5;
    const sub = defect ? `${act} Run ${say(a.countermeasure)}.`
                       : wasteSure ? `${act} The waste here is ${say(a.root_waste).toLowerCase()}: run ${say(a.countermeasure)}.` : `${act} Drex can’t name a waste with confidence.`;
    const sev = a.severity ? `<span class="sev sev-${String(a.severity).toLowerCase()}">${esc(a.severity)} severity</span>` : "";
    const lowKeys = Object.entries(c).filter(([, v]) => v != null && v < 0.5).map(([k]) => ({ subject: "what it shows", defect_type: "defect", root_waste: "waste", countermeasure: "fix", disposition: "lots", severity: "severity" }[k] || k));
    const row = (label, key) => `<div class="fact"><span class="fl">${label}</span><span class="fv">${esc(key === "severity" ? a[key] : say(a[key]))}</span>
        ${c[key] != null ? `<span class="fc"><i style="width:${c[key] * 100}%"></i></span><span class="fp${c[key] < 0.5 ? " low" : ""}">${pct(c[key])}</span>` : `<span></span><span></span>`}</div>`;
    $("photoResult").innerHTML = `
      <div class="call-card${defect ? " bad" : " good"}">
        <div class="call-top"><span class="call-k">Drex’s call</span>${sev}</div>
        <p class="call-h">${esc(headline)}</p>
        <p class="call-s">${esc(sub)}</p>
      </div>
      <div class="facts">
        ${row("What it shows", "subject")}${row("Defect", "defect_type")}${row("Lean waste", "root_waste")}
        ${row("Fix", "countermeasure")}${row("Lots", "disposition")}${row("Severity", "severity")}
      </div>
      <p class="call-meta">${esc(o.model)} · ${o.latencyMs} ms · $${o.costUsd.toFixed(5)}</p>
      ${lowKeys.length ? `<p class="call-warn">Unsure about ${esc(lowKeys.join(", "))}. An engineer should look before anyone acts on those.</p>` : ""}${note}
      ${o.fallback === "claude" ? `<p class="recnote">Drex wasn’t reachable from this network, so ${esc(o.model)} answered the same questions as a fallback. It doesn’t return confidence.</p>` : ""}`;
  }

  // ---------- Swarm: 12 station agents + a dispatcher, every call by Drex ----------
  const WASTE_SAY = { transportation: "Transportation", inventory: "Inventory", motion: "Motion", waiting: "Waiting", overprocessing: "Over-processing", overproduction: "Over-production", defects: "Defects", skills: "Unused skills" };
  function drawSwarm(sw, state = "done") {
    const pick = Object.fromEntries((sw?.picks || []).map(p => [p.step, p]));
    $("swarmBoard").innerHTML = F.STEPS.map(st => {
      const r = sw?.reports?.find(x => x.step === st.id);
      if (!r) return `<div class="st ${state === "thinking" ? "thinking" : "pending"}"><span class="sid">${st.id}</span><span class="sname">${esc(st.name)}</span><span class="sprob">${state === "thinking" ? "asking Drex…" : "waiting to run"}</span></div>`;
      const level = r.needsHelp >= 0.5 || r.ask === "engineer_now" ? (r.urgencyScore >= 2 || r.ask === "engineer_now" ? "hot" : "watch") : "calm";
      const prob = level === "calm" ? "Running normally" : WASTE_SAY[r.problem] || r.problem;
      const p = pick[r.step];
      return `<div class="st ${level}">${p ? `<span class="eng">ENGINEER ${p.engineer}</span>` : ""}<span class="sid">${r.step}</span><span class="sname">${esc(r.name)}</span>
        <span class="sprob">${esc(prob)}</span><span class="shelp" title="Needs help ${Math.round(r.needsHelp * 100)}%"><i style="width:${Math.round(r.needsHelp * 100)}%"></i></span>
        <span class="surg">${level === "calm" ? "No help needed" : esc(r.urgency)} · ${Math.round(r.needsHelp * 100)}%</span></div>`;
    }).join("");
  }
  function drawDispatch(sw, note = "") {
    if (sw?.picks) { LAST.swarm = sw; LAST.recorded.swarm = !!note; }
    if (!sw?.picks) { $("swarmDispatch").innerHTML = note; return; }
    const lines = sw.picks.map(p => { const r = sw.reports.find(x => x.step === p.step);
      return `<p>Engineer ${p.engineer} → <b>${esc(p.name)}</b> <span class="sub">${esc(WASTE_SAY[r?.problem] || "")}, ${esc(r?.urgency || "")}${p.confidence != null ? ` · Drex ${Math.round(p.confidence * 100)}% sure` : ""}</span></p>`; }).join("");
    const unsent = sw.reports.filter(r => (r.needsHelp >= 0.5 || r.ask === "engineer_now") && !sw.picks.some(p => p.step === r.step));
    $("swarmDispatch").innerHTML = `<div class="dispatch"><span class="call-k">Dispatcher · Drex’s call</span>${lines || "<p>No station needs an engineer this shift.</p>"}
      ${unsent.length ? `<p class="sub">Also asked for help, no engineer left: ${esc(unsent.map(r => r.name).join(", "))}.</p>` : ""}
      <p class="call-meta">${sw.drexCalls} Drex calls · ${(sw.wallMs / 1000).toFixed(1)} s · $${sw.costUsd.toFixed(4)} · ${esc(sw.model)}</p></div>${note}`;
  }
  drawSwarm(null);
  // "Watch the swarm" jumps to the board and runs it on a week with a real fault, so there's something to see.
  document.querySelectorAll("[data-run-swarm]").forEach(a => a.addEventListener("click", e => {
    e.preventDefault();
    const sel = $("scenario");
    if (sel.value === "baseline" || sel.value === "improved") { sel.value = "backend"; sel.dispatchEvent(new Event("change")); }
    // scroll after the charts above have re-drawn, so the board doesn't move out from under the jump
    // (a plain timer, not requestAnimationFrame: rAF never fires in a background tab)
    setTimeout(() => {
      if (!$("runSwarm").disabled) $("runSwarm").click();
      $("swarm").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
      history.replaceState(null, "", "#swarm");
    }, 200);
  }));
  $("runSwarm").onclick = async () => {
    const btn = $("runSwarm"); btn.disabled = true; $("swarmStatus").textContent = "12 station agents are asking Drex…";
    drawSwarm(null, "thinking"); $("swarmDispatch").innerHTML = "";
    try {
      const r = await fetch("api/swarm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rows, engineers: $("swarmEngineers").value }) });
      const sw = await r.json().catch(() => ({ error: "Server returned " + r.status }));
      if (!r.ok || sw.error) throw new Error(sw.error || "Server returned " + r.status);
      drawSwarm(sw); drawDispatch(sw); $("swarmStatus").textContent = "";
    } catch (e) {
      const rec = RECORDED.swarm?.[$("scenario").value];
      if (rec && liveFailed(e)) { drawSwarm(rec.result); drawDispatch(rec.result, recNote(rec.at)); $("swarmStatus").textContent = ""; }
      else { drawSwarm(null); $("swarmStatus").textContent = /fetch|Failed|Server returned (404|405|501)/.test(e.message) ? "The swarm runs live on the team laptop." : e.message; }
    } finally { btn.disabled = false; }
  };

  // ---------- Shift report: facts, Drex's findings, what to change; PDF, send, store ----------
  const SCEN_NAME = { baseline: "Baseline week", litho: "Litho bottleneck + CD drift", backend: "Wire-bond defect spike", improved: "After kaizen" };
  const money0 = v => "$" + Math.round(v).toLocaleString();
  function buildReport() {
    const res = LAST.res || F.analyze(rows, costs);
    const k = res.kpi, total = res.grand, lots = res.lotCount;
    const top = res.pareto.slice(0, 3).map(p => ({ name: p.name, perLot: p.value / lots, share: p.value / total }));
    const weak = res.steps.filter(s => s.cpk < 1.33).sort((a, b) => a.cpk - b.cpk).slice(0, 4)
      .map(s => ({ step: s.name, param: s.spec.param, cpk: s.cpk, oos: s.oos }));
    const worstStep = [...res.steps].sort((a, b) => b.wasteTotal - a.wasteTotal)[0];
    // What to change: the DMAIC actions, ranked by the waste dollars at their step, with a conservative saving estimate.
    const SAVE = { W: 0.3, I: 0.3, D: 0.4, T: 0.25, O: 0.5, M: 0.2, P: 0.3, S: 0.3 }; // share of that waste a first fix typically removes (assumption)
    const KEY = { Waiting: "W", Inventory: "I", Defects: "D", Transportation: "T", "Over-processing": "O", Motion: "M", "Over-production": "P", "Skills (unused talent)": "S" };
    const changes = res.recommendations.map(r => {
      const st = res.steps.find(s => s.name === r.step); const key = KEY[F.WASTES[r.waste]?.name] || r.waste;
      const at = st ? st.waste[key] || 0 : 0;
      return { step: r.step, waste: F.WASTES[r.waste]?.name || r.waste, phase: r.phase, action: r.text, savePerLot: at * (SAVE[key] || 0.25) / lots };
    }).sort((a, b) => b.savePerLot - a.savePerLot);
    return {
      title: "Muda Lens shift report", when: new Date(), week: SCEN_NAME[$("scenario").value] || "Uploaded lot data", lots,
      kpi: [["Process cycle efficiency", (k.pce * 100).toFixed(1) + "%"], ["Lead time", k.leadDays.toFixed(1) + " days"], ["Rolled throughput yield", (k.rty * 100).toFixed(1) + "%"],
            ["Sigma level", k.sigma.toFixed(2) + "σ"], ["DPMO per step", Math.round(k.dpmo).toLocaleString()], ["Waste cost per lot", money0(k.wastePerLot)], ["Constraint", res.bottleneck.name]],
      facts: [
        `Only ${(k.pce * 100).toFixed(0)}% of a lot’s ${k.leadDays.toFixed(1)}-day lead time is value-add work; the rest is waiting, moving and re-checking.`,
        `The biggest waste is ${top[0].name.toLowerCase()}: ${money0(top[0].perLot)} per lot, ${(top[0].share * 100).toFixed(0)}% of all waste. The top three wastes are ${(top.reduce((a, t) => a + t.share, 0) * 100).toFixed(0)}% of it.`,
        `${res.bottleneck.name} is the constraint, with lots queuing ${(res.bottleneck.queue / 60).toFixed(1)} h on average before the tool.`,
        `${weak.length} of 12 steps can’t reliably hold spec (Cpk under 1.33); the weakest is ${weak[0] ? `${weak[0].step} (${weak[0].param}, Cpk ${weak[0].cpk.toFixed(2)})` : "none"}.`,
        `${worstStep.name} carries the most waste cost of any single step: ${money0(worstStep.wasteTotal / lots)} per lot.`,
      ],
      top, weak, changes,
      drex: LAST.decide, drexRecorded: LAST.recorded.decide, swarm: LAST.swarm, swarmRecorded: LAST.recorded.swarm, photo: LAST.photo,
    };
  }
  function drexLine(r) {
    if (!r.drex) return "Not run this shift. Click “Ask Drex” in “Drex decides”.";
    const a = r.drex.answers, c = r.drex.confidence || {}, sure = Math.min(c.root_waste ?? 1, c.culprit_step ?? 1);
    if (sure < 0.5) return `Nothing clearly wrong. Drex’s best guess is only ${pct(sure)} sure.`;
    return `${say(a.root_waste)} at ${stepName(a.culprit_step)} (${pct(sure)} sure). ${({ release: "Release", hold: "Hold", rework: "Rework" })[a.disposition]} the lots; run ${say(a.countermeasure)}.${r.drexRecorded ? " (recorded run)" : ""}`;
  }
  function swarmLine(r) {
    if (!r.swarm) return "Not run this shift. Click “Run the swarm”.";
    if (!r.swarm.picks.length) return `No station asked for an engineer (${r.swarm.drexCalls} Drex calls).`;
    return r.swarm.picks.map(p => `Engineer ${p.engineer} → ${p.name}`).join("; ") + `. ${r.swarm.drexCalls} Drex calls.${r.swarmRecorded ? " (recorded run)" : ""}`;
  }
  function photoLine(r) {
    if (!r.photo) return "No photo reviewed this shift.";
    const a = r.photo.answers; const d = a.defect_type && a.defect_type !== "none";
    return `${d ? say(a.defect_type) + " found" : "No defect seen"} on ${String(r.photo.name || "the photo")}. ${({ release: "Release", hold: "Hold", rework: "Rework" })[a.disposition] || ""} the lots.${r.photo.recorded ? " (recorded run)" : ""}`;
  }
  function renderReport() {
    const r = buildReport();
    $("reportPreview").innerHTML = `
      <div class="rp-head"><div><p class="call-k">Shift report · ${esc(r.week)}</p><p class="rp-title">What the line is telling us</p></div>
        <p class="call-meta">${esc(r.when.toLocaleString())} · ${r.lots} lots</p></div>
      <div class="rp-kpis">${r.kpi.map(([l, v]) => `<div><span class="fl">${esc(l)}</span><b>${esc(v)}</b></div>`).join("")}</div>
      <p class="rp-h">The facts</p><ol class="rp-facts">${r.facts.map(f => `<li>${esc(f)}</li>`).join("")}</ol>
      <p class="rp-h">Drex this shift</p>
      <ul class="rp-drex"><li><b>Line call:</b> ${esc(drexLine(r))}</li><li><b>Swarm:</b> ${esc(swarmLine(r))}</li><li><b>Photo:</b> ${esc(photoLine(r))}</li></ul>
      <p class="rp-h">What to change in production</p>
      <div class="tbl"><table class="rp-table"><tr><th>#</th><th>Where</th><th>Change</th><th>Phase</th><th>Est. saving / lot</th></tr>
        ${r.changes.slice(0, 6).map((c, i) => `<tr><td>${i + 1}</td><td>${esc(c.step)}<br><span class="fl">${esc(c.waste)}</span></td><td style="white-space:normal;text-align:left">${esc(c.action)}</td><td>${esc(c.phase)}</td><td>${money0(c.savePerLot)}</td></tr>`).join("")}</table></div>
      <p class="call-meta">Savings assume a first fix removes 20–50% of that waste at that step. Data is synthetic, modelled on a power-MOSFET line.</p>`;
    return r;
  }

  function makePdf(r) {
    const { jsPDF } = window.jspdf; const doc = new jsPDF({ unit: "pt", format: "a4" });
    const W = doc.internal.pageSize.getWidth(), M = 48; let y = 44;
    const ink = [28, 23, 18], rust = [156, 52, 31], soft = [78, 71, 63];
    // The PDF's built-in fonts only cover Latin-1, so spell out symbols they can't draw.
    const safe = s => String(s).replace(/σ/g, " sigma").replace(/→/g, "->").replace(/≥/g, ">=").replace(/≤/g, "<=").replace(/[“”]/g, '"').replace(/[‘’]/g, "'").replace(/–/g, "-").replace(/—/g, " - ").replace(/·/g, "|").replace(/µ/g, "u");
    const text = (s, size = 10, color = ink, style = "normal", font = "helvetica") => {
      doc.setFont(font, style); doc.setFontSize(size); doc.setTextColor(...color);
      const lines = doc.splitTextToSize(safe(s), W - 2 * M);
      doc.text(lines, M, y + size * 0.8);           // y is the top of the line box; jsPDF draws at the baseline
      y += lines.length * size * 1.3;
    };
    const cell = v => safe(v);
    const need = h => { if (y + h > doc.internal.pageSize.getHeight() - 56) { doc.addPage(); y = 56; } };
    doc.setFillColor(...rust); doc.rect(0, 0, W, 6, "F");
    text("MUDA LENS · SHIFT REPORT", 9, rust, "bold"); y += 2;
    text("What the line is telling us", 22, ink, "bold", "times");
    text(`${r.week} · ${r.lots} lots · ${r.when.toLocaleString()}`, 9, soft); y += 6;
    doc.autoTable({ startY: y, margin: { left: M, right: M }, head: [r.kpi.map(k => cell(k[0]))], body: [r.kpi.map(k => cell(k[1]))], theme: "plain",
      styles: { fontSize: 8, cellPadding: 4, textColor: ink }, headStyles: { textColor: soft, fontStyle: "normal", fontSize: 7 }, bodyStyles: { fontStyle: "bold", fontSize: 10 } });
    y = doc.lastAutoTable.finalY + 12;
    text("The facts", 13, rust, "bold", "times"); y += 2; r.facts.forEach((f, i) => { need(26); text(`${i + 1}.  ${f}`, 9.5); y += 1; });
    y += 6; need(70); text("Drex this shift", 13, rust, "bold", "times"); y += 2;
    [["Line call", drexLine(r)], ["Swarm", swarmLine(r)], ["Photo", photoLine(r)]].forEach(([l, v]) => { need(24); text(`${l}: ${v}`, 9.5); y += 1; });
    y += 6; need(60); text("What to change in production", 13, rust, "bold", "times"); y += 2;
    doc.autoTable({ startY: y, margin: { left: M, right: M }, head: [["#", "Where", "Change", "Phase", "Saving / lot", "Owner"]],
      body: r.changes.slice(0, 6).map((c, i) => [i + 1, cell(`${c.step}\n${c.waste}`), cell(c.action), c.phase, money0(c.savePerLot), ""]),
      styles: { fontSize: 8, cellPadding: 4, textColor: ink, lineColor: [213, 201, 182], lineWidth: 0.5 }, headStyles: { fillColor: [28, 23, 18], textColor: [246, 240, 230] },
      columnStyles: { 0: { cellWidth: 18 }, 1: { cellWidth: 90 }, 3: { cellWidth: 50 }, 4: { cellWidth: 56, halign: "right" }, 5: { cellWidth: 58 } }, theme: "grid" });
    y = doc.lastAutoTable.finalY + 10;
    if (r.weak.length) { need(70); text("Steps that can't hold spec (Cpk < 1.33)", 10.5, ink, "bold"); y += 2;
      doc.autoTable({ startY: y, margin: { left: M, right: M }, head: [["Step", "Critical parameter", "Cpk", "Out of spec"]],
        body: r.weak.map(w => [w.step, cell(w.param), w.cpk.toFixed(2), (w.oos * 100).toFixed(1) + "%"]), styles: { fontSize: 8, cellPadding: 3 }, headStyles: { fillColor: [230, 220, 203], textColor: ink }, theme: "grid" });
      y = doc.lastAutoTable.finalY + 10; }
    need(24); text("Savings assume a first fix removes 20-50% of that waste at that step. Data is synthetic, modelled on a power-MOSFET line. Drex decisions come from drex-max-v1.1; an engineer owns every call.", 7.5, soft);
    return doc;
  }
  const reportName = r => `muda-lens-shift-report-${r.when.toISOString().slice(0, 16).replace(/[:T]/g, "-")}.pdf`;
  let lastReport = null;
  $("buildReport").onclick = () => { lastReport = renderReport(); ["dlReport", "sendReport", "storeReport"].forEach(id => $(id).disabled = false); $("reportStatus").textContent = ""; };
  $("dlReport").onclick = () => { const r = lastReport || renderReport(); makePdf(r).save(reportName(r)); $("reportStatus").textContent = "PDF downloaded."; };
  $("sendReport").onclick = async () => {
    const r = lastReport || renderReport(); const doc = makePdf(r); const name = reportName(r);
    const file = new File([doc.output("blob")], name, { type: "application/pdf" });
    const to = $("reportTo").value.split(/[,;\s]+/).filter(s => /.+@.+\..+/.test(s));
    const summary = `Shift report (${r.week}):\n\n${r.facts.map((f, i) => `${i + 1}. ${f}`).join("\n")}\n\nDrex: ${drexLine(r)}\nSwarm: ${swarmLine(r)}\n\nTop change: ${r.changes[0]?.step}: ${r.changes[0]?.action}`;
    if (navigator.canShare?.({ files: [file] })) {
      try { await navigator.share({ title: "Muda Lens shift report", text: summary, files: [file] }); $("reportStatus").textContent = "Shared."; return; } catch (e) { if (e.name === "AbortError") return; }
    }
    doc.save(name);
    location.href = `mailto:${encodeURIComponent(to.join(","))}?subject=${encodeURIComponent("Muda Lens shift report: " + r.week)}&body=${encodeURIComponent(summary + "\n\n(PDF attached: " + name + ")")}`;
    $("reportStatus").textContent = `PDF downloaded as ${name}. Your mail app opened with the summary; attach the PDF and send.`;
  };
  $("storeReport").onclick = async () => {
    const r = lastReport || renderReport(); const name = reportName(r); const btn = $("storeReport"); btn.disabled = true;
    $("reportStatus").textContent = "Saving to the cloud…";
    try {
      const b64 = makePdf(r).output("datauristring").split(",")[1];
      const res = await fetch("api/report/store", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pdfBase64: b64, filename: name }) });
      const o = await res.json().catch(() => ({ error: "Server returned " + res.status }));
      if (!res.ok || o.error) throw new Error(o.error || "Server returned " + res.status);
      $("reportStatus").innerHTML = `Saved to the Nace cloud: “${esc(o.workspace)}” / ${esc(o.path)} (${Math.round(o.bytes / 1024)} KB).` +
        (o.download_url ? ` <a href="${esc(o.download_url)}" target="_blank" rel="noopener">Open the stored PDF</a> · <button type="button" class="linkish" id="copyCloud">Copy link for engineers</button>` : "");
      if (o.download_url) $("copyCloud").onclick = async () => {
        try { await navigator.clipboard.writeText(o.download_url); $("copyCloud").textContent = "Link copied"; }
        catch { $("copyCloud").textContent = "Copy failed: right-click the link instead"; }
      };
    } catch (e) {
      $("reportStatus").textContent = /fetch|Failed|Server returned (404|405|501)/.test(e.message) ? "Saving to the cloud runs on the team laptop. Download the PDF instead." : e.message;
    } finally { btn.disabled = false; }
  };

  // ---------- Arena (benchmark results) ----------
  fetch("bench/results.json").then(r => r.ok ? r.json() : null).then(res => {
    if (!res) return;
    const cs = Object.entries(res.contenders);
    const kinds = Object.keys(cs.find(([, c]) => c.byKind)?.[1].byKind || {});
    $("arena").innerHTML = `<p class="sub">${res.cases} cases · generated ${new Date(res.generatedAt).toLocaleString()}</p><div class="tbl"><table>
      <tr><th>Contender</th><th>All 4 correct</th>${Q.map(q => `<th>${q.replace("_", " ")}</th>`).join("")}<th>p50 latency</th><th>$ / 1k decisions</th></tr>
      ${cs.map(([, c]) => c.skipped ? `<tr><td>${esc(c.label)}</td><td colspan="7" style="text-align:left" class="sub">not run (no API key configured)</td></tr>` :
        `<tr><td><b>${esc(c.label)}</b></td><td>${pill(c.exact * 100, 85, 70, x => x.toFixed(1) + "%")}</td>${Q.map(q => `<td>${(c.perQuestion[q] * 100).toFixed(0)}%</td>`).join("")}
         <td>${c.latencyMsP50} ms</td><td>$${c.costPer1kDecisionsUsd.toFixed(3)}</td></tr>`).join("")}
      </table></div>
      <div class="tbl" style="margin-top:10px"><table><tr><th>Accuracy by fault type</th>${kinds.map(k => `<th>${k.replace("_", " ")}</th>`).join("")}</tr>
      ${cs.filter(([, c]) => !c.skipped).map(([, c]) => `<tr><td>${esc(c.label)}</td>${kinds.map(k => `<td>${c.byKind[k].exact}/${c.byKind[k].n}</td>`).join("")}</tr>`).join("")}</table></div>`;
  }).catch(() => {});

  loadScenario();
})();
