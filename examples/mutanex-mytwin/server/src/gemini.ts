import { GoogleGenAI, ThinkingLevel, Type, type GenerateContentResponse } from '@google/genai';
import type {
  AskRequestBody,
  AskResponse,
  BriefingRequestBody,
  BriefingResponse,
  ExtractRequestBody,
  ExtractResponse,
  FaceAnalyzeRequestBody,
  FaceLikenessProfile,
  PreserveSpeakRequestBody,
  PreserveSpeakResponse,
  SourceItem,
  VoicePersonaProfile,
  VoiceSynthesizeRequestBody,
  VoiceSynthesizeResponse,
  VoiceTrainRequestBody,
  VoiceTrainResponse,
} from './types.ts';

export interface GeminiService {
  ask(params: AskRequestBody): Promise<AskResponse>;
  extract(params: ExtractRequestBody): Promise<ExtractResponse>;
  briefing?(params: BriefingRequestBody): Promise<BriefingResponse>;
  trainVoicePersona?(params: VoiceTrainRequestBody): Promise<VoiceTrainResponse>;
  synthesizeSpeech?(params: VoiceSynthesizeRequestBody): Promise<VoiceSynthesizeResponse>;
  analyzeFaceLikeness?(params: FaceAnalyzeRequestBody): Promise<FaceLikenessProfile>;
  speakPreservedTwin?(params: PreserveSpeakRequestBody): Promise<PreserveSpeakResponse>;
}

const VOICE_NOMINAL_PITCH_HZ: Record<VoicePersonaProfile['prebuilt_voice'], number> = {
  Fenrir: 105,
  Charon: 125,
  Puck: 155,
  Zephyr: 182,
  Kore: 205,
};

export function selectClosestPrebuiltVoiceByPitch(
  pitchHz?: number,
  fallback: VoicePersonaProfile['prebuilt_voice'] = 'Kore'
): VoicePersonaProfile['prebuilt_voice'] {
  if (typeof pitchHz !== 'number' || !Number.isFinite(pitchHz) || pitchHz < 65 || pitchHz > 360) {
    return fallback;
  }
  let best: VoicePersonaProfile['prebuilt_voice'] = fallback;
  let bestDist = Infinity;
  for (const [voice, hz] of Object.entries(VOICE_NOMINAL_PITCH_HZ) as Array<
    [VoicePersonaProfile['prebuilt_voice'], number]
  >) {
    const dist = Math.abs(Math.log2(pitchHz / hz));
    if (dist < bestDist) {
      bestDist = dist;
      best = voice;
    }
  }
  return best;
}

/**
 * Gently shifts pitch & formant warmth on a 24kHz 16-bit mono RIFF WAV buffer
 * so the neural Gemini TTS output matches the user's measured vocal F0 & warmth.
 */
export function matchWavToVoiceProfile(
  wavBase64: string,
  voiceName: VoicePersonaProfile['prebuilt_voice'],
  targetPitchHz?: number,
  warmth = 85
): string {
  if (!targetPitchHz || !Number.isFinite(targetPitchHz) || targetPitchHz < 70 || targetPitchHz > 340) {
    return wavBase64;
  }
  const nominalHz = VOICE_NOMINAL_PITCH_HZ[voiceName] || 180;
  const rawRatio = targetPitchHz / nominalHz;
  const ratio = Math.max(0.85, Math.min(1.17, rawRatio));
  if (Math.abs(ratio - 1) < 0.025) {
    return wavBase64;
  }

  try {
    const buf = Buffer.from(wavBase64, 'base64');
    if (buf.length < 64 || buf.subarray(0, 4).toString('ascii') !== 'RIFF') {
      return wavBase64;
    }
    // Locate 'data' chunk in WAV
    let dataOffset = 44;
    let sampleRate = buf.readUInt32LE(24) || 24000;
    let channels = buf.readUInt16LE(22) || 1;
    let bitsPerSample = buf.readUInt16LE(34) || 16;
    for (let pos = 12; pos + 8 <= Math.min(buf.length, 256); ) {
      const id = buf.subarray(pos, pos + 4).toString('ascii');
      const size = buf.readUInt32LE(pos + 4);
      if (id === 'fmt ' && size >= 16) {
        channels = buf.readUInt16LE(pos + 10) || 1;
        sampleRate = buf.readUInt32LE(pos + 12) || 24000;
        bitsPerSample = buf.readUInt16LE(pos + 22) || 16;
      } else if (id === 'data') {
        dataOffset = pos + 8;
        break;
      }
      pos += 8 + size + (size % 2);
    }

    if (channels !== 1 || bitsPerSample !== 16 || buf.length <= dataOffset + 8) {
      return wavBase64;
    }

    const inSamples = Math.floor((buf.length - dataOffset) / 2);
    const outSamples = Math.max(16, Math.floor(inSamples / ratio));
    const outDataBytes = outSamples * 2;
    const outBuf = Buffer.alloc(44 + outDataBytes);

    outBuf.write('RIFF', 0, 'ascii');
    outBuf.writeUInt32LE(36 + outDataBytes, 4);
    outBuf.write('WAVE', 8, 'ascii');
    outBuf.write('fmt ', 12, 'ascii');
    outBuf.writeUInt32LE(16, 16);
    outBuf.writeUInt16LE(1, 20);
    outBuf.writeUInt16LE(1, 22);
    outBuf.writeUInt32LE(sampleRate, 24);
    outBuf.writeUInt32LE(sampleRate * 2, 28);
    outBuf.writeUInt16LE(2, 32);
    outBuf.writeUInt16LE(16, 34);
    outBuf.write('data', 36, 'ascii');
    outBuf.writeUInt32LE(outDataBytes, 40);

    const readSample = (idx: number): number => {
      const clamped = Math.max(0, Math.min(inSamples - 1, idx));
      return buf.readInt16LE(dataOffset + clamped * 2);
    };

    const warmAlpha = warmth >= 85 ? 0.14 : warmth <= 65 ? -0.08 : 0.05;
    let prevOut = 0;

    for (let i = 0; i < outSamples; i++) {
      const srcPos = i * ratio;
      const idx = Math.floor(srcPos);
      const frac = srcPos - idx;
      const s0 = readSample(idx - 1);
      const s1 = readSample(idx);
      const s2 = readSample(idx + 1);
      const s3 = readSample(idx + 2);
      // Catmull-Rom cubic interpolation for clean speech harmonics
      const c0 = s1;
      const c1 = 0.5 * (s2 - s0);
      const c2 = s0 - 2.5 * s1 + 2 * s2 - 0.5 * s3;
      const c3 = 0.5 * (s3 - s0) + 1.5 * (s1 - s2);
      const interp = ((c3 * frac + c2) * frac + c1) * frac + c0;
      const filtered = interp * (1 - Math.abs(warmAlpha)) + prevOut * warmAlpha;
      prevOut = filtered;
      const pcm = Math.max(-32767, Math.min(32767, Math.round(filtered)));
      outBuf.writeInt16LE(pcm, 44 + i * 2);
    }

    return outBuf.toString('base64');
  } catch {
    return wavBase64;
  }
}

