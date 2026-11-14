import React, { useEffect, useRef, useState } from 'react';
import {
  formatProbPct,
  humanizeChoiceKey,
  scoreDistributionRows,
  sortedChoiceProbabilities,
} from './drex-utils';
import { BRIEFING_STORAGE_KEY } from './feed';
import { usePortalNavigation } from './navigation-context';
import { createSampleVoiceWavBase64, type VoicePersonaData } from './preserve-math';
import type {
  CompareModelsData,
  DrexClinicalRiskData,
  DrexFeedVerificationData,
  DrexTriageData,
  FeedEntry,
  PortalHostBridge,
  StoredBriefing,
} from './types';
import {
  analyzeBlobAcoustics,
  blobToBase64,
  playMatchedVoiceReply,
  requestMatchedVoiceSynthesis,
  resolvePortalApiUrl,
  SHARED_VOICE_STORE_KEY,
  trainAndTranscribeUserVoice,
  VOICE_UPDATED_EVENT,
} from './voice-engine';

export function DrexTriageSummaryBlock({
  triage,
  heading = 'Calibrated clinical triage',
}: {
  triage: DrexTriageData;
  heading?: string;
}) {
  const topSpecs = sortedChoiceProbabilities(triage.specialty.probabilities);
  const sevRows = scoreDistributionRows(triage.severity.legend, triage.severity.probabilities);

  return (
    <div className="mx-drex-triage-strip" data-testid="drex-triage-summary">
      <div className="mx-drex-triage-top">
        <b>{heading}</b>
        <span className="note mx-tabular">
          {triage.source}
          {triage.evaluation_time_ms ? ` · ${triage.evaluation_time_ms} ms` : ''}
          {' · '}Severity: <b>{triage.severity.label}</b>
          {' · '}Routing: <b>{humanizeChoiceKey(triage.specialty.choice)}</b>
        </span>
      </div>

      <div className="mx-drex-triage-meters">
        <div className="mx-drex-meter-item">
          <div className="mx-drex-meter-head">
            <span>Prompt 48h review</span>
            <b className="mx-tabular">{formatProbPct(triage.urgent_attention.probability, 1)}</b>
          </div>
          <div className="onb-meter">
            <span
              style={{ width: `${Math.round(triage.urgent_attention.probability * 100)}%` }}
            />
          </div>
        </div>

        <div className="mx-drex-meter-item">
          <div className="mx-drex-meter-head">
            <span>Biomarker / medication link</span>
            <b className="mx-tabular">{formatProbPct(triage.med_or_lab_link.probability, 1)}</b>
          </div>
          <div className="onb-meter">
            <span
              style={{ width: `${Math.round(triage.med_or_lab_link.probability * 100)}%` }}
            />
          </div>
        </div>

        {triage.actionable_change && (
          <div className="mx-drex-meter-item">
            <div className="mx-drex-meter-head">
              <span>Actionable care-plan step</span>
              <b className="mx-tabular">
                {formatProbPct(triage.actionable_change.probability, 1)}
              </b>
            </div>
            <div className="onb-meter">
              <span
                style={{ width: `${Math.round(triage.actionable_change.probability * 100)}%` }}
              />
            </div>
          </div>
        )}
      </div>

      <div className="mx-drex-inline-meta">
        <span>Severity distribution:</span>
        {sevRows.map((r, i) => (
          <React.Fragment key={r.label}>
            {i > 0 && <span aria-hidden="true">·</span>}
            <span className="mx-tabular">
              {r.label} {r.pct.toFixed(0)}%
            </span>
          </React.Fragment>
        ))}
        {topSpecs.length > 0 && (
          <>
            <span aria-hidden="true">·</span>
            <span className="mx-tabular">
              Top specialty: {topSpecs[0].label} ({topSpecs[0].pct.toFixed(0)}%)
            </span>
          </>
        )}
      </div>
    </div>
  );
}

const BENCHMARK_PROMPTS = [
  'My ankles are swollen and my new lab report shows eGFR dropped from 58 to 51 with potassium 5.3 on losartan — is this urgent?',
  'Could my SLCO1B1 gene variant affect which cholesterol medicine my doctor considers for LDL 144 mg/dL?',
  'I slept 6 hours after a flight and feel mildly tired today, with normal blood pressure — do I need urgent care?',
];

