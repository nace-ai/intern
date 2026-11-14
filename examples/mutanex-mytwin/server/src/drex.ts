import type {
  DrexChoiceMetric,
  DrexClinicalRiskRequestBody,
  DrexClinicalRiskResponse,
  DrexNoulMetric,
  DrexScoreMetric,
  DrexTriageRequestBody,
  DrexTriageResponse,
  DrexVerifyFeedRequestBody,
  DrexVerifyFeedResponse,
} from './types.ts';

const DREX_API_URL = 'https://console.nace.ai/v1/systemone';
const DREX_MODEL = 'drex-max-v1.1';

export interface DrexService {
  triage(payload: DrexTriageRequestBody): Promise<DrexTriageResponse>;
  verifyFeed(payload: DrexVerifyFeedRequestBody): Promise<DrexVerifyFeedResponse>;
  evaluateClinicalRisk(payload: DrexClinicalRiskRequestBody): Promise<DrexClinicalRiskResponse>;
}

function clamp01(val: unknown, fallback = 0.5): number {
  const num = typeof val === 'number' && Number.isFinite(val) ? val : fallback;
  return Math.max(0, Math.min(1, num));
}

/**
 * Strips base64 blobs / data URLs and extracts a compact, clean clinical summary
 * from a patient bundle so Drex never receives binary media (HTTP 422) or oversized JSON.
 */
export function compactBundleForDrex(bundle: any): Record<string, unknown> {
  if (!bundle || typeof bundle !== 'object') return {};

  const patient = bundle.patient || {};
  const latestLabs: Array<Record<string, unknown>> = [];
  if (bundle.labs && typeof bundle.labs === 'object') {
    for (const [labName, series] of Object.entries(bundle.labs)) {
      if (Array.isArray(series) && series.length > 0) {
        const latest = series[series.length - 1] as any;
        const prev = series.length > 1 ? (series[series.length - 2] as any) : null;
        latestLabs.push({
          analyte: labName,
          latest_value: latest?.value,
          unit: latest?.unit,
          date: latest?.date,
          flag: latest?.flag ?? null,
          prior_value: prev?.value ?? null,
          prior_date: prev?.date ?? null,
        });
      }
    }
  }

  const medicines = Array.isArray(bundle.medicines)
    ? bundle.medicines.slice(0, 15).map((m: any) =>
        typeof m === 'string'
          ? m
          : {
              name: m?.name || m?.title,
              dose: m?.dose,
              schedule: m?.freq || m?.schedule,
            }
      )
    : [];

  const insights = Array.isArray(bundle.insights)
    ? bundle.insights.slice(0, 10).map((ins: any) => ({
        id: ins?.id,
        title: ins?.title,
        severity: ins?.state || ins?.tone || null,
      }))
    : [];

  const pendingFeed = Array.isArray(bundle.pending_feed)
    ? bundle.pending_feed.slice(0, 12).map((f: any) => ({
        kind: f?.kind,
        date: f?.date,
        title: f?.title,
        detail: f?.detail,
        flag: f?.extracted?.flag ?? null,
        pct_change: f?.extracted?.pctChange ?? null,
      }))
    : [];

  return {
    patient: {
      name: patient.preferred_name || patient.name || 'Patient',
      age: patient.age ?? null,
      sex: patient.sex ?? null,
      conditions: Array.isArray(patient.conditions) ? patient.conditions.slice(0, 12) : [],
    },
    latest_labs: latestLabs.slice(0, 24),
    medicines,
    key_findings: insights,
    pending_patient_entries: pendingFeed,
  };
}