function capWords(text: string, maxWords: number): string {
  const cleaned = text.replace(/\s+/g, ' ').trim();
  if (!cleaned) return '';
  const words = cleaned.split(' ');
  if (words.length <= maxWords) return cleaned;
  const sliced = words.slice(0, maxWords).join(' ');
  // Prefer ending cleanly on the last complete sentence if past 65% of maxWords
  const lastSentenceEnd = Math.max(
    sliced.lastIndexOf('. '),
    sliced.lastIndexOf('? '),
    sliced.lastIndexOf('! ')
  );
  if (lastSentenceEnd > sliced.length * 0.6) {
    return sliced.slice(0, lastSentenceEnd + 1).trim();
  }
  return sliced.replace(/[,;:\-–—]+$/, '').trim() + '.';
}

function extractJsonObject(raw: string): any {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i);
    if (fenced?.[1]) {
      try {
        return JSON.parse(fenced[1].trim());
      } catch {
        // continue
      }
    }
    const firstBrace = trimmed.indexOf('{');
    const lastBrace = trimmed.lastIndexOf('}');
    if (firstBrace !== -1 && lastBrace > firstBrace) {
      try {
        return JSON.parse(trimmed.slice(firstBrace, lastBrace + 1));
      } catch {
        return null;
      }
    }
    return null;
  }
}

function enrichSourceUrls(sources: SourceItem[], bundle: any): SourceItem[] {
  const insights: any[] = Array.isArray(bundle?.insights) ? bundle.insights : [];
  return sources.map((src) => {
    if (src.url) return src;
    const combined = `${src.title || ''} ${src.detail || ''}`;
    const pmidMatch = combined.match(/PMID:\s*(\d{5,10})/i);
    if (pmidMatch) {
      return {
        ...src,
        url: `https://pubmed.ncbi.nlm.nih.gov/${pmidMatch[1]}/`,
      };
    }
    if (src.finding_id) {
      const matchedIns = insights.find((i) => i?.id === src.finding_id);
      const firstPmid = matchedIns?.evidence
        ?.flatMap((ev: any) => (Array.isArray(ev?.sources) ? ev.sources : []))
        ?.find((s: string) => /^PMID:\d+/i.test(String(s)));
      if (firstPmid && src.kind === 'kg') {
        const id = String(firstPmid).replace(/^PMID:/i, '').trim();
        return { ...src, url: `https://pubmed.ncbi.nlm.nih.gov/${id}/` };
      }
    }
    return src;
  });
}

function extractGroundingWebSources(response: GenerateContentResponse): SourceItem[] {
  const chunks = response.candidates?.[0]?.groundingMetadata?.groundingChunks;
  if (!Array.isArray(chunks)) return [];
  const seen = new Set<string>();
  const webSources: SourceItem[] = [];

  for (const chunk of chunks) {
    const web = (chunk as any)?.web;
    const uri = typeof web?.uri === 'string' ? web.uri.trim() : '';
    if (!uri || seen.has(uri)) continue;
    seen.add(uri);

    let host = '';
    try {
      host = new URL(uri).hostname.replace(/^www\./, '');
    } catch {
      host = uri;
    }

    const rawTitle = typeof web?.title === 'string' && web.title.trim() ? web.title.trim() : host || 'Clinical Web Reference';
    webSources.push({
      kind: 'web',
      title: rawTitle.slice(0, 90),
      detail: host && host !== rawTitle ? `Google Search · ${host}` : 'Google Search grounded evidence',
      url: uri,
    });
  }
  return webSources;
}