export function ModelComparisonPanel({
  b,
  host,
  clinicalDoc,
}: {
  b: any;
  host: PortalHostBridge;
  clinicalDoc?: any;
}) {
  const isDoctor = host.useDoctor ? host.useDoctor() : false;
  const patientName = b?.patient?.preferred_name || 'Member';
  const [prompt, setPrompt] = useState(BENCHMARK_PROMPTS[0]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CompareModelsData | null>(null);

  // Voice cloning & matched-voice playback state
  const [voiceProfile, setVoiceProfile] = useState<VoicePersonaData | null>(() =>
    host.readStore<VoicePersonaData | null>(SHARED_VOICE_STORE_KEY, null)
  );
  const [autoSpeak, setAutoSpeak] = useState(true);
  const [recording, setRecording] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState<string | null>(null);
  const [synthBusy, setSynthBusy] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  useEffect(() => {
    const syncVoice = () => {
      setVoiceProfile(host.readStore<VoicePersonaData | null>(SHARED_VOICE_STORE_KEY, null));
    };
    window.addEventListener(VOICE_UPDATED_EVENT, syncVoice);
    return () => window.removeEventListener(VOICE_UPDATED_EVENT, syncVoice);
  }, [host]);

  const saveVoiceProfile = (profile: VoicePersonaData) => {
    setVoiceProfile(profile);
    host.writeStore(SHARED_VOICE_STORE_KEY, profile);
    window.dispatchEvent(new Event(VOICE_UPDATED_EVENT));
  };

  const runComparison = async (
    questionToRun: string,
    overrideProfile?: VoicePersonaData | null,
    forceAudio = false
  ) => {
    const trimmed = questionToRun.trim();
    if (!trimmed || loading) return;
    setLoading(true);
    setError(null);

    try {
      const pendingFeed = host.readStore<FeedEntry[]>('mx-feed', []);
      const latestBriefing = host.readStore<StoredBriefing | null>(BRIEFING_STORAGE_KEY, null);
      const enrichedBundle = {
        ...b,
        pending_feed: pendingFeed,
        latest_briefing: latestBriefing,
      };
      const activeProfile = overrideProfile ?? voiceProfile;
      const shouldSpeak = forceAudio || autoSpeak;

      const res = await fetch(resolvePortalApiUrl('/v1/compare-models', host.apiBase), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: trimmed,
          bundle: enrichedBundle,
          mode: isDoctor ? 'cdss' : 'patient',
          clinical: clinicalDoc,
          synthesize_audio: shouldSpeak,
          voice_profile: activeProfile || undefined,
        }),
      });
      if (!res.ok) {
        throw new Error(`Model comparison returned HTTP ${res.status}`);
      }
      const data = (await res.json()) as CompareModelsData;
      setResult(data);

      if (shouldSpeak) {
        const spokenSummary = `Drex calibrated urgent review at ${Math.round(
          data.drex.triage.urgent_attention.probability * 100
        )} percent with ${data.drex.triage.severity.label} severity. ${data.gemini.answer}`;
        await playMatchedVoiceReply(data.audio_wav_base64, spokenSummary, activeProfile);
      }
    } catch (err: any) {
      setError(err?.message || 'Comparison request failed.');
    } finally {
      setLoading(false);
    }
  };

  const handleProcessVoiceBlob = async (blob: Blob, fallbackHint?: string) => {
    setVoiceStatus('Analyzing your vocal pitch, cadence & transcribing…');
    try {
      const [acoustics, b64] = await Promise.all([
        analyzeBlobAcoustics(blob),
        blobToBase64(blob),
      ]);
      const trained = await trainAndTranscribeUserVoice({
        audioBase64: b64,
        mimeType: blob.type || 'audio/wav',
        measuredPitchHz: acoustics.pitchHz,
        measuredBrightness: acoustics.brightness,
        transcriptHint: fallbackHint || prompt,
        patientName,
        existingProfile: voiceProfile,
        apiBase: host.apiBase,
      });
      saveVoiceProfile(trained.profile);
      const spokenQuestion = trained.transcript?.trim() || fallbackHint || prompt;
      setPrompt(spokenQuestion);
      setVoiceStatus(
        `Voice matched: ${trained.profile.prebuilt_voice} · ${
          trained.profile.pitch_hz || 165
        } Hz · ${trained.profile.cadence_wpm} WPM`
      );
      await runComparison(spokenQuestion, trained.profile, true);
    } catch (err: any) {
      setVoiceStatus(err?.message || 'Could not process voice input.');
    }
  };

  const handleToggleMic = async () => {
    if (recording && recorderRef.current) {
      recorderRef.current.stop();
      setRecording(false);
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setVoiceStatus('Microphone unavailable in this browser — use Upload audio or Sample voice.');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunksRef.current = [];
      const rec = new MediaRecorder(stream);
      recorderRef.current = rec;
      rec.ondataavailable = (ev) => {
        if (ev.data && ev.data.size > 0) chunksRef.current.push(ev.data);
      };
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const mime = rec.mimeType || 'audio/webm';
        const blob = new Blob(chunksRef.current, { type: mime });
        await handleProcessVoiceBlob(blob);
      };
      rec.start();
      setRecording(true);
      setVoiceStatus('Listening… speak your question, then click Stop & Run.');
    } catch {
      setVoiceStatus('Microphone permission declined — use Upload audio or Sample voice.');
    }
  };

  const handleSampleVoiceRun = async () => {
    setVoiceStatus('Calibrating from sample voice waveform…');
    try {
      const b64 = createSampleVoiceWavBase64(0.85);
      const trained = await trainAndTranscribeUserVoice({
        audioBase64: b64,
        mimeType: 'audio/wav',
        measuredPitchHz: 185,
        measuredBrightness: 62,
        transcriptHint: prompt,
        patientName,
        existingProfile: voiceProfile,
        apiBase: host.apiBase,
      });
      saveVoiceProfile(trained.profile);
      setVoiceStatus(
        `Voice matched: ${trained.profile.prebuilt_voice} · ${
          trained.profile.pitch_hz || 185
        } Hz · Speaking reply in matched voice`
      );
      await runComparison(prompt, trained.profile, true);
    } catch (err: any) {
      setVoiceStatus(err?.message || 'Sample voice calibration failed.');
    }
  };

  const handleSpeakExistingResult = async () => {
    if (!result || synthBusy) return;
    const spokenSummary = `Drex calibrated urgent review at ${Math.round(
      result.drex.triage.urgent_attention.probability * 100
    )} percent with ${result.drex.triage.severity.label} severity. ${result.gemini.answer}`;

    if (result.audio_wav_base64) {
      await playMatchedVoiceReply(result.audio_wav_base64, spokenSummary, voiceProfile);
      return;
    }

    setSynthBusy(true);
    try {
      const synth = await requestMatchedVoiceSynthesis({
        text: spokenSummary,
        voiceProfile,
        patientSex: b?.patient?.sex,
        apiBase: host.apiBase,
      });
      setResult((prev) =>
        prev
          ? {
              ...prev,
              audio_wav_base64: synth.audio_wav_base64,
              voice_used: synth.voice_used,
            }
          : prev
      );
      await playMatchedVoiceReply(synth.audio_wav_base64, spokenSummary, voiceProfile);
    } finally {
      setSynthBusy(false);
    }
  };

  return (
    <div className="mx-compare-workbench" data-testid="model-comparison-panel">
      <p className="note">
        Enter or speak any clinical question to execute it concurrently on{' '}
        <b>Gemini 4 Hydrogen</b> (grounded narrative synthesis) and{' '}
        <b>Drex (drex-max-v1.1)</b> (Brier-calibrated epistemic probabilities), and hear the output in your matched voice.
      </p>

      <div className="ask-box">
        <textarea
          aria-label="Comparison question"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Ask any clinical, lab, symptom, or medication question to run on both Gemini and Drex…"
        />
        <button
          type="button"
          className="primary"
          disabled={!prompt.trim() || loading}
          onClick={() => void runComparison(prompt)}
        >
          {loading ? 'Running both…' : 'Run Gemini + Drex'}
        </button>
      </div>

      {/* Voice Clone & Matched-Voice Controls */}
      <div className="mx-voice-bar">
        <div className="mx-voice-bar-btns">
          <button
            type="button"
            className={recording ? 'primary sm' : 'secondary sm'}
            disabled={loading}
            onClick={() => void handleToggleMic()}
          >
            {recording ? 'Stop & Run in My Voice' : 'Speak Question (Clone My Voice)'}
          </button>

          <label className="secondary sm mx-file-btn">
            <span>Upload voice audio</span>
            <input
              type="file"
              accept="audio/*"
              disabled={loading}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void handleProcessVoiceBlob(f);
              }}
            />
          </label>

          <button
            type="button"
            className="link"
            disabled={loading}
            onClick={() => void handleSampleVoiceRun()}
          >
            Test with sample voice
          </button>
        </div>

        <label className="mx-voice-toggle">
          <input
            type="checkbox"
            checked={autoSpeak}
            onChange={(e) => setAutoSpeak(e.target.checked)}
          />
          <span>
            Speak output in my matched voice
            {voiceProfile
              ? ` (${voiceProfile.prebuilt_voice} · ${voiceProfile.pitch_hz || 175} Hz)`
              : ''}
          </span>
        </label>
      </div>

      {voiceStatus && <p className="note">{voiceStatus}</p>}

      {error && (
        <p className="note mx-err-note" role="alert">
          {error}
        </p>
      )}

      {loading && (
        <div className="card mx-status-line" role="status">
          <span className="ap-dots" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span>Running Gemini 4 Hydrogen and Drex drex-max-v1.1 side by side…</span>
        </div>
      )}

      {result && !loading && (
        <>
          {/* Matched-Voice Audio Output Player */}
          <div className="mx-matched-audio-strip">
            <div className="mx-drex-inline-meta">
              <b>Matched-voice audio output</b>
              <span aria-hidden="true">·</span>
              <span>
                {result.voice_used ||
                  (voiceProfile
                    ? `${voiceProfile.prebuilt_voice} · ${voiceProfile.pitch_hz || 175} Hz`
                    : 'Ready to speak in your voice')}
              </span>
            </div>
            <div className="mx-matched-audio-controls">
              {result.audio_wav_base64 && (
                <audio
                  controls
                  src={`data:audio/wav;base64,${result.audio_wav_base64}`}
                  className="mx-inline-audio"
                />
              )}
              <button
                type="button"
                className="secondary sm"
                disabled={synthBusy}
                onClick={() => void handleSpeakExistingResult()}
              >
                {synthBusy ? 'Synthesizing voice…' : 'Speak in my voice'}
              </button>
            </div>
          </div>

          {/* Side-by-Side Live Results */}
          <div className="grid2 mx-compare-cols">
            {/* Left: Gemini */}
            <section className="card mx-compare-card" aria-label="Gemini generative output">
              <div className="sec">
                <div>
                  <span className="mx-eyebrow">System-2 Generative Synthesis</span>
                  <h3>Gemini ({result.gemini.model})</h3>
                </div>
                <span className="note mx-tabular">
                  {result.gemini.latency_ms} ms · {result.gemini.word_count} words ·{' '}
                  {result.gemini.sources.length} citations
                </span>
              </div>
              <p className="mx-answer-text">{result.gemini.answer}</p>
              {result.gemini.sources.length > 0 && (
                <div className="mx-drex-inline-meta">
                  <span>Sources:</span>
                  {result.gemini.sources.slice(0, 3).map((s, idx) => (
                    <React.Fragment key={`${s.title}-${idx}`}>
                      {idx > 0 && <span aria-hidden="true">·</span>}
                      <span>{s.title}</span>
                    </React.Fragment>
                  ))}
                </div>
              )}
            </section>

            {/* Right: Drex */}
            <section className="card mx-compare-card" aria-label="Drex calibrated probabilities">
              <div className="sec">
                <div>
                  <span className="mx-eyebrow">System-1 Calibrated Epistemic Engine</span>
                  <h3>Drex ({result.drex.model})</h3>
                </div>
                <span className="note mx-tabular">
                  {result.drex.evaluation_time_ms
                    ? `${result.drex.evaluation_time_ms} ms eval (${result.drex.latency_ms} ms RTT)`
                    : `${result.drex.latency_ms} ms`}
                  {result.drex.input_tokens
                    ? ` · ${result.drex.input_tokens} in / ${result.drex.output_tokens ?? 0} out tok`
                    : ''}
                </span>
              </div>
              <DrexTriageSummaryBlock
                triage={result.drex.triage}
                heading="Typed probability distribution"
              />
            </section>
          </div>
        </>
      )}

      <div className="suggest">
        {BENCHMARK_PROMPTS.map((q) => (
          <button
            key={q}
            type="button"
            disabled={loading}
            onClick={() => {
              setPrompt(q);
              void runComparison(q);
            }}
          >
            {q}
          </button>
        ))}
      </div>

      {/* Architecture Comparison Matrix: Drex vs. Gemini vs. GPT-Class Chat LLMs */}
      <section className="card mx-compare-matrix-card" aria-label="Model architecture comparison">
        <div className="sec">
          <div>
            <span className="mx-eyebrow">Why Mutanex Pairs Both Architectures</span>
            <h3>Drex (drex-max-v1.1) vs. Gemini vs. GPT-Class Generative LLMs</h3>
          </div>
        </div>
        <div className="mx-matrix-wrap">
          <table className="mx-matrix-table">
            <thead>
              <tr>
                <th>Dimension</th>
                <th>Drex (drex-max-v1.1)</th>
                <th>Gemini (gemini-4-hydrogen)</th>
                <th>Standard GPT-Class Chat LLMs</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <b>Primary output</b>
                </td>
                <td>
                  Typed calibrated probabilities (<code>noul</code>, <code>score</code>,{' '}
                  <code>choice</code> summing to 100%)
                </td>
                <td>
                  Knowledge-Graph + Google Search grounded clinical prose &amp; TTS Voice Design
                </td>
                <td> Autoregressive free-text narrative tokens</td>
              </tr>
              <tr>
                <td>
                  <b>Calibration &amp; thresholds</b>
                </td>
                <td>
                  Brier-calibrated epistemic <span className="mx-tabular">P(yes)</span> — safe for
                  deterministic triage &amp; queue routing
                </td>
                <td>Qualitative prose nuance; explains context &amp; cites KDIGO / CPIC literature</td>
                <td>
                  Verbalized confidence (&ldquo;likely&rdquo;, &ldquo;possibly&rdquo;) shifts with
                  prompt phrasing
                </td>
              </tr>
              <tr>
                <td>
                  <b>Typical latency</b>
                </td>
                <td className="mx-tabular">
                  {result?.drex.evaluation_time_ms
                    ? `${result.drex.evaluation_time_ms} ms live`
                    : '130 – 220 ms'}
                </td>
                <td className="mx-tabular">
                  {result?.gemini.latency_ms
                    ? `${result.gemini.latency_ms} ms live`
                    : '1,200 – 3,500 ms'}
                </td>
                <td className="mx-tabular">1,500 – 4,500 ms</td>
              </tr>
              <tr>
                <td>
                  <b>Role in Mutanex Twin</b>
                </td>
                <td>
                  Safety gatekeeping, unverified lab report triage &amp; 30-day clinician risk
                  scoring
                </td>
                <td>
                  Multimodal lab OCR, grounded patient/doctor explanations &amp; matched-voice speech
                </td>
                <td>Unpaired prose alone cannot safely gate clinical escalation queues</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

