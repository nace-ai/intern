// NDI (Nace Data Intelligence): read a crash / EMS report (PDF, scan, photo) and pull out the
// facts the triage needs. Upload → Extract with a JSON Schema → fields with a found/not-found status.
const BASE = "https://console.nace.ai";

export const INTAKE_SCHEMA = {
  type: "object",
  properties: {
    age: { type: "number", description: "Driver's age in years." },
    sex: { type: "string", enum: ["male", "female", "unknown"] },
    belted: { type: "boolean", description: "True if the driver was wearing a seat belt." },
    airbag_deployed: { type: "boolean", description: "True if the driver's frontal airbag deployed." },
    estimated_speed_kmh: { type: "number", description: "Estimated impact speed in km/h. Convert from mph if needed (1 mph = 1.609 km/h)." },
    crash_type: { type: "string", enum: ["frontal", "side", "rear", "rollover", "unknown"] },
    anticoagulants: { type: "boolean", description: "True if the patient takes blood thinners (warfarin, apixaban, rivaroxaban, clopidogrel...)." },
    loss_of_consciousness: { type: "boolean", description: "True if any loss of consciousness was reported." },
    complaints: { type: "string", description: "Patient's main complaints at the scene, short." },
  },
  required: ["age", "belted", "airbag_deployed"],
  additionalProperties: false,
};

const auth = () => {
  if (!process.env.NACE_API_KEY) throw new Error("NACE_API_KEY missing");
  return { Authorization: `Bearer ${process.env.NACE_API_KEY}` };
};
const j = async (r) => { const b = await r.json().catch(() => ({})); if (!r.ok) throw new Error(`NDI ${r.status}: ${JSON.stringify(b.error ?? b).slice(0, 300)}`); return b; };

/** Upload bytes to the account workspace. Returns the Extract `source`. */
export async function upload(bytes, fileName) {
  const dest = `crashzilla/${Date.now()}-${fileName}`;
  const grant = await j(await fetch(`${BASE}/v1/documents/upload-grants`, { method: "POST", headers: { ...auth(), "Content-Type": "application/json" }, body: JSON.stringify({ path: dest }) }));
  const form = new FormData();
  form.append("file", new Blob([bytes]), fileName);
  form.append("metadata", JSON.stringify({ path: dest }));
  const up = await j(await fetch(grant.upload_url, { method: "POST", headers: { "X-Upload-Token": grant.token }, body: form }));
  return { type: "workspace_file", workspace_id: grant.workspace_id, file_id: up.result.file.file_id };
}

/** Extract the intake fields from an uploaded document. Polls until the job finishes. */
export async function extractIntake(source) {
  const t0 = performance.now();
  let job = await j(await fetch(`${BASE}/v1/documents/extract?wait_seconds=60`, { method: "POST", headers: { ...auth(), "Content-Type": "application/json" }, body: JSON.stringify({ source, schema: INTAKE_SCHEMA }) }));
  for (let i = 0; i < 30 && !["succeeded", "failed", "cancelled"].includes(job.status); i++) {
    await new Promise((r) => setTimeout(r, 2000));
    job = await j(await fetch(`${BASE}/v1/jobs/${job.job_id}`, { headers: auth() }));
  }
  if (job.status !== "succeeded") throw new Error(`NDI extract ${job.status}: ${JSON.stringify(job.error ?? {}).slice(0, 200)}`);
  return { data: job.result.data, fields: job.result.fields, job_id: job.job_id, ms: performance.now() - t0 };
}

/** Report fields → simulator inputs + extra facts Drex should know. */
export function toTriageInput(data) {
  const input = {};
  if (typeof data.age === "number") input.age = data.age;
  if (typeof data.belted === "boolean") input.belted = data.belted;
  if (typeof data.airbag_deployed === "boolean") input.airbag = data.airbag_deployed;
  if (typeof data.estimated_speed_kmh === "number") input.speedKmh = Math.round(data.estimated_speed_kmh);
  const notes = [];
  if (data.anticoagulants) notes.push("Patient is on anticoagulants (higher bleeding risk).");
  if (data.loss_of_consciousness) notes.push("Loss of consciousness reported at the scene.");
  if (data.complaints) notes.push(`Complaints: ${data.complaints}.`);
  return { input, notes };
}