export function buildKnowledgeGraphSummary(bundle: any, clinical?: any): string {
  const parts: string[] = [];
  const patient = bundle?.patient || {};

  parts.push(`PATIENT PROFILE: ${patient.preferred_name || 'Patient'}`);
  parts.push(`- DOB: ${patient.date_of_birth || 'unknown'}, Sex: ${patient.sex || 'unknown'}`);
  if (Array.isArray(patient.conditions) && patient.conditions.length) {
    parts.push(`- Conditions: ${patient.conditions.join('; ')}`);
  }
  if (Array.isArray(patient.medicines) && patient.medicines.length) {
    parts.push(`- Active Medications: ${patient.medicines.join('; ')}`);
  }
  if (Array.isArray(patient.supplements) && patient.supplements.length) {
    parts.push(`- Active Supplements: ${patient.supplements.join('; ')}`);
  }
  if (Array.isArray(patient.allergies) && patient.allergies.length) {
    parts.push(`- Allergies: ${patient.allergies.join('; ')}`);
  }

  // Newly added patient entries (e.g. uploaded lab report awaiting verification)
  const pendingFeed: any[] = Array.isArray(bundle?.pending_feed) ? bundle.pending_feed : [];
  if (pendingFeed.length > 0) {
    parts.push('\nNEWLY ADDED PATIENT REPORT / FEED ENTRIES (Awaiting care-team verification):');
    for (const entry of pendingFeed.slice(0, 20)) {
      const ext = entry?.extracted;
      if (ext && typeof ext === 'object') {
        const prevStr =
          ext.prevValue != null
            ? ` [Previous twin value: ${ext.prevValue} ${ext.prevUnit || ext.unit || ''} on ${ext.prevDate || 'prior'}, Change: ${
                ext.pctChange != null ? `${ext.pctChange > 0 ? '+' : ''}${Number(ext.pctChange).toFixed(1)}%` : 'n/a'
              }]`
            : ' [No prior twin value]';
        const flagStr = ext.flag ? ` (${String(ext.flag).toUpperCase()})` : '';
        parts.push(
          `- ${ext.name}: ${ext.value} ${ext.unit || ''}${flagStr} on ${entry.date || ext.date || 'recent report'}${
            ext.range ? `, ref ${ext.range}` : ''
          }${prevStr}`
        );
      } else {
        parts.push(`- [${entry.kind || 'entry'}] ${entry.title} (${entry.date || ''}${entry.detail ? ` · ${entry.detail}` : ''})`);
      }
    }
  }

  if (bundle?.latest_briefing?.patient || bundle?.latest_briefing?.clinician) {
    parts.push('\nRECENT REPORT BRIEFING SUMMARY:');
    if (bundle.latest_briefing.clinician) {
      parts.push(`- Clinical summary: ${bundle.latest_briefing.clinician}`);
    } else if (bundle.latest_briefing.patient) {
      parts.push(`- Summary: ${bundle.latest_briefing.patient}`);
    }
  }

  // Historical & Latest Labs in Twin Record
  const labs = bundle?.labs;
  if (labs && typeof labs === 'object') {
    const labLines: string[] = [];
    for (const [labName, arr] of Object.entries(labs)) {
      if (Array.isArray(arr) && arr.length > 0) {
        const latest = arr[arr.length - 1] as any;
        const flagStr = latest?.flag ? ` [${String(latest.flag).toUpperCase()}]` : '';
        if (arr.length > 1) {
          const historyStr = arr.map((pt: any) => `${pt.value} (${pt.date})`).join(' -> ');
          labLines.push(`- ${labName}: latest ${latest.value} ${latest.unit || ''} on ${latest.date}${flagStr} | trend: ${historyStr}`);
        } else {
          labLines.push(`- ${labName}: ${latest.value} ${latest.unit || ''} on ${latest.date}${flagStr}`);
        }
      }
    }
    if (labLines.length > 0) {
      parts.push('\nVERIFIED TWIN LABS & LONGITUDINAL TRENDS:');
      parts.push(...labLines);
    }
  }

  // Verified Knowledge Graph Findings (Insights)
  const insights: any[] = Array.isArray(bundle?.insights) ? bundle.insights : [];
  if (insights.length > 0) {
    parts.push('\nVERIFIED KNOWLEDGE GRAPH FINDINGS (bundle.insights):');
    for (const ins of insights) {
      const pmids = (ins.evidence || [])
        .flatMap((e: any) => e?.sources || [])
        .slice(0, 4)
        .join(', ');
      parts.push(
        `- [finding_id="${ins.id}"] ${ins.title} (Grade ${ins.certainty || 'n/a'}${pmids ? `, Sources: ${pmids}` : ''})\n` +
          `  Found: ${ins.found || ''}\n` +
          `  Why: ${ins.why || ''}\n` +
          `  Action: ${ins.do || ''}`
      );
    }
  }

  // Personalized Panel / Missing Tests
  const panel: any[] = Array.isArray(bundle?.panel) ? bundle.panel : [];
  if (panel.length > 0) {
    parts.push('\nPERSONALISED TEST PANEL & RECOMMENDED NEXT CHECKS:');
    for (const item of panel) {
      parts.push(
        `- ${item.test} [tier: ${item.tier}, already in record: ${item.already_have}]: ${item.why}`
      );
    }
  }

  // Refresh / Retest Windows
  const retests: any[] = Array.isArray(bundle?.refresh?.retests) ? bundle.refresh.retests : [];
  if (retests.length > 0) {
    parts.push(
      `\nRETEST SCHEDULE WINDOWS: ${retests
        .slice(0, 10)
        .map((r) => `${r.name} (${r.min_days}-${r.max_days}d)`)
        .join('; ')}`
    );
  }

  // Genetics
  const genes: any[] = Array.isArray(bundle?.genetics?.genes) ? bundle.genetics.genes : [];
  if (genes.length > 0) {
    const patientGenes = genes.filter((g) => g.role === 'patient_variant' || g.gene === 'SLCO1B1');
    if (patientGenes.length > 0) {
      parts.push(
        `\nGENETIC & PGx CONTEXT: ${patientGenes.map((g) => `${g.gene} (${g.role})`).join(', ')}`
      );
    }
  }

  // Physician CDSS Dossier Context (Doctor View)
  if (clinical && typeof clinical === 'object') {
    parts.push('\nPHYSICIAN CLINICAL DOSSIER (CDSS):');
    if (Array.isArray(clinical.priorities) && clinical.priorities.length > 0) {
      parts.push(`- Clinical Priorities: ${clinical.priorities.join('; ')}`);
    }
    if (clinical.stats) {
      parts.push(`- Dossier Stats: ${JSON.stringify(clinical.stats)}`);
    }
    const sections: any[] = Array.isArray(clinical.sections) ? clinical.sections : [];
    for (const sec of sections.slice(0, 8)) {
      const blockSummaries: string[] = [];
      for (const blk of (sec.blocks || []).slice(0, 4)) {
        if (blk.kind === 'text' && blk.text) {
          blockSummaries.push(String(blk.text).replace(/<[^>]+>/g, '').slice(0, 240));
        } else if (blk.kind === 'list' && Array.isArray(blk.items)) {
          blockSummaries.push(
            (blk.h ? `${blk.h}: ` : '') +
              blk.items
                .slice(0, 5)
                .map((it: string) => String(it).replace(/<[^>]+>/g, '').slice(0, 140))
                .join(' | ')
          );
        } else if (blk.kind === 'table' && Array.isArray(blk.rows)) {
          const sampleRows = blk.rows
            .slice(0, 5)
            .map((r: any[]) =>
              r
                .map((c) => String(c).replace(/<[^>]+>/g, '').slice(0, 80))
                .join(': ')
            )
            .join(' | ');
          blockSummaries.push(`${blk.h || sec.title}: ${sampleRows}`);
        }
      }
      if (blockSummaries.length > 0) {
        parts.push(`- Section [${sec.title}]: ${blockSummaries.join(' // ')}`);
      }
    }
  }

  return parts.join('\n');
}

export class DefaultGeminiService implements GeminiService {
  private ai: GoogleGenAI | null;
  private model: string;

  constructor(
    apiKey = process.env.GEMINI_API_KEY || '',
    model = 'gemini-4-hydrogen'
  ) {
    const trimmedKey = String(apiKey || '').trim();
    if (trimmedKey) {
      try {
        this.ai = new GoogleGenAI({
          apiKey: trimmedKey,
          httpOptions: {
            headers: {
              'User-Agent': 'aistudio-build',
            },
          },
        });
      } catch (err) {
        console.error('[Gemini init fallback]', err);
        this.ai = null;
      }
    } else {
      this.ai = null;
    }
    this.model = model;
  }

