// Photo diagnosis: an engineer uploads photos from the line (die/wafer defect, wire bond,
// SPC chart screenshot, WIP shelf, shop-floor layout...) and Drex Max reads the images
// and answers typed questions about them in one pass.
const { QUESTIONS } = require("../bench/cases.js");
const { drexCall, drexCfg } = require("../bench/contenders.js");
const F = require("./engine.js");

const PHOTO_QUESTIONS = {
  subject: {
    type: "choice",
    instructions: "What do the images mainly show?",
    criteria: {
      wafer_or_die: "A wafer, die, or wafer map seen up close (microscope, inspection or AOI image)",
      package_or_bond: "A packaged part, lead frame, wire bonds, solder, or mold compound",
      spc_or_data: "A control chart, dashboard, spreadsheet, or other data screenshot",
      wip_or_queue: "Lots, carriers/FOUPs, trays, or parts waiting on shelves, carts or racks",
      floor_or_layout: "A production floor, tool layout, walking path, or material route",
      equipment: "A process tool, fixture, or machine component",
      other: "Something else",
    },
  },
  defect_type: {
    type: "choice",
    instructions: "If a product defect is visible, which type is it? Answer none if no product defect is visible.",
    criteria: {
      none: "No product defect is visible",
      particle_contamination: "Particles, residue, stains, or contamination on the surface",
      scratch_or_handling: "Scratches, scuffs, or handling damage",
      pattern_or_litho: "Pattern distortion, misalignment, bridging, or missing features",
      crack_or_chipping: "Cracks, chipping, or broken edges (die, wafer, or package)",
      bond_defect: "Lifted, broken, misplaced, or deformed wire bonds or solder joints",
      void_or_delamination: "Voids, bubbles, or delamination in mold, attach, or solder",
      corrosion_or_discoloration: "Corrosion, oxidation, or discoloration",
    },
  },
  root_waste: { type: "choice", instructions: "Which of the 8 Lean wastes (TIMWOODS) do the images point to most?", criteria: QUESTIONS.root_waste.options },
  countermeasure: { type: "choice", instructions: QUESTIONS.countermeasure.instructions, criteria: QUESTIONS.countermeasure.options },
  disposition: { type: "choice", instructions: "Based on the images, what should happen to the affected lots?", criteria: QUESTIONS.disposition.options },
  severity: { type: "score", instructions: "How severe is the problem shown for product quality or line output?", criteria: ["Low", "Medium", "High", "Critical"] },
};

// Fallback when the Drex API can't be reached (e.g. a venue network blocked by its firewall):
// the same questions and options, answered by Claude's vision model, labelled as such.
async function claudePhoto({ state, images }) {
  const Anthropic = require("@anthropic-ai/sdk");
  const client = new (Anthropic.default || Anthropic)();
  const model = process.env.CLAUDE_MODEL || "claude-opus-5-5";
  const props = Object.fromEntries(Object.entries(PHOTO_QUESTIONS).map(([k, q]) =>
    [k, { type: "string", enum: q.type === "score" ? q.criteria : Object.keys(q.criteria) }]));
  const qs = Object.entries(PHOTO_QUESTIONS).map(([k, q]) => `${k}: ${q.instructions}\n` +
    (q.type === "score" ? `  levels: ${q.criteria.join(", ")}` : Object.entries(q.criteria).map(([o, d]) => `  - ${o}: ${d}`).join("\n"))).join("\n\n");
  const content = [
    ...images.map(d => { const [, mt, data] = d.match(/^data:(image\/[a-z]+);base64,(.*)$/); return { type: "image", source: { type: "base64", media_type: mt, data } }; }),
    { type: "text", text: `You are a Lean Six Sigma black belt in a semiconductor fab. The images above are Image 1..${images.length}.\nContext: ${JSON.stringify(state)}\nAnswer each question with exactly one option.\n\n${qs}` },
  ];
  const t0 = Date.now();
  const msg = await client.beta.messages.create({
    model, max_tokens: 16000, betas: ["server-side-fallback-2026-07-01"], fallbacks: "default",
    output_config: { effort: "low", format: { type: "json_schema", schema: { type: "object", additionalProperties: false, required: Object.keys(props), properties: props } } },
    messages: [{ role: "user", content }],
  });
  if (msg.stop_reason === "refusal") throw new Error("Claude declined to answer.");
  const answers = JSON.parse(msg.content.filter(b => b.type === "text").map(b => b.text).join(""));
  const u = msg.usage;
  return { answers, confidence: {}, model, latencyMs: Date.now() - t0, inputTokens: u.input_tokens,
           costUsd: (u.input_tokens * 4 + u.output_tokens * 20) / 1e6 };
}