export function sanitizeDrexState(input: unknown, depth = 0): unknown {
  if (depth > 6 || input == null) return null;
  if (typeof input === 'string') {
    if (/^data:[a-z0-9.+-]+\/[a-z0-9.+-]+;base64,/i.test(input.trim())) {
      return '[omitted-binary-data-url]';
    }
    if (input.length > 1200 && /^[A-Za-z0-9+/=\s]+$/.test(input)) {
      return '[omitted-base64-payload]';
    }
    return input.length > 4000 ? `${input.slice(0, 4000)}…` : input;
  }
  if (typeof input === 'number' || typeof input === 'boolean') {
    return input;
  }
  if (Array.isArray(input)) {
    return input.slice(0, 30).map((item) => sanitizeDrexState(item, depth + 1));
  }
  if (typeof input === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(input as Record<string, unknown>).slice(0, 40)) {
      if (k === 'file' || k === 'audio_base64' || k === 'image_base64' || k === 'audio_wav_base64') {
        continue;
      }
      out[k] = sanitizeDrexState(v, depth + 1);
    }
    return out;
  }
  return String(input);
}

function parseNoulMetric(raw: any, fallbackProb: number): DrexNoulMetric {
  const probability = clamp01(raw?.noul, fallbackProb);
  const confidence = clamp01(raw?.confidence, Math.max(probability, 1 - probability));
  return { probability, confidence };
}

function parseScoreMetric(raw: any, legend: string[], fallbackScore: number): DrexScoreMetric {
  const resolvedLegend = Array.isArray(raw?.legend) && raw.legend.length > 0 ? raw.legend : legend;
  const maxIdx = Math.max(0, resolvedLegend.length - 1);
  const score =
    typeof raw?.score === 'number' && Number.isFinite(raw.score)
      ? Math.max(0, Math.min(maxIdx, raw.score))
      : Math.max(0, Math.min(maxIdx, fallbackScore));
  const roundedIdx = Math.min(maxIdx, Math.max(0, Math.round(score)));
  const label = resolvedLegend[roundedIdx] || resolvedLegend[0] || 'Routine';

  const probabilities: Record<string, number> = {};
  if (raw?.probabilities && typeof raw.probabilities === 'object') {
    for (let i = 0; i < resolvedLegend.length; i++) {
      probabilities[String(i)] = clamp01(raw.probabilities[String(i)], 0);
    }
  } else {
    let sum = 0;
    for (let i = 0; i < resolvedLegend.length; i++) {
      const dist = Math.abs(i - score);
      const w = Math.exp(-1.4 * dist);
      probabilities[String(i)] = w;
      sum += w;
    }
    for (let i = 0; i < resolvedLegend.length; i++) {
      probabilities[String(i)] = Number((probabilities[String(i)] / (sum || 1)).toFixed(4));
    }
  }

  const topProb = Math.max(...Object.values(probabilities), 0.5);
  const confidence = clamp01(raw?.confidence, topProb);
  return {
    score: Number(score.toFixed(3)),
    label,
    legend: resolvedLegend,
    probabilities,
    confidence,
  };
}

function parseChoiceMetric(
  raw: any,
  keys: string[],
  fallbackChoice: string,
  fallbackProbs: Record<string, number>
): DrexChoiceMetric {
  const probabilities: Record<string, number> = {};
  if (raw?.probabilities && typeof raw.probabilities === 'object') {
    for (const k of keys) {
      probabilities[k] = clamp01(raw.probabilities[k], 0);
    }
  } else {
    for (const k of keys) {
      probabilities[k] = clamp01(fallbackProbs[k], 1 / Math.max(1, keys.length));
    }
  }

  let bestKey = typeof raw?.choice === 'string' && keys.includes(raw.choice) ? raw.choice : fallbackChoice;
  if (!keys.includes(bestKey)) {
    bestKey = keys[0] || fallbackChoice;
  }
  const confidence = clamp01(raw?.confidence, probabilities[bestKey] ?? 0.6);
  return {
    choice: bestKey,
    probabilities,
    confidence,
  };
}

export class DefaultDrexService implements DrexService {
  private readonly explicitKey?: string;

  constructor(apiKey?: string) {
    this.explicitKey = apiKey;
  }

  private resolveApiKey(): string {
    if (this.explicitKey !== undefined) {
      return this.explicitKey.trim();
    }
    return (process.env.NACE_API_KEY || process.env.DREX_API_KEY || '').trim();
  }