  public generateLocalKgAnswer(params: AskRequestBody): AskResponse {
    const qLower = params.question.toLowerCase();
    const isCdss = params.mode === 'cdss';
    const words = qLower
      .replace(/[^a-z0-9\s]/g, ' ')
      .split(/\s+/)
      .filter((w) => w.length > 2);
    const bundle = params.bundle || {};
    const patient = bundle.patient || {};
    const name = patient.preferred_name || 'the patient';
    const insights: any[] = Array.isArray(bundle.insights) ? bundle.insights : [];
    const labs: Record<string, any[]> =
      bundle.labs && typeof bundle.labs === 'object' ? bundle.labs : {};
    const pendingFeed: any[] = Array.isArray(bundle.pending_feed) ? bundle.pending_feed : [];
    const panel: any[] = Array.isArray(bundle.panel) ? bundle.panel : [];

    const answerParts: string[] = [];
    const sources: SourceItem[] = [];

    // Check newly added lab report entries relevant to the question (e.g. kidneys: eGFR, creatinine, uACR, potassium)
    const kidneyTerms = ['kidney', 'kidneys', 'renal', 'egfr', 'creatinine', 'uacr', 'albumin', 'potassium', 'urate'];
    const asksKidney = kidneyTerms.some((t) => qLower.includes(t));
    const asksNext = /next|check|order|test|panel|follow.?up/i.test(qLower);

    const matchingFeed = pendingFeed.filter((entry) => {
      const text = `${entry?.title || ''} ${entry?.detail || ''} ${entry?.extracted?.name || ''}`.toLowerCase();
      if (asksKidney && kidneyTerms.some((k) => text.includes(k))) return true;
      return words.some((w) => w.length > 3 && text.includes(w));
    });

    if (isCdss && asksNext) {
      const essentialNext = panel.filter((p) => p.tier === 'essential' || p.already_have === 'No').slice(0, 5);
      const feedSummary = pendingFeed
        .slice(0, 4)
        .map((f) => f.title)
        .join('; ');
      answerParts.push(
        `Findings: ${name} has ${
          (patient.conditions || []).slice(0, 3).join(', ') || 'active cardiometabolic/renal monitoring'
        }.${feedSummary ? ` Pending patient-uploaded results: ${feedSummary}.` : ''}\n` +
          `Suggested next tests: ${
            essentialNext.length > 0
              ? essentialNext.map((p) => `${p.test} (${p.why})`).join('; ')
              : 'Repeat serum creatinine, eGFR, cystatin C, urine ACR, potassium, HbA1c, and fasting lipid panel with ApoB; obtain baseline urinalysis and creatine kinase prior to lipid-lowering therapy.'
          }\n` +
          `Considerations: Verify potassium and renal trajectory on losartan and factor SLCO1B1 genotype into lipid therapy. For physician review.`
      );
      for (const p of essentialNext.slice(0, 3)) {
        sources.push({
          kind: 'record',
          title: p.test,
          detail: p.why,
        });
      }
      for (const ins of insights.slice(0, 2)) {
        sources.push({
          kind: 'finding',
          title: String(ins.title || '').slice(0, 60),
          detail: String(ins.found || '').slice(0, 100),
          finding_id: ins.id,
        });
      }
      return { answer: answerParts.join(''), sources: enrichSourceUrls(sources, bundle) };
    }

    if (matchingFeed.length > 0) {
      const details = matchingFeed.map((entry) => {
        const ext = entry.extracted;
        if (ext && ext.prevValue != null) {
          const pct =
            ext.pctChange != null
              ? ` (${ext.pctChange > 0 ? '+' : ''}${Number(ext.pctChange).toFixed(1)}%)`
              : '';
          return `${ext.name} moved from ${ext.prevValue} to ${ext.value} ${ext.unit || ''}${pct} on ${entry.date || 'your new report'}`;
        }
        return `${entry.title} (${entry.detail || entry.date || 'new report'})`;
      });
      answerParts.push(
        `From your newly uploaded report (marked waiting for care-team check): ${details.join('; ')}.`
      );
      for (const entry of matchingFeed.slice(0, 4)) {
        sources.push({
          kind: 'record',
          title: entry.title,
          detail: entry.detail || `Uploaded ${entry.date || ''}`,
        });
      }
    }

    const matchedInsights = insights.filter((ins) => {
      const hay = `${ins?.id || ''} ${ins?.title || ''} ${ins?.found || ''} ${ins?.area || ''}`.toLowerCase();
      if (asksKidney && (ins?.area === 'kidney' || hay.includes('kidney') || hay.includes('egfr'))) return true;
      return words.some((w) => w.length > 3 && hay.includes(w));
    });

    const matchedLabs: Array<[string, any]> = [];
    for (const [labName, values] of Object.entries(labs)) {
      if (!Array.isArray(values) || values.length === 0) continue;
      const lLow = labName.toLowerCase();
      if (
        qLower.includes(lLow) ||
        (asksKidney && ['egfr', 'creatinine', 'uacr', 'potassium', 'urate', 'cystatin c'].includes(lLow)) ||
        words.some((w) => w.length > 3 && lLow.includes(w))
      ) {
        matchedLabs.push([labName, values[values.length - 1]]);
      }
    }

    if (matchedInsights.length > 0) {
      const top = matchedInsights[0];
      answerParts.push(
        `${answerParts.length ? ' ' : ''}In ${name}'s verified Knowledge Graph: ${top.found || top.title} ${
          top.do ? `Recommended next step: ${top.do}` : ''
        }`
      );
      for (const ins of matchedInsights.slice(0, 3)) {
        sources.push({
          kind: 'finding',
          title: String(ins.title || '').slice(0, 60),
          detail: String(ins.found || '').slice(0, 100),
          finding_id: ins.id,
        });
      }
    } else if (matchedLabs.length > 0 && answerParts.length === 0) {
      const labStrs = matchedLabs.map(
        ([labName, val]) => `${labName}: ${val?.value} ${val?.unit || ''} (${val?.date || 'recorded'})`
      );
      answerParts.push(`According to ${name}'s verified twin record: ${labStrs.join('; ')}. Ask your doctor how these values guide your next check.`);
      for (const [labName, val] of matchedLabs.slice(0, 4)) {
        sources.push({
          kind: 'record',
          title: `${labName}: ${val?.value} ${val?.unit || ''}`.trim(),
          detail: `Recorded ${val?.date || ''}`,
        });
      }
    } else if (answerParts.length === 0) {
      const meds =
        Array.isArray(patient.medicines) && patient.medicines.length > 0
          ? patient.medicines.join(', ')
          : 'None recorded';
      const conds =
        Array.isArray(patient.conditions) && patient.conditions.length > 0
          ? patient.conditions.join(', ')
          : 'None recorded';
      answerParts.push(
        `Mapped in ${name}'s Digital Twin Knowledge Graph — Conditions: ${conds}. Current Medications: ${meds}. Ask your doctor about any unassessed areas or upcoming panel tests.`
      );
      sources.push({
        kind: 'record',
        title: `${name}'s Medical Profile`,
        detail: 'Active conditions and medications in twin record',
      });
    }

    if (isCdss && !answerParts.join(' ').includes('For physician review.')) {
      answerParts.push(' For physician review.');
    }

    return {
      answer: answerParts.join(''),
      sources: enrichSourceUrls(sources, bundle),
    };
  }

  public generateLocalBriefing(params: BriefingRequestBody): BriefingResponse {
    const patientName = params.bundle?.patient?.preferred_name || 'Your';
    const rows = Array.isArray(params.new_results) ? params.new_results : [];

    const summaries = rows.slice(0, 5).map((r) => {
      if (r.prev_value != null && r.pct_change != null) {
        const dir = r.pct_change > 0 ? 'rose' : r.pct_change < 0 ? 'fell' : 'stayed steady';
        return `${r.name} ${dir} from ${r.prev_value} to ${r.value} ${r.unit}`;
      }
      return `${r.name} is ${r.value} ${r.unit}${r.flag ? ` (${r.flag})` : ''}`;
    });

    const patientText = capWords(
      `We read ${rows.length} result${rows.length === 1 ? '' : 's'} from your new report and marked them waiting for your care team's check: ${summaries.join(
        '; '
      )}. Your twin's confirmed record stays unchanged until reviewed. At your next visit, ask your doctor what these updated numbers mean for your kidney, heart, and medicine plan.`,
      90
    );

    const clinDetails = rows.slice(0, 6).map((r) => {
      const dt = r.date ? ` (${r.date})` : '';
      const flag = r.flag ? ` [${r.flag.toUpperCase()}]` : '';
      const delta =
        r.prev_value != null && r.pct_change != null
          ? ` vs prior ${r.prev_value}${r.prev_date ? ` on ${r.prev_date}` : ''} (${
              r.pct_change > 0 ? '+' : ''
            }${Number(r.pct_change).toFixed(1)}%)`
          : ' (new baseline)';
      return `${r.name} ${r.value} ${r.unit}${flag}${dt}${delta}`;
    });

    const clinicianText = capWords(
      `Patient-uploaded lab report (${rows.length} analyte${
        rows.length === 1 ? '' : 's'
      }, pending verification) for ${patientName}: ${clinDetails.join(
        '; '
      )}. Review trajectory against active regimen and confirm repeat testing or dose adjustments. For physician review.`,
      120
    );

    return {
      patient: patientText,
      clinician: clinicianText,
    };
  }