const DEMO_SCENARIOS = [
  {
    id: 'renal_shift',
    title: 'Case 1 · Subtle Renal & Potassium Shift on Losartan',
    subtitle: 'eGFR 58 → 51 (-12%) · uACR 46 → 68 (+48%) · K+ 5.3 mmol/L · Ankle swelling',
    question:
      'My ankles are swollen and my uploaded lab report shows eGFR dropped from 58 to 51 with potassium 5.3 mmol/L while taking losartan 50 mg. Should my doctor review this within 48 hours?',
    whyDrexWins:
      'A generic chat LLM gives polite hedging prose ("talk to your doctor if symptoms persist"). Drex immediately outputs a calibrated high P(48h review) and routes to Nephrology in ~150 ms so the clinic queue flags the chart automatically, while Gemini explains the KDIGO ARB/potassium rationale and speaks it in your voice.',
  },
  {
    id: 'benign_fatigue',
    title: 'Case 2 · Benign Travel Fatigue with Normal Biomarkers',
    subtitle: '6 hours sleep after flight · Normal home BP 118/76 · No red-flag symptoms',
    question:
      'I slept 6 hours after an evening flight and feel mildly tired today, with home blood pressure 118/76 mmHg and no other symptoms. Does this need urgent medical review?',
    whyDrexWins:
      'False alarms burn out clinicians. Drex calibrates urgent review near single-digit probability and assigns Routine severity, preventing alert fatigue while Gemini reassures the member calmly.',
  },
  {
    id: 'pgx_statin',
    title: 'Case 3 · Pharmacogenomic SLCO1B1 Statin Selection',
    subtitle: 'LDL 144 mg/dL · SLCO1B1 decreased transporter variant · Starting lipid therapy',
    question:
      'My LDL cholesterol is 144 mg/dL and my twin genetics show SLCO1B1 decreased function. How does this affect choosing a statin dose with my cardiologist?',
    whyDrexWins:
      'Drex separates low 48h emergency urgency from high biomarker/medication link and Cardiology routing—distinguishing "not an ER visit" from "high-value medication safety review".',
  },
];

