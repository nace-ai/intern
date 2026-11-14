import React, { useEffect, useRef, useState } from 'react';
import { BRIEFING_STORAGE_KEY } from './feed';
import { KnowledgeGraphCard } from './kg-graph';
import {
  DrexTriageSummaryBlock,
  ModelComparisonPanel,
  ProductDrexDemoModal,
} from './model-comparison';
import { createSampleVoiceWavBase64, type VoicePersonaData } from './preserve-math';
import type {
  AskSource,
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

const SOURCE_LABELS: Record<string, string> = {
  web: 'Google Search',
  kg: 'Medical evidence',
  record: 'Your record',
  finding: 'Finding for you',
};

function extractDomain(url?: string): string {
  if (!url) return '';
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
}

interface ChatTurn {
  me: string;
  a: string;
  links: string[];
  sources?: AskSource[];
  drexTriage?: DrexTriageData;
  audioWavBase64?: string | null;
  voiceUsed?: string;
  live?: boolean;
  cdss?: boolean;
  pending?: boolean;
  crisis?: boolean;
}

export function CitationsList({
  sources,
  openFinding,
  insights,
}: {
  sources: AskSource[];
  openFinding?: (id: string) => void;
  insights: any[];
}) {
  if (!sources || sources.length === 0) return null;

  return (
    <div className="mx-citations-block" aria-label="Grounded sources and citations">
      <span className="mx-citations-heading">Sources &amp; Grounded Citations</span>
      <ul className="ask-src mx-ask-src">
        {sources.slice(0, 8).map((src, idx) => {
          const badge = SOURCE_LABELS[src.kind] || src.kind;
          const domain = extractDomain(src.url);
          const canOpenFinding =
            Boolean(src.finding_id) &&
            Boolean(openFinding) &&
            insights.some((ins) => ins.id === src.finding_id);

          if (src.url) {
            return (
              <li key={`${src.title}-${idx}`} data-k={src.kind}>
                <a
                  href={src.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mx-citation-link"
                >
                  <span className="mx-citation-top">
                    <i>{badge}</i>
                    {domain && <small className="mx-citation-host">{domain} ↗</small>}
                  </span>
                  <span className="mx-citation-title">{src.title}</span>
                  {src.detail && <small className="mx-citation-detail">{src.detail}</small>}
                </a>
              </li>
            );
          }

          if (canOpenFinding) {
            return (
              <li key={`${src.title}-${idx}`} data-k="finding">
                <button
                  type="button"
                  className="mx-citation-btn"
                  onClick={() => openFinding!(src.finding_id!)}
                >
                  <span className="mx-citation-top">
                    <i>{badge}</i>
                    <small className="mx-citation-host">Open finding →</small>
                  </span>
                  <span className="mx-citation-title">{src.title}</span>
                  {src.detail && <small className="mx-citation-detail">{src.detail}</small>}
                </button>
              </li>
            );
          }

          return (
            <li key={`${src.title}-${idx}`} data-k={src.kind}>
              <span className="mx-citation-static">
                <span className="mx-citation-top">
                  <i>{badge}</i>
                </span>
                <span className="mx-citation-title">{src.title}</span>
                {src.detail && <small className="mx-citation-detail">{src.detail}</small>}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function AskPanel({
  b,
  openFinding,
  embedded = false,
  initial,
  host,
}: {
  b: any;
  openFinding: (id: string) => void;
  embedded?: boolean;
  initial?: string;
  host: PortalHostBridge;
}) {
  const [question, setQuestion] = useState('');
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [serverLive, setServerLive] = useState(true);
  const [showKg, setShowKg] = useState(!embedded);
  const [doctorDoc, setDoctorDoc] = useState<any>(null);
  const [viewMode, setViewMode] = useState<'ask' | 'compare'>(() => {
    try {
      const initMode = sessionStorage.getItem('mx-ask-initial-mode');
      if (initMode === 'compare') {
        sessionStorage.removeItem('mx-ask-initial-mode');
        return 'compare';
      }
    } catch {
      // ignore
    }
    return 'ask';
  });
  const [showDemoModal, setShowDemoModal] = useState(false);

  // Voice cloning & matched-voice synthesis state
  const [voiceProfile, setVoiceProfile] = useState<VoicePersonaData | null>(() =>
    host.readStore<VoicePersonaData | null>(SHARED_VOICE_STORE_KEY, null)
  );
  const [autoSpeakReply, setAutoSpeakReply] = useState<boolean>(() =>
    Boolean(host.readStore<VoicePersonaData | null>(SHARED_VOICE_STORE_KEY, null))
  );
  const [isRecordingVoice, setIsRecordingVoice] = useState(false);
  const [voiceStatusMsg, setVoiceStatusMsg] = useState<string | null>(null);
  const [speakingTurnIdx, setSpeakingTurnIdx] = useState<number | null>(null);
  const mediaRecRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  const isDoctor = host.useDoctor ? host.useDoctor() : false;
  const patientName = b?.patient?.preferred_name || 'Member';

  useEffect(() => {
    if (host.checkHealth) {
      host.checkHealth().then(setServerLive).catch(() => setServerLive(false));
    }
  }, [host]);

  useEffect(() => {
    if (isDoctor && host.loadDoctorDoc) {
      host.loadDoctorDoc().then(setDoctorDoc).catch(() => setDoctorDoc(null));
    }
  }, [isDoctor, host]);

  useEffect(() => {
    const syncVoice = () => {
      setVoiceProfile(host.readStore<VoicePersonaData | null>(SHARED_VOICE_STORE_KEY, null));
    };
    window.addEventListener(VOICE_UPDATED_EVENT, syncVoice);
    return () => window.removeEventListener(VOICE_UPDATED_EVENT, syncVoice);
  }, [host]);

  const persistVoiceProfile = (profile: VoicePersonaData) => {
    setVoiceProfile(profile);
    host.writeStore(SHARED_VOICE_STORE_KEY, profile);
    window.dispatchEvent(new Event(VOICE_UPDATED_EVENT));
  };

  const localFallbackAnswer = (q: string): ChatTurn => {
    const qLow = q.toLowerCase();
    let best: any = null;
    let bestScore = 0;
    for (const item of b?.ask || []) {
      const score = (item.keys || []).reduce(
        (acc: number, k: string) => acc + (qLow.includes(k) ? k.length : 0),
        0
      );
      if (score > bestScore) {
        bestScore = score;
        best = item;
      }
    }
    if (best) {
      return {
        me: q,
        a: best.a,
        links: best.insights || [],
        sources: [{ kind: 'record', title: `${b?.patient?.preferred_name || 'Twin'} record` }],
      };
    }
    return {
      me: q,
      a: "That isn't in your record yet, so I won't guess. Your care team can answer it. If it is about a part of your body your twin hasn't assessed, your personalised panel is how we find out.",
      links: [],
    };
  };

  const askQuestion = async (
    rawQ: string,
    opts?: { forceSpeak?: boolean; profileOverride?: VoicePersonaData | null }
  ) => {
    const trimmed = rawQ.trim();
    if (!trimmed) return;

    if (host.crisisRegex && host.crisisRegex.test(trimmed)) {
      setTurns((prev) => [...prev, { me: trimmed, a: '', links: [], crisis: true }]);
      return;
    }

    setTurns((prev) => [...prev, { me: trimmed, a: '', links: [], pending: true }]);

    const pendingFeed = host.readStore<FeedEntry[]>('mx-feed', []);
    const latestBriefing = host.readStore<StoredBriefing | null>(BRIEFING_STORAGE_KEY, null);
    const enrichedBundle = {
      ...b,
      pending_feed: pendingFeed,
      latest_briefing: latestBriefing,
    };

    const patientId =
      String(host.getPatientId() || 'patient').replace(/[^A-Za-z0-9_-]/g, '') || 'patient';

    let clinicalDoc = doctorDoc;
    if (isDoctor && !clinicalDoc && host.loadDoctorDoc) {
      clinicalDoc = await host.loadDoctorDoc();
      setDoctorDoc(clinicalDoc);
    }

    const activeVoice = opts?.profileOverride ?? voiceProfile;
    const shouldSpeak = Boolean(opts?.forceSpeak || autoSpeakReply);

    let resolvedTurn: ChatTurn;
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 45000);
      const res = await fetch(resolvePortalApiUrl('/v1/ask', host.apiBase), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          patient_id: patientId,
          question: trimmed,
          bundle: enrichedBundle,
          include_drex: true,
          synthesize_audio: shouldSpeak,
          voice_profile: activeVoice || undefined,
          ...(isDoctor ? { mode: 'cdss', clinical: clinicalDoc } : {}),
        }),
      });
      clearTimeout(timer);

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const data = await res.json();
      const sources: AskSource[] = Array.isArray(data.sources) ? data.sources : [];
      const insightsList: any[] = Array.isArray(b?.insights) ? b.insights : [];
      const matchedFindingLinks = Array.from(
        new Set(
          sources
            .map((s) => s.finding_id)
            .filter((fid): fid is string => Boolean(fid) && insightsList.some((i) => i.id === fid))
        )
      );

      resolvedTurn = {
        me: trimmed,
        a: data.answer || '',
        links: matchedFindingLinks,
        sources,
        drexTriage: data.drex_triage,
        audioWavBase64: data.audio_wav_base64 ?? null,
        voiceUsed: data.voice_used,
        live: true,
        cdss: isDoctor,
      };
    } catch {
      resolvedTurn = localFallbackAnswer(trimmed);
    }

    setTurns((prev) => {
      const copy = [...prev];
      const idx = copy.findIndex((item) => item.pending && item.me === trimmed);
      if (idx >= 0) {
        copy[idx] = resolvedTurn;
      } else {
        copy.push(resolvedTurn);
      }
      return copy;
    });

    if (shouldSpeak && resolvedTurn.a) {
      await playMatchedVoiceReply(resolvedTurn.audioWavBase64, resolvedTurn.a, activeVoice);
    }
  };

  const handleVoiceAudioBlob = async (blob: Blob, fallbackHint?: string) => {
    setVoiceStatusMsg('Analyzing your voice pitch & cadence and transcribing question…');
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
        transcriptHint:
          fallbackHint ||
          question.trim() ||
          'What changed in my kidneys and should my doctor check it soon?',
        patientName,
        existingProfile: voiceProfile,
        apiBase: host.apiBase,
      });
      persistVoiceProfile(trained.profile);
      setAutoSpeakReply(true);
      const askedText =
        trained.transcript?.trim() ||
        question.trim() ||
        'What changed in my kidneys and should my doctor check it soon?';
      setQuestion('');
      setVoiceStatusMsg(
        `Matched your voice: ${trained.profile.prebuilt_voice} · ${
          trained.profile.pitch_hz || 175
        } Hz · Speaking answer in your voice`
      );
      await askQuestion(askedText, {
        forceSpeak: true,
        profileOverride: trained.profile,
      });
    } catch (err: any) {
      setVoiceStatusMsg(err?.message || 'Could not process voice recording.');
    }
  };

  const handleToggleMicQuestion = async () => {
    if (isRecordingVoice && mediaRecRef.current) {
      mediaRecRef.current.stop();
      setIsRecordingVoice(false);
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setVoiceStatusMsg('Microphone unavailable here — upload an audio file or try Sample voice.');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioChunksRef.current = [];
      const rec = new MediaRecorder(stream);
      mediaRecRef.current = rec;
      rec.ondataavailable = (ev) => {
        if (ev.data && ev.data.size > 0) audioChunksRef.current.push(ev.data);
      };
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const mime = rec.mimeType || 'audio/webm';
        const blob = new Blob(audioChunksRef.current, { type: mime });
        await handleVoiceAudioBlob(blob);
      };
      rec.start();
      setIsRecordingVoice(true);
      setVoiceStatusMsg('Recording… ask your question aloud, then click Stop & Ask.');
    } catch {
      setVoiceStatusMsg('Microphone permission declined — upload an audio clip or try Sample voice.');
    }
  };

  const handleSampleVoiceAsk = async () => {
    const promptToAsk =
      question.trim() || 'What changed in my kidneys and should my doctor review it soon?';
    setVoiceStatusMsg('Calibrating your vocal timbre & synthesizing spoken answer…');
    try {
      const sampleWav = createSampleVoiceWavBase64(0.85);
      const trained = await trainAndTranscribeUserVoice({
        audioBase64: sampleWav,
        mimeType: 'audio/wav',
        measuredPitchHz: 185,
        measuredBrightness: 60,
        transcriptHint: promptToAsk,
        patientName,
        existingProfile: voiceProfile,
        apiBase: host.apiBase,
      });
      persistVoiceProfile(trained.profile);
      setAutoSpeakReply(true);
      setQuestion('');
      setVoiceStatusMsg(
        `Voice matched (${trained.profile.prebuilt_voice} · ${
          trained.profile.pitch_hz || 185
        } Hz) — playing reply in matched voice`
      );
      await askQuestion(promptToAsk, {
        forceSpeak: true,
        profileOverride: trained.profile,
      });
    } catch (err: any) {
      setVoiceStatusMsg(err?.message || 'Sample voice run failed.');
    }
  };

  const handleSpeakTurn = async (turnIdx: number) => {
    const turn = turns[turnIdx];
    if (!turn || !turn.a || speakingTurnIdx !== null) return;
    if (turn.audioWavBase64) {
      await playMatchedVoiceReply(turn.audioWavBase64, turn.a, voiceProfile);
      return;
    }
    setSpeakingTurnIdx(turnIdx);
    try {
      const synth = await requestMatchedVoiceSynthesis({
        text: turn.a,
        voiceProfile,
        patientSex: b?.patient?.sex,
        apiBase: host.apiBase,
      });
      setTurns((prev) =>
        prev.map((item, idx) =>
          idx === turnIdx
            ? {
                ...item,
                audioWavBase64: synth.audio_wav_base64,
                voiceUsed: synth.voice_used,
              }
            : item
        )
      );
      await playMatchedVoiceReply(synth.audio_wav_base64, turn.a, voiceProfile);
    } finally {
      setSpeakingTurnIdx(null);
    }
  };

  useEffect(() => {
    if (initial && initial.trim()) {
      void askQuestion(initial);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial]);

  const submitCurrent = () => {
    if (!question.trim()) return;
    const q = question.trim();
    setQuestion('');
    void askQuestion(q);
  };

  const CrisisBanner = host.CrisisBanner;
  const cdssSuggestions = host.cdssPrompts || [
    'What should we check next?',
    'Treatment options and their evidence',
    'Drug interactions to review',
    'Results outside range',
  ];
  const patientSuggestions: string[] = [
    'What changed in my kidneys?',
    ...((b?.ask || []).map((x: any) => x.q) as string[]).slice(0, 6),
  ];

  const content = (
    <>
      {!embedded && <h1>{isDoctor ? 'Clinical assistant' : 'Ask your twin'}</h1>}
      <p className="lede">
        {isDoctor
          ? `Clinical decision support: grounded in ${
              b?.patient?.preferred_name || 'the patient'
            }’s Knowledge Graph, uploaded lab reports, and live Google Search clinical guidelines. For physician review; never diagnoses or prescribes.`
          : serverLive
            ? 'Live: your twin answers from your Knowledge Graph, newly added lab reports, and Google Search medical guidelines, with clickable sources.'
            : 'Answers come strictly from your own record and verified medical evidence.'}
      </p>

      {/* Segmented Workspace Switcher: Ask Twin vs. Side-by-Side Gemini & Drex Comparison */}
      <div className="mx-ask-mode-bar">
        <div className="segc" role="tablist" aria-label="Clinical AI workspace mode">
          <button
            type="button"
            role="tab"
            aria-selected={viewMode === 'ask'}
            onClick={() => setViewMode('ask')}
          >
            Ask Your Twin (Voice + Drex Triage)
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={viewMode === 'compare'}
            onClick={() => setViewMode('compare')}
          >
            Compare Models: Gemini vs. Drex
          </button>
        </div>
        <button
          type="button"
          className="link"
          onClick={() => setShowDemoModal(true)}
        >
          Product &amp; Drex Use-Case Demo
        </button>
      </div>

      {viewMode === 'compare' ? (
        <ModelComparisonPanel b={b} host={host} clinicalDoc={doctorDoc} />
      ) : (
        <>
          <div className="mx-kg-toggle-row">
            <button
              type="button"
              className="secondary sm mx-kg-toggle-btn"
              onClick={() => setShowKg((v) => !v)}
              aria-expanded={showKg}
            >
              {showKg ? 'Hide Knowledge Graph' : 'Show Patient Knowledge Graph'}
            </button>
            <span className="note">
              Gemini 4 + Drex v1.1 + Matched Voice active
            </span>
          </div>

          {showKg && (
            <div className="mx-ask-kg-wrap">
              <KnowledgeGraphCard
                b={b}
                doc={doctorDoc}
                readStore={host.readStore}
                openFinding={openFinding}
                compact={embedded}
              />
            </div>
          )}

          <div className="ask-box">
            <textarea
              aria-label="Your question"
              placeholder={
                isDoctor
                  ? 'Ask about next tests, treatment options, interactions, genes, or recent report changes...'
                  : 'Ask anything about your health, your kidneys, your lab reports, or speak in your voice below...'
              }
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  submitCurrent();
                }
              }}
            />
            <button className="primary" onClick={submitCurrent}>
              Ask
            </button>
          </div>

          {/* Voice Input & Matched-Voice Output Bar */}
          <div className="mx-voice-bar">
            <div className="mx-voice-bar-btns">
              <button
                type="button"
                className={isRecordingVoice ? 'primary sm' : 'secondary sm'}
                onClick={() => void handleToggleMicQuestion()}
              >
                {isRecordingVoice ? 'Stop & Ask in My Voice' : 'Speak Question (My Voice)'}
              </button>

              <label className="secondary sm mx-file-btn">
                <span>Upload voice</span>
                <input
                  type="file"
                  accept="audio/*"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void handleVoiceAudioBlob(f);
                  }}
                />
              </label>

              <button
                type="button"
                className="link"
                onClick={() => void handleSampleVoiceAsk()}
              >
                Ask with sample voice
              </button>
            </div>

            <label className="mx-voice-toggle">
              <input
                type="checkbox"
                checked={autoSpeakReply}
                onChange={(e) => setAutoSpeakReply(e.target.checked)}
              />
              <span>
                Speak reply in my voice
                {voiceProfile
                  ? ` (${voiceProfile.prebuilt_voice} · ${voiceProfile.pitch_hz || 175} Hz)`
                  : ''}
              </span>
            </label>
          </div>

          {voiceStatusMsg && <p className="note">{voiceStatusMsg}</p>}

          {turns.length === 0 && (
            <div className="suggest">
              {(isDoctor ? cdssSuggestions : patientSuggestions).map((prompt) => (
                <button key={prompt} onClick={() => void askQuestion(prompt)}>
                  {prompt}
                </button>
              ))}
            </div>
          )}

          <div className="mx-turns-stack">
            {turns.map((turn, idx) => (
              <div key={`${turn.me}-${idx}`} className="mx-turn-pair">
                <div className="bubble me">{turn.me}</div>

                {turn.crisis && CrisisBanner ? (
                  <CrisisBanner />
                ) : turn.pending ? (
                  <div className="bubble ask-pending">
                    <span className="ap-dots" aria-hidden="true">
                      <i />
                      <i />
                      <i />
                    </span>
                    Evaluating with Gemini Knowledge Graph + Drex calibrated triage…
                  </div>
                ) : (
                  <div className="bubble mx-answer-bubble">
                    <div className="mx-answer-top-bar">
                      {turn.cdss ? (
                        <span className="ask-live">Clinical decision support · Grounded + Drex</span>
                      ) : turn.live ? (
                        <span className="ask-live">Researched live · KG + Search + Drex v1.1</span>
                      ) : (
                        <span />
                      )}

                      <button
                        type="button"
                        className="secondary sm"
                        disabled={speakingTurnIdx === idx}
                        onClick={() => void handleSpeakTurn(idx)}
                      >
                        {speakingTurnIdx === idx
                          ? 'Synthesizing voice…'
                          : turn.audioWavBase64
                            ? 'Replay in my voice'
                            : 'Speak in my voice'}
                      </button>
                    </div>

                    <p className="mx-answer-text">{turn.a}</p>

                    {turn.audioWavBase64 && (
                      <div className="mx-matched-audio-controls">
                        <audio
                          controls
                          src={`data:audio/wav;base64,${turn.audioWavBase64}`}
                          className="mx-inline-audio"
                        />
                        {turn.voiceUsed && <small className="note">{turn.voiceUsed}</small>}
                      </div>
                    )}

                    {turn.drexTriage && (
                      <DrexTriageSummaryBlock
                        triage={turn.drexTriage}
                        heading="Calibrated clinical triage"
                      />
                    )}

                    {turn.sources && turn.sources.length > 0 && (
                      <CitationsList
                        sources={turn.sources}
                        openFinding={openFinding}
                        insights={b?.insights || []}
                      />
                    )}

                    {turn.links.length > 0 && (
                      <div className="suggest mx-insight-links">
                        {turn.links.map((fid) => (
                          <button key={fid} onClick={() => openFinding(fid)}>
                            Read: {b?.insights?.find((x: any) => x.id === fid)?.title || fid}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>

          {turns.length > 0 && (
            <div className="suggest">
              {(isDoctor ? cdssSuggestions : patientSuggestions)
                .filter((q) => !turns.some((t) => t.me === q))
                .slice(0, 4)
                .map((q) => (
                  <button key={q} onClick={() => void askQuestion(q)}>
                    {q}
                  </button>
                ))}
            </div>
          )}
        </>
      )}

      {showDemoModal && (
        <ProductDrexDemoModal
          b={b}
          host={host}
          onClose={() => setShowDemoModal(false)}
        />
      )}
    </>
  );

  if (embedded) {
    return <div className="ask-wrap">{content}</div>;
  }
  return <main className="screen">{content}</main>;
}