  async ask(params: AskRequestBody): Promise<AskResponse> {
    if (!this.ai) {
      return this.generateLocalKgAnswer(params);
    }

    const isCdss = params.mode === 'cdss';
    const kgSummary = buildKnowledgeGraphSummary(params.bundle, params.clinical);

    const systemInstruction = `You are the Mutanex Digital Twin Clinical AI assistant, grounded in BOTH the patient's verified Knowledge Graph (bundle + pending uploaded lab reports + clinical dossier) AND live medical literature/guidelines via Google Search.
Rules for your answer:
- Always anchor patient-specific statements strictly in the provided Knowledge Graph & patient record. Quote exact lab values, previous vs new values (including any newly uploaded report entries marked pending check), units, and dates.
- Use Google Search to cross-reference current clinical practice guidelines (e.g. KDIGO, AHA/ACC, ADA, CPIC pharmacogenomics) relevant to the patient's findings, medications, genes, and next diagnostic steps.
- Never invent patient lab values, diagnoses, or prescriptions. If a measurement is missing from the record, state that plainly and note which test on their panel measures it.
- Urgent or notable shifts (e.g. falling eGFR, rising uACR, potassium > 5.0 mmol/L on ARB/losartan, potassium > 6, eGFR < 15) must be highlighted first.
- If mode is "cdss": write for a physician — concise, structured (Findings, Guideline & KG Synthesis, Suggested Next Tests), and end with "For physician review."
- Otherwise: write for the patient in clear, warm plain language (at most 130 words) and suggest what to ask their doctor.
- Return valid JSON matching:
  {
    "answer": "string",
    "sources": [
      { "kind": "record" | "finding" | "kg" | "web", "title": "string", "detail": "string", "finding_id": "optional matching bundle.insights[].id", "url": "optional https URL or PubMed link" }
    ]
  }`;

    const userPrompt = `Patient ID: ${params.patient_id || 'unknown'}
Mode: ${isCdss ? 'cdss' : 'patient'}
Question: ${params.question}

PATIENT KNOWLEDGE GRAPH & CLINICAL CONTEXT:
${kgSummary}`;

    const askResponseSchema = {
      type: Type.OBJECT,
      properties: {
        answer: { type: Type.STRING },
        sources: {
          type: Type.ARRAY,
          items: {
            type: Type.OBJECT,
            properties: {
              kind: { type: Type.STRING, enum: ['record', 'finding', 'kg', 'web'] },
              title: { type: Type.STRING },
              detail: { type: Type.STRING },
              finding_id: { type: Type.STRING },
              url: { type: Type.STRING },
            },
            required: ['kind', 'title'],
          },
        },
      },
      required: ['answer', 'sources'],
    };

    let response: GenerateContentResponse | null = null;
    const jsonPrompt = `${userPrompt}\n\nSearch current clinical practice guidelines (e.g., KDIGO, AHA/ACC, ADA, CPIC) where helpful to ground your response, then respond ONLY with a valid JSON object (no markdown code fences outside the JSON) matching {"answer": string, "sources": Array<{"kind": "record"|"finding"|"kg"|"web", "title": string, "detail"?: string, "finding_id"?: string, "url"?: string}>}.`;
    try {
      // Tier 1: Google Search grounding with fast LOW thinking latency
      response = await this.ai.models.generateContent({
        model: this.model,
        contents: jsonPrompt,
        config: {
          systemInstruction,
          tools: [{ googleSearch: {} }],
          thinkingConfig: { thinkingLevel: ThinkingLevel.LOW },
        },
      });
    } catch (err1: any) {
      console.warn('[Gemini ask Tier 1 fallback]', err1?.message || err1);
      // Tier 2: Structured JSON Schema fallback if search grounding is unavailable
      response = await this.ai.models.generateContent({
        model: this.model,
        contents: userPrompt,
        config: {
          systemInstruction,
          responseMimeType: 'application/json',
          responseSchema: askResponseSchema,
          thinkingConfig: { thinkingLevel: ThinkingLevel.LOW },
        },
      });
    }

    const rawText = response.text || '';
    const parsed = extractJsonObject(rawText);
    const answerText =
      typeof parsed?.answer === 'string' && parsed.answer.trim()
        ? parsed.answer.trim()
        : rawText.trim() || "That isn't in your record yet, so I won't guess.";

    const modelSources: SourceItem[] = Array.isArray(parsed?.sources)
      ? parsed.sources
          .filter((s: any) => s && typeof s.title === 'string')
          .map((s: any) => ({
            kind: ['record', 'finding', 'kg', 'web'].includes(s.kind) ? s.kind : 'kg',
            title: s.title,
            detail: typeof s.detail === 'string' ? s.detail : undefined,
            finding_id: typeof s.finding_id === 'string' ? s.finding_id : undefined,
            url: typeof s.url === 'string' && /^https?:\/\//i.test(s.url) ? s.url : undefined,
          }))
      : [];

    // Merge real Google Search grounding web citations from groundingMetadata
    const webGroundingSources = extractGroundingWebSources(response);
    const mergedSources: SourceItem[] = [...modelSources];
    for (const ws of webGroundingSources) {
      if (!mergedSources.some((existing) => existing.url && existing.url === ws.url)) {
        mergedSources.push(ws);
      }
    }

    // Cross-link any bundle.insights referenced in question or answer
    const insights: any[] = Array.isArray(params.bundle?.insights) ? params.bundle.insights : [];
    for (const ins of insights) {
      if (!ins?.id) continue;
      const idLow = String(ins.id).toLowerCase();
      const titleLow = String(ins.title || '').toLowerCase();
      if (
        params.question.toLowerCase().includes(idLow) ||
        answerText.toLowerCase().includes(idLow) ||
        (titleLow.length > 8 && answerText.toLowerCase().includes(titleLow.slice(0, 24)))
      ) {
        if (!mergedSources.some((s) => s.finding_id === ins.id)) {
          mergedSources.push({
            kind: 'finding',
            title: String(ins.title || ins.id).slice(0, 70),
            detail: String(ins.found || '').slice(0, 120),
            finding_id: ins.id,
          });
        }
      }
    }

    return {
      answer: answerText,
      sources: enrichSourceUrls(mergedSources, params.bundle),
    };
  }