async function diagnosePhoto(input) {
  try { return await diagnosePhotoDrex(input); }
  catch (e) {
    const unreachable = /HTTP (403|429|5\d\d)|firewall|fetch failed|ECONN|ENOTFOUND|ETIMEDOUT/i.test(e.message);
    const canClaude = !!(process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_AUTH_TOKEN);
    if (!unreachable || !canClaude) throw e;
    const { images, note = "", step = "" } = input;
    const st = F.STEPS.find(s => s.id === step);
    const state = { process_step: st ? st.name : "not specified", engineer_note: String(note).slice(0, 2000) || "none",
                    plant_rule: "Product-quality risk outranks flow waste. If no product defect is visible, judge the flow waste shown." };
    const r = await claudePhoto({ state, images });
    return { ...r, fallback: "claude", fallbackReason: "Drex API unreachable from this network",
             labels: Object.fromEntries(Object.entries(PHOTO_QUESTIONS).map(([k, q]) => [k, q.type === "score" ? null : q.criteria])) };
  }
}

async function diagnosePhotoDrex({ images, note = "", step = "" }) {
  if (!Array.isArray(images) || !images.length) throw new Error("Add at least one photo.");
  if (images.length > 8) throw new Error("Drex reads up to 8 photos at a time.");
  if (!images.every(s => typeof s === "string" && /^data:image\/(png|jpeg|webp);base64,/.test(s)))
    throw new Error("Photos must be PNG, JPEG or WebP.");
  if (!/^drex-max|^drex-latest/.test(drexCfg().model)) throw new Error("Photo diagnosis needs DREX_MODEL=drex-max-v1.1.");
  const st = F.STEPS.find(s => s.id === step);
  const state = {
    context: "Photos from a power-semiconductor wafer-to-package line, uploaded by an engineer for a Lean Six Sigma review.",
    images: images.map((_, i) => `Image ${i + 1}`).join(", "),
    process_step: st ? `${st.name} (${st.area})` : "not specified",
    engineer_note: String(note).slice(0, 2000) || "none",
    plant_rule: "Product-quality risk outranks flow waste. If no product defect is visible, judge the flow waste shown.",
  };
  const t0 = Date.now();
  const body = await drexCall({ state, questions: PHOTO_QUESTIONS, images });
  const a = body.answers || {};
  const out = { answers: {}, confidence: {} };
  for (const [k, q] of Object.entries(PHOTO_QUESTIONS)) {
    if (q.type === "score") {
      const lvl = Math.round(a[k]?.score ?? 0);
      out.answers[k] = q.criteria[Math.max(0, Math.min(q.criteria.length - 1, lvl))];
    } else out.answers[k] = a[k]?.choice;
    out.confidence[k] = a[k]?.confidence;
  }
  const inTok = body.usage?.input_tokens || 0;
  return { ...out, model: body.model, latencyMs: Date.now() - t0, inputTokens: inTok, imageTokens: body.usage?.image_tokens,
           costUsd: inTok / 1e6 * drexCfg().pricePerMTok,
           labels: Object.fromEntries(Object.entries(PHOTO_QUESTIONS).map(([k, q]) => [k, q.type === "score" ? null : q.criteria])) };
}

module.exports = { PHOTO_QUESTIONS, diagnosePhoto };