  private async callSystemOne(
    state: unknown,
    questions: Record<string, unknown>
  ): Promise<Record<string, any> | null> {
    const apiKey = this.resolveApiKey();
    if (!apiKey) {
      return null;
    }

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(DREX_API_URL, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${apiKey}`,
          'X-Nace-Client': 'mutanex-mytwin/1',
          'Content-Type': 'application/json',
        },
        signal: controller.signal,
        body: JSON.stringify({
          model: DREX_MODEL,
          state: sanitizeDrexState(state),
          questions,
        }),
      });
      clearTimeout(timeout);

      if (!response.ok) {
        return null;
      }

      const json = (await response.json()) as Record<string, any>;
      return json;
    } catch {
      clearTimeout(timeout);
      return null;
    }
  }

  async triage(payload: DrexTriageRequestBody): Promise<DrexTriageResponse> {
    const severityLegend = ['Routine', 'Mild', 'Moderate', 'High'];
    const specialtyKeys = ['nephrology', 'cardiology', 'endocrinology', 'primary_care'];

    const state = {
      mode: payload.mode || 'patient',
      patient_input: payload.text || null,
      onboarding_summary: payload.summary_lines || null,
      questionnaire: payload.questionnaire || null,
      uploaded_labs: Array.isArray(payload.uploaded_labs) ? payload.uploaded_labs.slice(0, 16) : undefined,
      genetics: Array.isArray(payload.genetics) ? payload.genetics.slice(0, 10) : undefined,
      twin_context: payload.bundle ? compactBundleForDrex(payload.bundle) : undefined,
    };

    const questions = {
      urgent_attention: {
        type: 'noul',
        instructions:
          'Does this clinical question, symptom report, or intake profile warrant prompt physician review within 48 hours rather than waiting for a routine checkup?',
      },
      med_or_lab_link: {
        type: 'noul',
        instructions:
          'Is this concern plausibly connected to an abnormal biomarker trend, organ finding, or active medication in the patient record?',
      },
      actionable_change: {
        type: 'noul',
        instructions:
          'Would addressing this question or signal likely lead to a concrete diagnostic test order, monitoring change, or treatment review?',
      },
      severity: {
        type: 'score',
        instructions: 'Rate the clinical concern severity of this inquiry or patient intake snapshot.',
        criteria: severityLegend,
      },
      specialty: {
        type: 'choice',
        instructions: 'Which clinical specialty is most relevant to address the primary signal here?',
        criteria: {
          nephrology: 'Kidney function, eGFR, creatinine, albuminuria/uACR, electrolytes, potassium, fluid retention',
          cardiology: 'Blood pressure, lipids, LDL, ApoB, chest symptoms, heart rate, statin or antihypertensive therapy',
          endocrinology: 'HbA1c, glucose, diabetes, thyroid, weight or metabolic syndrome',
          primary_care: 'General preventive care, sleep, habits, family history, routine wellness',
        },
      },
    };

    const live = await this.callSystemOne(state, questions);
    const combinedText = JSON.stringify(state).toLowerCase();
    const hasRedFlag =
      /chest|shortness of breath|faint|swollen|edema|potassium 5\.[3-9]|egfr [1-4]\d|dropped|urgent|severe/.test(
        combinedText
      );
    const hasRenal = /egfr|kidney|creatinine|uacr|albumin|potassium|losartan|ankles/.test(combinedText);
    const hasCardio = /ldl|cholesterol|statin|slco1b1|blood pressure|systolic|heart/.test(combinedText);
    const hasEndo = /hba1c|glucose|diabetes|thyroid|insulin/.test(combinedText);

    const fallbackUrgent = hasRedFlag ? 0.68 : hasRenal || hasCardio ? 0.34 : 0.14;
    const fallbackLink = hasRenal || hasCardio || hasEndo ? 0.82 : 0.38;
    const fallbackActionable = hasRedFlag ? 0.89 : hasRenal || hasCardio || hasEndo ? 0.74 : 0.31;
    const fallbackScore = hasRedFlag ? 2.4 : hasRenal || hasCardio ? 1.6 : 0.7;
    const fallbackProbs: Record<string, number> = hasRenal
      ? { nephrology: 0.58, cardiology: 0.22, endocrinology: 0.08, primary_care: 0.12 }
      : hasCardio
        ? { nephrology: 0.12, cardiology: 0.56, endocrinology: 0.14, primary_care: 0.18 }
        : hasEndo
          ? { nephrology: 0.1, cardiology: 0.18, endocrinology: 0.54, primary_care: 0.18 }
          : { nephrology: 0.12, cardiology: 0.18, endocrinology: 0.14, primary_care: 0.56 };
    const fallbackChoice = hasRenal
      ? 'nephrology'
      : hasCardio
        ? 'cardiology'
        : hasEndo
          ? 'endocrinology'
          : 'primary_care';

    if (live) {
      return {
        source: 'drex-max-v1.1',
        evaluation_time_ms:
          typeof live.evaluation_time_ms === 'number' ? Math.round(live.evaluation_time_ms) : undefined,
        input_tokens:
          typeof live.usage?.input_tokens === 'number' ? live.usage.input_tokens : undefined,
        output_tokens:
          typeof live.usage?.output_tokens === 'number' ? live.usage.output_tokens : undefined,
        urgent_attention: parseNoulMetric(live.urgent_attention, fallbackUrgent),
        med_or_lab_link: parseNoulMetric(live.med_or_lab_link, fallbackLink),
        actionable_change: parseNoulMetric(live.actionable_change, fallbackActionable),
        severity: parseScoreMetric(live.severity, severityLegend, fallbackScore),
        specialty: parseChoiceMetric(live.specialty, specialtyKeys, fallbackChoice, fallbackProbs),
      };
    }

    return {
      source: 'calibrated-fallback',
      urgent_attention: parseNoulMetric(null, fallbackUrgent),
      med_or_lab_link: parseNoulMetric(null, fallbackLink),
      actionable_change: parseNoulMetric(null, fallbackActionable),
      severity: parseScoreMetric(null, severityLegend, fallbackScore),
      specialty: parseChoiceMetric(null, specialtyKeys, fallbackChoice, fallbackProbs),
    };
  }

  async verifyFeed(payload: DrexVerifyFeedRequestBody): Promise<DrexVerifyFeedResponse> {
    const priorityLegend = ['Routine log', 'Standard check', 'Priority review', 'Urgent flag'];
    const domainKeys = [
      'renal_electrolytes',
      'cardiometabolic',
      'medication_safety',
      'general_wellness',
    ];

    const entries = Array.isArray(payload.entries) ? payload.entries.slice(0, 20) : [];
    const state = {
      submitted_entries: entries,
      twin_context: payload.bundle ? compactBundleForDrex(payload.bundle) : undefined,
    };

    const questions = {
      clinically_actionable: {
        type: 'noul',
        instructions:
          'Do these patient-submitted health entries or lab results represent a clinically meaningful signal that could alter risk stratification, monitoring, or treatment?',
      },
      prompt_followup: {
        type: 'noul',
        instructions:
          'Should the care team review and follow up on these submitted entries promptly rather than waiting for the next routine annual visit?',
      },
      review_priority: {
        type: 'score',
        instructions: 'Assign the clinician verification queue priority for these patient-submitted entries.',
        criteria: priorityLegend,
      },
      domain: {
        type: 'choice',
        instructions: 'Which clinical domain best characterizes these submitted entries?',
        criteria: {
          renal_electrolytes: 'Kidney filtration (eGFR, creatinine, uACR), potassium, fluid balance',
          cardiometabolic: 'Blood pressure, lipids, LDL, HbA1c, glucose, weight',
          medication_safety: 'Prescription medicines, supplements, dosing changes, side effects',
          general_wellness: 'Sleep, physical activity, smoking/alcohol habits, family history',
        },
      },
    };

    const live = await this.callSystemOne(state, questions);
    const text = JSON.stringify(entries).toLowerCase();
    const flaggedCount = entries.filter(
      (e) => e?.extracted?.flag === 'high' || e?.extracted?.flag === 'low'
    ).length;
    const isRenal = /egfr|creatinine|uacr|potassium|kidney|ankle|swollen/.test(text);
    const isMed = entries.some((e) => e.kind === 'medicine') || /mg|losartan|statin|dose/.test(text);
    const isCardioMet =
      /blood pressure|ldl|cholesterol|hba1c|glucose|weight/.test(text) ||
      entries.some((e) => e.kind === 'measure');

    const fallbackActionable = flaggedCount > 0 ? 0.88 : isRenal || isMed || isCardioMet ? 0.64 : 0.28;
    const fallbackPrompt = flaggedCount >= 2 ? 0.79 : flaggedCount === 1 || isRenal ? 0.56 : 0.21;
    const fallbackPriority = flaggedCount >= 2 ? 2.3 : flaggedCount === 1 || isRenal ? 1.7 : 0.8;
    const fallbackDomain = isRenal
      ? 'renal_electrolytes'
      : isMed
        ? 'medication_safety'
        : isCardioMet
          ? 'cardiometabolic'
          : 'general_wellness';
    const fallbackProbs: Record<string, number> =
      fallbackDomain === 'renal_electrolytes'
        ? { renal_electrolytes: 0.62, cardiometabolic: 0.2, medication_safety: 0.1, general_wellness: 0.08 }
        : fallbackDomain === 'medication_safety'
          ? { renal_electrolytes: 0.1, cardiometabolic: 0.16, medication_safety: 0.62, general_wellness: 0.12 }
          : fallbackDomain === 'cardiometabolic'
            ? { renal_electrolytes: 0.14, cardiometabolic: 0.6, medication_safety: 0.12, general_wellness: 0.14 }
            : { renal_electrolytes: 0.08, cardiometabolic: 0.16, medication_safety: 0.1, general_wellness: 0.66 };

    if (live) {
      return {
        source: 'drex-max-v1.1',
        evaluation_time_ms:
          typeof live.evaluation_time_ms === 'number' ? Math.round(live.evaluation_time_ms) : undefined,
        clinically_actionable: parseNoulMetric(live.clinically_actionable, fallbackActionable),
        prompt_followup: parseNoulMetric(live.prompt_followup, fallbackPrompt),
        review_priority: parseScoreMetric(live.review_priority, priorityLegend, fallbackPriority),
        domain: parseChoiceMetric(live.domain, domainKeys, fallbackDomain, fallbackProbs),
      };
    }

    return {
      source: 'calibrated-fallback',
      clinically_actionable: parseNoulMetric(null, fallbackActionable),
      prompt_followup: parseNoulMetric(null, fallbackPrompt),
      review_priority: parseScoreMetric(null, priorityLegend, fallbackPriority),
      domain: parseChoiceMetric(null, domainKeys, fallbackDomain, fallbackProbs),
    };
  }

  async evaluateClinicalRisk(
    payload: DrexClinicalRiskRequestBody
  ): Promise<DrexClinicalRiskResponse> {
    const urgencyLegend = [
      'Routine annual',
      'Quarterly review',
      'Near-term (2–4 wks)',
      'Immediate (<72h)',
    ];
    const specialtyKeys = ['nephrology', 'cardiology', 'endocrinology', 'internal_medicine'];

    const compact = compactBundleForDrex({
      ...payload.bundle,
      pending_feed: payload.pending_feed ?? payload.bundle?.pending_feed,
    });

    const clinicalSummary =
      payload.clinical && Array.isArray(payload.clinical.sections)
        ? payload.clinical.sections.slice(0, 5).map((s: any) => ({
            title: s?.title,
            summary: s?.sub || s?.lead || null,
          }))
        : undefined;

    const state = {
      twin_summary: compact,
      clinician_dossier_sections: clinicalSummary,
      unverified_report_briefing: payload.latest_briefing?.clinician || null,
    };

    const questions = {
      deterioration_30d: {
        type: 'noul',
        instructions:
          'Is there an elevated 30-day risk of biomarker progression, organ function decline, or clinical decompensation if current trends go unaddressed?',
      },
      renal_or_med_adjustment: {
        type: 'noul',
        instructions:
          'Do the patient labs, pharmacogenomics, or active medications warrant physician review for dose adjustment, electrolyte re-check, or drug selection?',
      },
      monitoring_gap: {
        type: 'noul',
        instructions:
          'Are there pending unverified patient labs, overdue confirmatory tests, or unassessed biomarker gaps that require near-term diagnostic ordering?',
      },
      followup_urgency: {
        type: 'score',
        instructions: 'Select the recommended clinical follow-up horizon based on longitudinal trajectory and pending deltas.',
        criteria: urgencyLegend,
      },
      lead_specialty: {
        type: 'choice',
        instructions: 'Which specialty perspective should lead the next clinical review for this patient?',
        criteria: {
          nephrology: 'CKD staging, eGFR decline, albuminuria, potassium/RAASi management',
          cardiology: 'Lipid lowering, ApoB/Lp(a), hypertension, PGx statin selection',
          endocrinology: 'Glycemic control, insulin resistance, thyroid or metabolic trajectory',
          internal_medicine: 'Comprehensive longitudinal primary care and preventive coordination',
        },
      },
    };

    const live = await this.callSystemOne(state, questions);
    const rawStr = JSON.stringify(state).toLowerCase();
    const hasPending = Array.isArray(payload.pending_feed) && payload.pending_feed.length > 0;
    const hasRenalSignal = /egfr|ckd|kidney|uacr|potassium/.test(rawStr);
    const hasCardioSignal = /ldl|statin|slco1b1|hypertension|blood pressure/.test(rawStr);

    const fbDet = hasPending && hasRenalSignal ? 0.64 : hasRenalSignal ? 0.46 : 0.24;
    const fbMed = hasRenalSignal || hasCardioSignal ? 0.74 : 0.32;
    const fbGap = hasPending ? 0.83 : 0.48;
    const fbUrg = hasPending && hasRenalSignal ? 2.15 : hasRenalSignal ? 1.65 : 1.1;
    const fbSpec = hasRenalSignal
      ? 'nephrology'
      : hasCardioSignal
        ? 'cardiology'
        : 'internal_medicine';
    const fbSpecProbs: Record<string, number> =
      fbSpec === 'nephrology'
        ? { nephrology: 0.54, cardiology: 0.26, endocrinology: 0.08, internal_medicine: 0.12 }
        : fbSpec === 'cardiology'
          ? { nephrology: 0.14, cardiology: 0.52, endocrinology: 0.14, internal_medicine: 0.2 }
          : { nephrology: 0.12, cardiology: 0.2, endocrinology: 0.16, internal_medicine: 0.52 };

    if (live) {
      return {
        source: 'drex-max-v1.1',
        evaluation_time_ms:
          typeof live.evaluation_time_ms === 'number' ? Math.round(live.evaluation_time_ms) : undefined,
        evaluated_at: new Date().toISOString(),
        deterioration_30d: parseNoulMetric(live.deterioration_30d, fbDet),
        renal_or_med_adjustment: parseNoulMetric(live.renal_or_med_adjustment, fbMed),
        monitoring_gap: parseNoulMetric(live.monitoring_gap, fbGap),
        followup_urgency: parseScoreMetric(live.followup_urgency, urgencyLegend, fbUrg),
        lead_specialty: parseChoiceMetric(live.lead_specialty, specialtyKeys, fbSpec, fbSpecProbs),
      };
    }

    return {
      source: 'calibrated-fallback',
      evaluated_at: new Date().toISOString(),
      deterioration_30d: parseNoulMetric(null, fbDet),
      renal_or_med_adjustment: parseNoulMetric(null, fbMed),
      monitoring_gap: parseNoulMetric(null, fbGap),
      followup_urgency: parseScoreMetric(null, urgencyLegend, fbUrg),
      lead_specialty: parseChoiceMetric(null, specialtyKeys, fbSpec, fbSpecProbs),
    };
  }
}