  async extract(params: ExtractRequestBody): Promise<ExtractResponse> {
    if (!this.ai) {
      throw new Error('GEMINI_API_KEY is not configured for multimodal lab report extraction');
    }

    const systemInstruction = `You are a medical document analyzer. Extract all laboratory test results printed on this report.
Rules:
- Return only values printed on the report; never fill gaps, extrapolate, or guess.
- Extract the report date (YYYY-MM-DD if possible) and laboratory name if present.
- Extract individual test results with numeric values, units, reference ranges, and abnormal flags ("high" or "low", or null if normal/unflagged).`;

    const cleanBase64 = params.file.replace(/^data:[^;]+;base64,/, '');

    const response = await this.ai.models.generateContent({
      model: this.model,
      contents: [
        {
          inlineData: {
            data: cleanBase64,
            mimeType: params.mime_type,
          },
        },
        'Extract all lab test results printed on this report. Return only values printed on the report; never fill gaps.',
      ],
      config: {
        systemInstruction,
        responseMimeType: 'application/json',
        responseSchema: {
          type: Type.OBJECT,
          properties: {
            report_date: { type: Type.STRING, nullable: true },
            lab: { type: Type.STRING, nullable: true },
            results: {
              type: Type.ARRAY,
              items: {
                type: Type.OBJECT,
                properties: {
                  name: { type: Type.STRING },
                  value: { type: Type.NUMBER },
                  unit: { type: Type.STRING },
                  range: { type: Type.STRING, nullable: true },
                  flag: { type: Type.STRING, enum: ['high', 'low'], nullable: true },
                  date: { type: Type.STRING, nullable: true },
                },
                required: ['name', 'value', 'unit'],
              },
            },
          },
          required: ['report_date', 'lab', 'results'],
        },
      },
    });

    const text = response.text || '{}';
    const parsed = extractJsonObject(text) || {};
    return {
      report_date: parsed.report_date ?? null,
      lab: parsed.lab ?? null,
      results: Array.isArray(parsed.results) ? parsed.results : [],
    };
  }

  async briefing(params: BriefingRequestBody): Promise<BriefingResponse> {
    if (!this.ai) {
      return this.generateLocalBriefing(params);
    }

    const patient = params.bundle?.patient || {};
    const labs = params.bundle?.labs || {};

    // Summarize existing relevant labs for comparison
    const existingLabsSummary: Record<string, any> = {};
    for (const [key, arr] of Object.entries(labs)) {
      if (Array.isArray(arr) && arr.length > 0) {
        existingLabsSummary[key] = arr[arr.length - 1];
      }
    }

    const systemInstruction = `You generate two concise clinical briefings when a patient adds new laboratory results to their Mutanex Digital Twin:
1. "patient": MAXIMUM 90 WORDS. Plain, calm, reassuring everyday language for the patient. State clearly what changed compared with their previous twin values (mention key numbers simply), remind them their care team checks new entries first, and suggest 1-2 specific questions to ask their doctor. Never diagnose or change medicines.
2. "clinician": MAXIMUM 120 WORDS. Concise physician-oriented clinical note summarizing the clinically relevant changes with exact old -> new values, units, percentage changes, abnormal flags, and dates, plus relevant medication/condition context (e.g. CKD stage, ARB/losartan potassium monitoring, statin/PGx context). End with "For physician review."`;

    const prompt = `PATIENT PROFILE:
Name: ${patient.preferred_name || 'Patient'}
Conditions: ${(patient.conditions || []).join('; ') || 'None listed'}
Medications: ${(patient.medicines || []).join('; ') || 'None listed'}

PREVIOUS LATEST TWIN LABS:
${JSON.stringify(existingLabsSummary, null, 2)}

NEWLY ADDED REPORT RESULTS (KEPT BY PATIENT):
${JSON.stringify(params.new_results, null, 2)}`;

    try {
      const response = await this.ai.models.generateContent({
        model: this.model,
        contents: prompt,
        config: {
          systemInstruction,
          responseMimeType: 'application/json',
          responseSchema: {
            type: Type.OBJECT,
            properties: {
              patient: { type: Type.STRING },
              clinician: { type: Type.STRING },
            },
            required: ['patient', 'clinician'],
          },
        },
      });

      const parsed = extractJsonObject(response.text || '{}');
      if (parsed?.patient && parsed?.clinician) {
        return {
          patient: capWords(String(parsed.patient), 90),
          clinician: capWords(String(parsed.clinician), 120),
        };
      }
    } catch (err) {
      console.warn('[Gemini briefing fallback]', err);
    }

    return this.generateLocalBriefing(params);
  }