interface PipelineResults {
  compare?: CompareModelsData;
  feedVerify?: DrexFeedVerificationData;
  clinicalRisk?: DrexClinicalRiskData;
  audioWav?: string | null;
  voiceLabel?: string;
}

export function ProductDrexDemoModal({
  b,
  host,
  onClose,
  setDoctorMode,
}: {
  b: any;
  host: PortalHostBridge;
  onClose: () => void;
  setDoctorMode?: (on: boolean) => void;
}) {
  const { navigate } = usePortalNavigation();
  const [tab, setTab] = useState<'unique' | 'pipeline' | 'screens'>('unique');
  const [selectedScenarioId, setSelectedScenarioId] = useState(DEMO_SCENARIOS[0].id);
  const [caseRunning, setCaseRunning] = useState(false);
  const [caseResult, setCaseResult] = useState<CompareModelsData | null>(null);

  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [pipelineStep, setPipelineStep] = useState(0);
  const [pipelineData, setPipelineData] = useState<PipelineResults | null>(null);

  const activeScenario =
    DEMO_SCENARIOS.find((s) => s.id === selectedScenarioId) || DEMO_SCENARIOS[0];

  const voiceProfile = host.readStore<VoicePersonaData | null>(SHARED_VOICE_STORE_KEY, null);

  const runSelectedScenario = async (scenario = activeScenario) => {
    setCaseRunning(true);
    try {
      const res = await fetch(resolvePortalApiUrl('/v1/compare-models', host.apiBase), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: scenario.question,
          bundle: b,
          mode: 'patient',
          synthesize_audio: true,
          voice_profile: voiceProfile || undefined,
        }),
      });
      if (res.ok) {
        const data = (await res.json()) as CompareModelsData;
        setCaseResult(data);
      }
    } finally {
      setCaseRunning(false);
    }
  };

  useEffect(() => {
    void runSelectedScenario(DEMO_SCENARIOS[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runFullLivePipeline = async () => {
    setPipelineRunning(true);
    setPipelineStep(1);
    const out: PipelineResults = {};

    try {
      // Stage 1: Patient Check-in Ask + Gemini vs Drex Comparison + Matched Voice
      const cmpRes = await fetch(resolvePortalApiUrl('/v1/compare-models', host.apiBase), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: DEMO_SCENARIOS[0].question,
          bundle: b,
          mode: 'patient',
          synthesize_audio: true,
          voice_profile: voiceProfile || undefined,
        }),
      });
      if (cmpRes.ok) {
        out.compare = (await cmpRes.json()) as CompareModelsData;
        out.audioWav = out.compare.audio_wav_base64;
        out.voiceLabel = out.compare.voice_used;
      }

      // Stage 2: Feed Your Twin Unverified Report Gatekeeping
      setPipelineStep(2);
      const feedRes = await fetch(resolvePortalApiUrl('/v1/drex/verify-feed', host.apiBase), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bundle: b,
          entries: [
            {
              kind: 'lab',
              date: '2026-11-14',
              title: 'eGFR 51 mL/min/1.73m2',
              detail: 'Flag: low (-12.1% vs 58)',
              extracted: { name: 'eGFR', value: 51, flag: 'low', pctChange: -12.1 },
            },
            {
              kind: 'lab',
              date: '2026-11-14',
              title: 'Potassium 5.3 mmol/L',
              detail: 'Flag: high (+12.8% vs 4.7)',
              extracted: { name: 'Potassium', value: 5.3, flag: 'high', pctChange: 12.8 },
            },
            {
              kind: 'lab',
              date: '2026-11-14',
              title: 'uACR 68 mg/g',
              detail: 'Flag: high (+47.8% vs 46)',
              extracted: { name: 'uACR', value: 68, flag: 'high', pctChange: 47.8 },
            },
          ],
        }),
      });
      if (feedRes.ok) {
        out.feedVerify = (await feedRes.json()) as DrexFeedVerificationData;
      }

      // Stage 3: Doctor View Longitudinal 5-Question Risk Panel
      setPipelineStep(3);
      const riskRes = await fetch(resolvePortalApiUrl('/v1/drex/clinical-risk', host.apiBase), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bundle: b,
          pending_feed: [
            {
              kind: 'lab',
              date: '2026-11-14',
              title: 'eGFR 51 mL/min/1.73m2',
              extracted: { flag: 'low', pctChange: -12.1 },
            },
          ],
        }),
      });
      if (riskRes.ok) {
        out.clinicalRisk = (await riskRes.json()) as DrexClinicalRiskData;
      }

      setPipelineStep(4);
      setPipelineData(out);
      if (out.compare) {
        await playMatchedVoiceReply(out.audioWav, out.compare.gemini.answer, voiceProfile);
      }
    } finally {
      setPipelineRunning(false);
    }
  };

  const jumpToScreen = (hash: string, doctorMode: boolean) => {
    if (setDoctorMode) {
      setDoctorMode(doctorMode);
    }
    onClose();
    navigate(hash);
  };

  return (
    <div
      className="mx-tour-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Mutanex Whole-Product and Drex Unique Case Demo"
    >
      <div className="card mx-tour-modal mx-wide-modal">
        <div className="sec mx-tour-head">
          <div>
            <span className="mx-eyebrow">
              Mutanex Digital Twin · Gemini System-2 + Drex System-1 + Voice Cloning
            </span>
            <h2>Whole-Product Showcase and Drex Clinical Case</h2>
          </div>
          <button type="button" className="secondary sm" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="segc" role="tablist" aria-label="Demo sections">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'unique'}
            onClick={() => setTab('unique')}
          >
            1. Drex Unique Case vs. LLMs
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'pipeline'}
            onClick={() => setTab('pipeline')}
          >
            2. Live End-to-End Product Run
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'screens'}
            onClick={() => setTab('screens')}
          >
            3. Jump to Product Modules
          </button>
        </div>

        {tab === 'unique' && (
          <div className="mx-demo-body">
            <p className="note">
              <b>Why Drex is uniquely necessary in a Digital Twin:</b> Generative LLMs (GPT, Claude,
              Gemini) excel at reading lab PDFs, citing medical literature, and speaking empathetically,
              but free-form text probabilities are uncalibrated—you cannot safely trigger clinical
              alerts from prose like &ldquo;consider seeing a doctor.&rdquo; Drex (
              <code>drex-max-v1.1</code>) answers typed epistemic questions over the Twin state with
              calibrated probabilities in ~150 ms. Pick any clinical case below to see both run live:
            </p>

            <div className="mx-demo-case-list">
              {DEMO_SCENARIOS.map((sc) => {
                const active = sc.id === selectedScenarioId;
                return (
                  <button
                    key={sc.id}
                    type="button"
                    className={`mx-tour-ch${active ? ' is-current' : ''}`}
                    onClick={() => {
                      setSelectedScenarioId(sc.id);
                      void runSelectedScenario(sc);
                    }}
                  >
                    <b>{sc.title}</b>
                    <small>{sc.subtitle}</small>
                  </button>
                );
              })}
            </div>

            <div className="onb-where">
              <b>Clinical takeaway for this scenario</b>
              <span>{activeScenario.whyDrexWins}</span>
            </div>

            {caseRunning ? (
              <div className="mx-status-line" role="status">
                <span className="ap-dots" aria-hidden="true">
                  <i />
                  <i />
                  <i />
                </span>
                <span>Evaluating scenario live on Drex drex-max-v1.1 + Gemini 4 Hydrogen…</span>
              </div>
            ) : caseResult ? (
              <div className="grid2 mx-compare-cols">
                <div className="card mx-compare-card">
                  <div className="sec">
                    <b>Drex System-1 Gate ({caseResult.drex.model})</b>
                    <span className="note mx-tabular">
                      {caseResult.drex.evaluation_time_ms || caseResult.drex.latency_ms} ms
                    </span>
                  </div>
                  <DrexTriageSummaryBlock
                    triage={caseResult.drex.triage}
                    heading="Calibrated decision probabilities"
                  />
                </div>

                <div className="card mx-compare-card">
                  <div className="sec">
                    <b>Gemini System-2 + Matched Voice ({caseResult.gemini.model})</b>
                    <span className="note mx-tabular">{caseResult.gemini.latency_ms} ms</span>
                  </div>
                  <p className="mx-answer-text">{caseResult.gemini.answer}</p>
                  <div className="mx-matched-audio-controls">
                    {caseResult.audio_wav_base64 && (
                      <audio
                        controls
                        src={`data:audio/wav;base64,${caseResult.audio_wav_base64}`}
                        className="mx-inline-audio"
                      />
                    )}
                    <button
                      type="button"
                      className="secondary sm"
                      onClick={() =>
                        void playMatchedVoiceReply(
                          caseResult.audio_wav_base64,
                          caseResult.gemini.answer,
                          voiceProfile
                        )
                      }
                    >
                      Play in matched voice
                    </button>
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        )}

        {tab === 'pipeline' && (
          <div className="mx-demo-body">
            <div className="sec">
              <div>
                <h3>Execute All 4 Live Stages Across Mutanex</h3>
                <p className="note">
                  Runs real live requests across Check-in Triage, Lab Feed Gatekeeping, Physician
                  30-Day Risk Panel, and Matched-Voice Speech Synthesis.
                </p>
              </div>
              <button
                type="button"
                className="primary sm"
                disabled={pipelineRunning}
                onClick={() => void runFullLivePipeline()}
              >
                {pipelineRunning
                  ? `Running Stage ${pipelineStep} of 4…`
                  : 'Run Full Live Demo Pipeline'}
              </button>
            </div>

            {pipelineData ? (
              <ul className="rs-rows">
                {pipelineData.compare && (
                  <li>
                    <span>
                      <b>
                        1. Check-in &amp; Ask Triage ({pipelineData.compare.drex.model} +{' '}
                        {pipelineData.compare.gemini.model})
                      </b>
                      <small>
                        Urgent 48h review:{' '}
                        {formatProbPct(
                          pipelineData.compare.drex.triage.urgent_attention.probability
                        )}{' '}
                        · Severity: {pipelineData.compare.drex.triage.severity.label} · Specialty:{' '}
                        {humanizeChoiceKey(pipelineData.compare.drex.triage.specialty.choice)} ·
                        Drex {pipelineData.compare.drex.evaluation_time_ms || pipelineData.compare.drex.latency_ms} ms vs Gemini{' '}
                        {pipelineData.compare.gemini.latency_ms} ms
                      </small>
                    </span>
                    <span className="rs-val mx-tabular">Verified</span>
                  </li>
                )}
                {pipelineData.feedVerify && (
                  <li>
                    <span>
                      <b>2. Feed Your Twin · Unverified Lab Report Verification</b>
                      <small>
                        Actionable:{' '}
                        {formatProbPct(pipelineData.feedVerify.clinically_actionable.probability)}{' '}
                        · Prompt follow-up:{' '}
                        {formatProbPct(pipelineData.feedVerify.prompt_followup.probability)} ·
                        Queue: {pipelineData.feedVerify.review_priority.label} · Domain:{' '}
                        {humanizeChoiceKey(pipelineData.feedVerify.domain.choice)}
                      </small>
                    </span>
                    <span className="rs-val mx-tabular">
                      {pipelineData.feedVerify.evaluation_time_ms
                        ? `${pipelineData.feedVerify.evaluation_time_ms} ms`
                        : 'Verified'}
                    </span>
                  </li>
                )}
                {pipelineData.clinicalRisk && (
                  <li>
                    <span>
                      <b>3. Doctor View · 30-Day Longitudinal Risk Panel</b>
                      <small>
                        30d progression:{' '}
                        {formatProbPct(pipelineData.clinicalRisk.deterioration_30d.probability)} ·
                        Med/renal review:{' '}
                        {formatProbPct(
                          pipelineData.clinicalRisk.renal_or_med_adjustment.probability
                        )}{' '}
                        · Horizon: {pipelineData.clinicalRisk.followup_urgency.label}
                      </small>
                    </span>
                    <span className="rs-val mx-tabular">
                      {pipelineData.clinicalRisk.evaluation_time_ms
                        ? `${pipelineData.clinicalRisk.evaluation_time_ms} ms`
                        : 'Verified'}
                    </span>
                  </li>
                )}
                <li>
                  <span>
                    <b>4. Matched-Voice Audio Synthesis (Gemini 3.8 TTS + Acoustic F0 Match)</b>
                    <small>
                      {pipelineData.voiceLabel || 'Voice profile ready — click Replay to hear spoken summary'}
                    </small>
                  </span>
                  <button
                    type="button"
                    className="link"
                    onClick={() =>
                      void playMatchedVoiceReply(
                        pipelineData.audioWav,
                        pipelineData.compare?.gemini.answer || '',
                        voiceProfile
                      )
                    }
                  >
                    Replay audio
                  </button>
                </li>
              </ul>
            ) : (
              <p className="note">
                Click <b>Run Full Live Demo Pipeline</b> above to execute all 4 live product stages
                in sequence and hear the spoken synthesis.
              </p>
            )}
          </div>
        )}

        {tab === 'screens' && (
          <div className="mx-demo-body">
            <div className="mx-sandbox-chapters">
              <button
                type="button"
                className="mx-sandbox-chap"
                onClick={() => jumpToScreen('#/you/checkin:ask', false)}
              >
                <b>Ask + Voice Clone + Compare</b>
                <small>Speak in your voice, hear matched audio &amp; compare Gemini vs. Drex</small>
              </button>
              <button
                type="button"
                className="mx-sandbox-chap"
                onClick={() => jumpToScreen('#/you/feed', false)}
              >
                <b>Feed Your Twin + Lab OCR</b>
                <small>Upload lab report, see % deltas &amp; Drex verification probabilities</small>
              </button>
              <button
                type="button"
                className="mx-sandbox-chap"
                onClick={() => jumpToScreen('#/clinical', true)}
              >
                <b>Doctor View · Drex Risk Panel</b>
                <small>Physician CDSS overview with 5-question longitudinal Drex panel</small>
              </button>
              <button
                type="button"
                className="mx-sandbox-chap"
                onClick={() => jumpToScreen('#/me/preserve', false)}
              >
                <b>Preserve Yourself (3D + Voice)</b>
                <small>3D portrait likeness mesh + vocal pitch &amp; cadence persona studio</small>
              </button>
              <button
                type="button"
                className="mx-sandbox-chap"
                onClick={() => jumpToScreen('#/welcome', false)}
              >
                <b>7-Step Onboarding + Snapshot</b>
                <small>Adaptive intake, VCF genetics &amp; calibrated Drex intake snapshot</small>
              </button>
              <button
                type="button"
                className="mx-sandbox-chap"
                onClick={() => jumpToScreen('#/research', false)}
              >
                <b>Research &amp; D3 Knowledge Graph</b>
                <small>Interactive evidence network &amp; therapeutic target findings</small>
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export function TwinDrexShowcaseTile({
  b,
  host,
  setDoctorMode,
}: {
  b: any;
  host: PortalHostBridge;
  setDoctorMode?: (on: boolean) => void;
}) {
  const { navigate } = usePortalNavigation();
  const [openDemo, setOpenDemo] = useState(false);

  return (
    <>
      <div className="bt wide mx-drex-bento" data-testid="twin-drex-showcase">
        <div className="mx-drex-bento-top">
          <span className="bt-l">Calibrated AI + Matched Voice</span>
          <span className="note mx-tabular">Gemini 4 + Drex v1.1</span>
        </div>
        <b>Interactive Product Demo, Voice-Matched Q&amp;A &amp; Model Comparison</b>
        <small>
          Compare Gemini narrative reasoning against Drex calibrated clinical probabilities side by
          side, and hear answers spoken back in your own matched voice.
        </small>
        <div className="mx-drex-bento-actions">
          <button
            type="button"
            className="primary sm"
            onClick={() => setOpenDemo(true)}
          >
            Launch Product &amp; Drex Demo
          </button>
          <button
            type="button"
            className="secondary sm"
            onClick={() => {
              try {
                sessionStorage.setItem('mx-ask-initial-mode', 'compare');
              } catch {
                // ignore
              }
              navigate('#/you/checkin:ask');
            }}
          >
            Compare Gemini vs. Drex
          </button>
        </div>
      </div>

      {openDemo && (
        <ProductDrexDemoModal
          b={b}
          host={host}
          onClose={() => setOpenDemo(false)}
          setDoctorMode={setDoctorMode}
        />
      )}
    </>
  );
}
