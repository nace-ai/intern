// Shared Drex calls: the decision questions and the System One client.
// Used by the web server, the MCP server (for Intern) and the benchmark.

const PROVIDERS = {
  // Nace console (default)
  nace: { url: "https://console.nace.ai/v1/systemone", keyEnv: "NACE_API_KEY", model: "drex-max-v1.1" },
  // DeepInfra hosts the open Drex 2.0 weights behind the same System One API (text only)
  deepinfra: { url: "https://api.deepinfra.com/v1/systemone", keyEnv: "DEEPINFRA_API_KEY", model: "nace-ai/drex-v2.0" },
};

export const AIS = ["none", "minor (AIS 1)", "moderate (AIS 2)", "serious (AIS 3)", "severe (AIS 4)", "critical (AIS 5)"];

export const ORGANS = {
  brain: "Brain: concussion, contusion or intracranial bleed",
  skull_face: "Skull and facial bones",
  cervical_spine: "Cervical spine and spinal cord (neck)",
  ribs: "Ribs and sternum fractures",
  lungs: "Lungs: contusion, pneumothorax, hemothorax",
  heart_aorta: "Heart and thoracic aorta (blunt cardiac injury, aortic tear)",
  liver: "Liver laceration",
  spleen: "Spleen laceration",
  kidneys: "Kidneys",
  bowel: "Bowel and mesentery (seat-belt sign injuries)",
  pelvis: "Pelvis fracture",
  femur: "Femur and hip (knee-thigh-hip complex)",
  lower_leg: "Tibia, fibula, ankle and foot",
  arms: "Arms, wrists, clavicle",
};

/** The 20 typed questions Drex answers for one patient: 14 organ severities + 6 ER calls. */
export function decisionQuestions() {
  const q = {};
  for (const [id, desc] of Object.entries(ORGANS)) {
    q[id] = { type: "score", instructions: `Most likely worst injury severity for: ${desc}.`, criteria: AIS };
  }
  q.destination = {
    type: "choice",
    instructions: "Where should EMS take this patient?",
    criteria: {
      level1_trauma: "Level I trauma center: life- or limb-threatening injury likely",
      level2_trauma: "Level II trauma center: serious but stable injuries likely",
      community_er: "Nearest community emergency department",
      urgent_care: "Urgent care or release at scene",
    },
  };
  q.trauma_activation = { type: "noul", instructions: "Should the ER activate the full trauma team before arrival?" };
  q.ct_head = { type: "noul", instructions: "Is a head CT indicated on arrival?" };
  q.ct_cspine = { type: "noul", instructions: "Is cervical spine imaging indicated?" };
  q.fast_exam = { type: "noul", instructions: "Is a FAST ultrasound for internal abdominal bleeding indicated?" };
  q.blood_ready = { type: "noul", instructions: "Should the blood bank prepare units for transfusion?" };
  return q;
}

/** One System One request. `images` (data URLs) needs Drex Max on the Nace console. */
export async function drex({ state, questions, images, model, provider = process.env.DREX_PROVIDER || "nace" }) {
  const p = PROVIDERS[images ? "nace" : provider] ?? PROVIDERS.nace;
  const key = process.env[p.keyEnv];
  if (!key) throw new Error(`${p.keyEnv} missing`);
  const body = { model: model || process.env.DREX_MODEL || p.model, state, questions };
  if (images) body.images = images;
  const t0 = performance.now();
  const r = await fetch(p.url, { method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const roundTripMs = performance.now() - t0;
  const json = await r.json();
  if (!r.ok) throw new Error(`Drex ${r.status}: ${JSON.stringify(json.error ?? json).slice(0, 300)}`);
  return { ...json, roundTripMs, cost: ((json.usage?.input_tokens ?? 0) * 0.03) / 1e6 };
}

/** Compact, readable triage from a Drex answer set. */
export function summarize(answers) {
  const serious = (pr) => [3, 4, 5].reduce((s, k) => s + (pr?.[k] ?? 0), 0);
  const organs = Object.keys(ORGANS)
    .map((id) => ({ organ: id, p_serious: +serious(answers[id]?.probabilities).toFixed(3) }))
    .sort((a, b) => b.p_serious - a.p_serious);
  const d = answers.destination;
  return {
    destination: d.choice,
    destination_probability: +(d.probabilities[d.choice] ?? 0).toFixed(3),
    trauma_team: +answers.trauma_activation.noul.toFixed(3),
    ct_head: +answers.ct_head.noul.toFixed(3),
    ct_cspine: +answers.ct_cspine.noul.toFixed(3),
    fast_exam: +answers.fast_exam.noul.toFixed(3),
    blood_ready: +answers.blood_ready.noul.toFixed(3),
    organs,
  };
}