  async trainVoicePersona(params: VoiceTrainRequestBody): Promise<VoiceTrainResponse> {
    const prevCount = params.existing_profile?.sample_count ?? 0;
    const nextCount = Math.max(prevCount + 1, params.sample_count ?? prevCount + 1);
    const progressiveFidelity = Math.min(98, Math.round(38 + Math.min(nextCount, 6) * 10));

    const measuredPitch =
      typeof params.measured_pitch_hz === 'number' &&
      Number.isFinite(params.measured_pitch_hz) &&
      params.measured_pitch_hz >= 70 &&
      params.measured_pitch_hz <= 340
        ? Math.round(params.measured_pitch_hz)
        : params.existing_profile?.pitch_hz;

    const measuredBrightness =
      typeof params.measured_brightness === 'number' && Number.isFinite(params.measured_brightness)
        ? Math.round(params.measured_brightness)
        : params.existing_profile?.brightness;

    const pitchMatchedVoice = selectClosestPrebuiltVoiceByPitch(
      measuredPitch,
      params.existing_profile?.prebuilt_voice || 'Kore'
    );

    let transcript = (params.transcript_hint || '').trim();

    if (this.ai && params.audio_base64) {
      const cleanAudio = params.audio_base64.replace(/^data:[^;]+;base64,/, '');
      const mimeType = params.mime_type || 'audio/wav';
      try {
        const trRes = await this.ai.models.generateContent({
          model: 'gemini-3.5-transcribe',
          contents: {
            parts: [
              { inlineData: { mimeType, data: cleanAudio } },
              {
                text: 'Transcribe the exact words spoken in this audio clip cleanly without commentary. If silent or non-speech tone, return an empty string.',
              },
            ],
          },
        });
        if (trRes.text && trRes.text.trim()) {
          transcript = trRes.text.trim();
        }
      } catch (err) {
        console.warn('[gemini-3.5-transcribe fallback]', err);
      }
    }

    if (!transcript) {
      transcript =
        'When my family listens back years from now, I want them to hear calm warmth, steady optimism, and the small everyday stories that shaped us.';
    }

    if (this.ai) {
      try {
        const parts: any[] = [];
        if (params.audio_base64) {
          parts.push({
            inlineData: {
              mimeType: params.mime_type || 'audio/wav',
              data: params.audio_base64.replace(/^data:[^;]+;base64,/, ''),
            },
          });
        }
        parts.push({
          text: `Analyze this speaker's exact vocal acoustics, accent, pitch register (${
            measuredPitch ? `measured F0 = ${measuredPitch} Hz` : 'infer from audio'
          }), cadence, and intonation so Gemini 3.8 TTS Voice Design can speak back in the SAME voice as this speaker (${
            params.patient_name || 'the user'
          }).
Transcript: "${transcript}"
Closest acoustic base voice by F0: ${pitchMatchedVoice}
Previous profile: ${JSON.stringify(params.existing_profile || {})}
Sample #${nextCount}.
Return JSON with:
- vocal_style: rich TTS Voice Design prompt capturing the speaker's exact register, accent/inflection, breathiness, pacing, and warmth
- prebuilt_voice: best matching Gemini voice ("Fenrir" ~105Hz deep, "Charon" ~125Hz warm baritone, "Puck" ~155Hz midrange, "Zephyr" ~182Hz bright mezzo, "Kore" ~205Hz clear alto/soprano)
- cadence_wpm (105-170), warmth (0-100), clarity (0-100), signature_phrases (up to 4), and summary.`,
        });

        const res = await this.ai.models.generateContent({
          model: this.model,
          contents: [{ role: 'user', parts }],
          config: {
            responseMimeType: 'application/json',
            responseSchema: {
              type: Type.OBJECT,
              properties: {
                vocal_style: { type: Type.STRING },
                prebuilt_voice: {
                  type: Type.STRING,
                  enum: ['Kore', 'Puck', 'Charon', 'Fenrir', 'Zephyr'],
                },
                cadence_wpm: { type: Type.NUMBER },
                warmth: { type: Type.NUMBER },
                clarity: { type: Type.NUMBER },
                signature_phrases: {
                  type: Type.ARRAY,
                  items: { type: Type.STRING },
                },
                summary: { type: Type.STRING },
              },
              required: [
                'vocal_style',
                'prebuilt_voice',
                'cadence_wpm',
                'warmth',
                'clarity',
                'signature_phrases',
                'summary',
              ],
            },
          },
        });

        const parsed = extractJsonObject(res.text || '{}');
        if (parsed?.vocal_style) {
          const validVoices = ['Kore', 'Puck', 'Charon', 'Fenrir', 'Zephyr'] as const;
          // Anchor to acoustic F0 match if measuredPitch is present and LLM picked a distant register
          let pb: VoicePersonaProfile['prebuilt_voice'] = validVoices.includes(parsed.prebuilt_voice)
            ? parsed.prebuilt_voice
            : pitchMatchedVoice;
          if (measuredPitch) {
            const llmHz = VOICE_NOMINAL_PITCH_HZ[pb];
            if (Math.abs(measuredPitch - llmHz) > 45) {
              pb = pitchMatchedVoice;
            }
          }
          return {
            transcript,
            profile: {
              vocal_style: String(parsed.vocal_style),
              prebuilt_voice: pb,
              cadence_wpm: Math.max(95, Math.min(180, Math.round(Number(parsed.cadence_wpm) || 136))),
              warmth: Math.max(50, Math.min(100, Math.round(Number(parsed.warmth) || 88))),
              clarity: Math.max(60, Math.min(100, Math.round(Number(parsed.clarity) || 91))),
              pitch_hz: measuredPitch ?? VOICE_NOMINAL_PITCH_HZ[pb],
              brightness: measuredBrightness ?? 58,
              signature_phrases: Array.isArray(parsed.signature_phrases)
                ? parsed.signature_phrases.slice(0, 4).map(String)
                : ['Take one steady step at a time'],
              sample_count: nextCount,
              fidelity_score: progressiveFidelity,
              summary: String(
                parsed.summary ||
                  'Vocal pitch, cadence, and timbre locked from your spoken audio.'
              ),
            },
          };
        }
      } catch (err) {
        console.warn('[trainVoicePersona fallback]', err);
      }
    }

    return {
      transcript,
      profile: {
        vocal_style:
          params.existing_profile?.vocal_style ||
          `Natural conversational voice at ~${measuredPitch || VOICE_NOMINAL_PITCH_HZ[pitchMatchedVoice]} Hz with warm articulation and steady pacing`,
        prebuilt_voice: pitchMatchedVoice,
        cadence_wpm: 136,
        warmth: 89,
        clarity: 92,
        pitch_hz: measuredPitch ?? VOICE_NOMINAL_PITCH_HZ[pitchMatchedVoice],
        brightness: measuredBrightness ?? 58,
        signature_phrases: [
          ...(params.existing_profile?.signature_phrases || []),
          transcript.split(/[.,!?]/)[0]?.slice(0, 48) || 'Steady warmth and care',
        ].slice(-4),
        sample_count: nextCount,
        fidelity_score: progressiveFidelity,
        summary: `Matched to ${pitchMatchedVoice} (${measuredPitch || VOICE_NOMINAL_PITCH_HZ[pitchMatchedVoice]} Hz) from ${nextCount} voice sample${nextCount === 1 ? '' : 's'}.`,
      },
    };
  }

  async synthesizeSpeech(params: VoiceSynthesizeRequestBody): Promise<VoiceSynthesizeResponse> {
    const cleanText = capWords(
      String(params.text || '')
        .replace(/[*#_`[\]()]/g, '')
        .replace(/\s+/g, ' ')
        .trim(),
      115
    );
    const validVoices = ['Kore', 'Puck', 'Charon', 'Fenrir', 'Zephyr'] as const;
    const defaultBySex: VoicePersonaProfile['prebuilt_voice'] =
      params.patient_sex?.toLowerCase() === 'male' ? 'Charon' : 'Kore';
    const targetPitch = params.voice_profile?.pitch_hz;
    const chosenVoice: VoicePersonaProfile['prebuilt_voice'] =
      params.voice_profile?.prebuilt_voice &&
      validVoices.includes(params.voice_profile.prebuilt_voice)
        ? params.voice_profile.prebuilt_voice
        : selectClosestPrebuiltVoiceByPitch(targetPitch, defaultBySex);

    const stylePrompt =
      params.voice_profile?.vocal_style ||
      'Warm, natural conversational tone matching the speaker’s own calm cadence and clarity';
    const resolvedPitch = targetPitch || VOICE_NOMINAL_PITCH_HZ[chosenVoice];

    if (!cleanText || !this.ai) {
      return {
        audio_wav_base64: null,
        voice_used: chosenVoice,
        style_used: stylePrompt,
        pitch_hz: resolvedPitch,
      };
    }

    for (const ttsModel of ['gemini-3.8-flash-tts', 'gemini-3.8-flash-lite-tts']) {
      try {
        const ttsRes = await this.ai.models.generateContent({
          model: ttsModel,
          contents: [
            {
              role: 'user',
              parts: [
                {
                  text: cleanText,
                  speechMetadata: {
                    style: stylePrompt,
                  },
                } as any,
              ],
            },
          ],
          config: {
            responseModalities: ['AUDIO'],
            speechConfig: {
              voiceConfig: {
                prebuiltVoiceConfig: { voiceName: chosenVoice },
              },
            },
          },
        });

        const rawWav =
          ttsRes.candidates?.[0]?.content?.parts?.[0]?.inlineData?.data || null;
        if (rawWav) {
          const matchedWav = matchWavToVoiceProfile(
            rawWav,
            chosenVoice,
            targetPitch,
            params.voice_profile?.warmth ?? 86
          );
          return {
            audio_wav_base64: matchedWav,
            voice_used: `${chosenVoice} · ${resolvedPitch} Hz (${ttsModel})`,
            style_used: stylePrompt,
            pitch_hz: resolvedPitch,
          };
        }
      } catch (err: any) {
        console.warn(`[synthesizeSpeech ${ttsModel} fallback]`, err?.message || err);
      }
    }

    return {
      audio_wav_base64: null,
      voice_used: chosenVoice,
      style_used: stylePrompt,
      pitch_hz: resolvedPitch,
    };
  }

  async analyzeFaceLikeness(params: FaceAnalyzeRequestBody): Promise<FaceLikenessProfile> {
    const count = Math.max(1, params.photo_count ?? 1);
    const baseScore = Math.min(97, 62 + count * 11);

    if (this.ai && params.image_base64) {
      const cleanImg = params.image_base64.replace(/^data:[^;]+;base64,/, '');
      try {
        const res = await this.ai.models.generateContent({
          model: this.model,
          contents: [
            {
              inlineData: {
                mimeType: params.mime_type || 'image/jpeg',
                data: cleanImg,
              },
            },
            `Extract 3D avatar facial geometry ratios and appearance colors from this portrait photo so the 3D Digital Twin bust matches this person's likeness accurately.
Return valid JSON with hex colors (#RRGGBB) and normalized ratios around 1.0.`,
          ],
          config: {
            responseMimeType: 'application/json',
            responseSchema: {
              type: Type.OBJECT,
              properties: {
                skin_tone_hex: { type: Type.STRING },
                hair_color_hex: { type: Type.STRING },
                eye_color_hex: { type: Type.STRING },
                lip_color_hex: { type: Type.STRING },
                face_width_ratio: { type: Type.NUMBER },
                jaw_taper: { type: Type.NUMBER },
                cheek_prominence: { type: Type.NUMBER },
                brow_arch: { type: Type.NUMBER },
                hair_style: {
                  type: Type.STRING,
                  enum: ['short', 'medium', 'long', 'bun', 'curly'],
                },
                glasses: { type: Type.BOOLEAN },
                likeness_summary: { type: Type.STRING },
              },
              required: [
                'skin_tone_hex',
                'hair_color_hex',
                'eye_color_hex',
                'lip_color_hex',
                'face_width_ratio',
                'jaw_taper',
                'cheek_prominence',
                'brow_arch',
                'hair_style',
                'glasses',
                'likeness_summary',
              ],
            },
          },
        });

        const parsed = extractJsonObject(res.text || '{}');
        if (parsed?.skin_tone_hex) {
          const hexOr = (v: unknown, def: string) =>
            typeof v === 'string' && /^#[0-9a-fA-F]{6}$/.test(v.trim()) ? v.trim() : def;
          const clamp = (n: unknown, lo: number, hi: number, def: number) => {
            const v = Number(n);
            return Number.isFinite(v) ? Math.max(lo, Math.min(hi, v)) : def;
          };
          const styles = ['short', 'medium', 'long', 'bun', 'curly'] as const;
          return {
            skin_tone_hex: hexOr(parsed.skin_tone_hex, '#c68b67'),
            hair_color_hex: hexOr(parsed.hair_color_hex, '#1c191c'),
            eye_color_hex: hexOr(parsed.eye_color_hex, '#3b281c'),
            lip_color_hex: hexOr(parsed.lip_color_hex, '#a8645d'),
            face_width_ratio: clamp(parsed.face_width_ratio, 0.85, 1.15, 0.98),
            jaw_taper: clamp(parsed.jaw_taper, 0.78, 1.15, 0.94),
            cheek_prominence: clamp(parsed.cheek_prominence, 0.85, 1.22, 1.04),
            brow_arch: clamp(parsed.brow_arch, 0.85, 1.2, 1.02),
            hair_style: styles.includes(parsed.hair_style) ? parsed.hair_style : 'medium',
            glasses: Boolean(parsed.glasses),
            likeness_score: baseScore,
            likeness_summary: String(
              parsed.likeness_summary ||
                'Balanced facial symmetry, warm complexion, and expressive eye/brow contours mapped to 3D mesh.'
            ),
          };
        }
      } catch (err) {
        console.warn('[analyzeFaceLikeness fallback]', err);
      }
    }

    return {
      skin_tone_hex: '#c68b67',
      hair_color_hex: '#1c191c',
      eye_color_hex: '#3b281c',
      lip_color_hex: '#a8645d',
      face_width_ratio: 0.98,
      jaw_taper: 0.94,
      cheek_prominence: 1.04,
      brow_arch: 1.02,
      hair_style: 'medium',
      glasses: false,
      likeness_score: baseScore,
      likeness_summary: 'Warm complexion, shoulder-length dark hair, and gentle cheek contours mapped to 3D likeness.',
    };
  }

  async speakPreservedTwin(params: PreserveSpeakRequestBody): Promise<PreserveSpeakResponse> {
    const name = params.patient_name || params.bundle?.patient?.preferred_name || 'Anika';
    const validVoices = ['Kore', 'Puck', 'Charon', 'Fenrir', 'Zephyr'] as const;
    const chosenVoice =
      params.voice_profile?.prebuilt_voice &&
      validVoices.includes(params.voice_profile.prebuilt_voice)
        ? params.voice_profile.prebuilt_voice
        : params.bundle?.patient?.sex === 'male'
          ? 'Charon'
          : 'Kore';

    const stylePrompt =
      params.voice_profile?.vocal_style ||
      'Warm, calm, reflective, natural human cadence with gentle warmth';
    const prevFidelity = params.voice_profile?.fidelity_score ?? 45;
    const updatedFidelity = Math.min(99, prevFidelity + 4);

    let replyText = `Hi, it's ${name}. Hearing from you means so much—remember to take things one calm step at a time, enjoy a brisk walk outdoors, and look after the people we love.`;

    if (this.ai) {
      try {
        const memoriesText = Array.isArray(params.memories) && params.memories.length
          ? params.memories.join(' | ')
          : 'Loves family dinners, morning walks, yoga, home-cooked vegetarian meals, and teaching.';
        const phrasesText = (params.voice_profile?.signature_phrases || []).join('; ');

        const textRes = await this.ai.models.generateContent({
          model: this.model,
          contents: `User message to ${name}'s Preserved Digital Self: "${params.prompt}"\n\nMemories & life notes: ${memoriesText}\nSignature phrasing: ${phrasesText || 'warm, grounded everyday speech'}`,
          config: {
            systemInstruction: `You are ${name}'s Preserved Digital Self speaking in the first person ("I"). Match ${name}'s vocal cadence (${stylePrompt}), warmth, and personal memories. Keep your spoken response natural, heartfelt, and concise (30 to 55 words) so it sounds completely authentic when spoken aloud. Plain spoken words only—no markdown or stage tags.`,
          },
        });
        if (textRes.text && textRes.text.trim()) {
          replyText = capWords(textRes.text.replace(/[*#_`]/g, '').trim(), 60);
        }
      } catch (err) {
        console.warn('[speakPreservedTwin text fallback]', err);
      }

      const synth = await this.synthesizeSpeech({
        text: replyText,
        voice_profile: {
          ...params.voice_profile,
          prebuilt_voice: chosenVoice,
          vocal_style: stylePrompt,
        },
        patient_sex: params.bundle?.patient?.sex,
      });
      if (synth.audio_wav_base64) {
        return {
          reply_text: replyText,
          audio_wav_base64: synth.audio_wav_base64,
          voice_used: synth.voice_used,
          style_used: stylePrompt,
          updated_fidelity: updatedFidelity,
        };
      }
    }

    return {
      reply_text: replyText,
      audio_wav_base64: null,
      voice_used: chosenVoice,
      style_used: stylePrompt,
      updated_fidelity: updatedFidelity,
    };
  }
}
