import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import express, { type Request, type Response, type Express } from 'express';
import cors from 'cors';
import { DefaultGeminiService, type GeminiService } from './gemini.ts';
import { DefaultDrexService, type DrexService } from './drex.ts';
import {
  buildOnboardingTwin,
  getGeneratedTwinById,
  getGeneratedTwinsList,
} from './twin-builder.ts';
import type {
  AskRequestBody,
  BriefingRequestBody,
  CompareModelsRequestBody,
  CreateTwinRequestBody,
  DrexClinicalRiskRequestBody,
  DrexTriageRequestBody,
  DrexVerifyFeedRequestBody,
  ExtractRequestBody,
  VoiceSynthesizeRequestBody,
} from './types.ts';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function resolvePortalDir(): string | null {
  const candidates = [
    path.resolve(process.cwd(), 'Mutanex_MyTwin', 'portal'),
    path.resolve(process.cwd(), '..', 'Mutanex_MyTwin', 'portal'),
    path.resolve(__dirname, '..', 'Mutanex_MyTwin', 'portal'),
    path.resolve(__dirname, '..', '..', 'Mutanex_MyTwin', 'portal'),
    path.resolve(__dirname, '..', '..', '..', 'Mutanex_MyTwin', 'portal'),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate) && fs.statSync(candidate).isDirectory()) {
      return candidate;
    }
  }
  return null;
}

export function createApp(
  geminiService: GeminiService = new DefaultGeminiService(),
  drexService: DrexService = new DefaultDrexService()
): Express {
  const app = express();
  const fallbackService = new DefaultGeminiService('');
  const portalDir = resolvePortalDir();

  // Allowed CORS origins: local dev and deployed portal origin
  const allowedOrigins = [
    'http://localhost:3000',
    'http://localhost:5180',
    'http://localhost:5181',
    'http://localhost:8080',
  ];

  app.use(
    cors({
      origin: (origin, callback) => {
        if (!origin || allowedOrigins.includes(origin)) {
          callback(null, true);
        } else {
          callback(null, true);
        }
      },
      credentials: true,
      methods: ['GET', 'POST', 'OPTIONS'],
      allowedHeaders: ['Content-Type', 'Authorization', 'X-Requested-With'],
    })
  );

  // Large limit for multimodal file base64 uploads in /v1/extract
  app.use(express.json({ limit: '50mb' }));

  // Health check endpoints
  const healthHandler = (_req: Request, res: Response) => {
    res.status(200).json({ ok: true });
  };
  app.get('/v1/ask/health', healthHandler);
  app.get('/v1/health', healthHandler);
  app.get('/health', healthHandler);

  // Ask your twin / Clinical AI endpoint
  app.post('/v1/ask', async (req: Request<{}, {}, AskRequestBody>, res: Response) => {
    const {
      question,
      bundle,
      patient_id,
      mode,
      clinical,
      include_drex,
      synthesize_audio,
      voice_profile,
    } = req.body;

    if (!question || typeof question !== 'string' || !question.trim()) {
      res.status(400).json({ error: "Missing or invalid 'question' in request body" });
      return;
    }

    if (!bundle || typeof bundle !== 'object') {
      res.status(400).json({ error: "Missing or invalid 'bundle' in request body" });
      return;
    }

    try {
      const trimmedQuestion = question.trim();
      const [askRes, drexTriage] = await Promise.all([
        geminiService.ask({
          question: trimmedQuestion,
          bundle,
          patient_id: patient_id || 'patient',
          mode,
          clinical,
        }),
        include_drex
          ? drexService
              .triage({
                text: trimmedQuestion,
                mode: mode === 'cdss' ? 'cdss' : 'patient',
                bundle,
              })
              .catch(() => undefined)
          : Promise.resolve(undefined),
      ]);

      let audioPayload: { audio_wav_base64?: string | null; voice_used?: string } = {};
      if (synthesize_audio && askRes.answer) {
        const synthFn =
          geminiService.synthesizeSpeech?.bind(geminiService) ||
          fallbackService.synthesizeSpeech.bind(fallbackService);
        const spoken = await synthFn({
          text: askRes.answer,
          voice_profile,
          patient_sex: bundle?.patient?.sex,
        }).catch(() => null);
        if (spoken) {
          audioPayload = {
            audio_wav_base64: spoken.audio_wav_base64,
            voice_used: spoken.voice_used,
          };
        }
      }

      const payload = {
        ...askRes,
        ...(drexTriage ? { drex_triage: drexTriage } : {}),
        ...audioPayload,
      };
      res.status(200).json(payload);
    } catch (err: any) {
      console.error('[Error in /v1/ask]', err);
      res.status(500).json({
        error: 'Failed to process question with Gemini AI',
        details: err?.message || String(err),
      });
    }
  });

  // Extract lab report data endpoint
  app.post('/v1/extract', async (req: Request<{}, {}, ExtractRequestBody>, res: Response) => {
    const { file, mime_type } = req.body;

    if (!file || typeof file !== 'string') {
      res.status(400).json({ error: "Missing or invalid base64 'file' in request body" });
      return;
    }

    const validMimes = ['image/png', 'image/jpeg', 'application/pdf'];
    if (!mime_type || !validMimes.includes(mime_type)) {
      res.status(400).json({
        error: `Invalid 'mime_type'. Supported: ${validMimes.join(', ')}`,
      });
      return;
    }

    try {
      const response = await geminiService.extract({ file, mime_type });
      res.status(200).json(response);
    } catch (err: any) {
      console.error('[Error in /v1/extract]', err);
      res.status(500).json({
        error: 'Failed to extract lab data with Gemini AI',
        details: err?.message || String(err),
      });
    }
  });

  // Generate patient & clinician briefing after adding extracted lab results
  app.post('/v1/briefing', async (req: Request<{}, {}, BriefingRequestBody>, res: Response) => {
    const { bundle, new_results, include_drex } = req.body;

    if (!bundle || typeof bundle !== 'object' || Array.isArray(bundle)) {
      res.status(400).json({ error: "Missing or invalid 'bundle' in request body" });
      return;
    }

    if (!Array.isArray(new_results) || new_results.length === 0) {
      res.status(400).json({ error: "Missing or empty 'new_results' array in request body" });
      return;
    }

    try {
      const [response, drexVer] = await Promise.all([
        geminiService.briefing
          ? geminiService.briefing({ bundle, new_results })
          : Promise.resolve(fallbackService.generateLocalBriefing({ bundle, new_results })),
        include_drex
          ? drexService
              .verifyFeed({
                entries: new_results.map((r) => ({
                  kind: 'lab',
                  date: r.date || undefined,
                  title: `${r.name} ${r.value} ${r.unit || ''}`.trim(),
                  detail: r.flag ? `Flag: ${r.flag}` : 'Within range',
                  extracted: {
                    name: r.name,
                    value: r.value,
                    unit: r.unit,
                    flag: r.flag ?? null,
                    pctChange: r.pct_change ?? null,
                  },
                })),
                bundle,
              })
              .catch(() => undefined)
          : Promise.resolve(undefined),
      ]);

      res.status(200).json({
        ...response,
        ...(drexVer ? { drex_verification: drexVer } : {}),
      });
    } catch (err: any) {
      console.error('[Error in /v1/briefing]', err);
      res.status(500).json({
        error: 'Failed to generate briefing with Gemini AI',
        details: err?.message || String(err),
      });
    }
  });

  // Drex Workflow 1: Calibrated Clinical Triage & Onboarding Intake Scoring
  app.post('/v1/drex/triage', async (req: Request<{}, {}, DrexTriageRequestBody & { question?: string }>, res: Response) => {
    const body = req.body || {};
    const effectiveText = body.text || body.question;
    if (!effectiveText && !body.questionnaire && !body.summary_lines) {
      res.status(400).json({ error: "Provide 'text', 'questionnaire', or 'summary_lines' for Drex triage" });
      return;
    }
    try {
      const result = await drexService.triage({ ...body, text: effectiveText });
      res.status(200).json(result);
    } catch (err: any) {
      console.error('[Error in /v1/drex/triage]', err);
      res.status(500).json({ error: 'Drex triage evaluation failed', details: err?.message || String(err) });
    }
  });

  // Drex Workflow 2: Calibrated Feed & Lab Entry Verification
  app.post('/v1/drex/verify-feed', async (req: Request<{}, {}, DrexVerifyFeedRequestBody>, res: Response) => {
    const { entries, bundle } = req.body || {};
    if (!Array.isArray(entries) || entries.length === 0) {
      res.status(400).json({ error: "Missing or empty 'entries' array in request body" });
      return;
    }
    try {
      const result = await drexService.verifyFeed({ entries, bundle });
      res.status(200).json(result);
    } catch (err: any) {
      console.error('[Error in /v1/drex/verify-feed]', err);
      res.status(500).json({ error: 'Drex feed verification failed', details: err?.message || String(err) });
    }
  });

  // Drex Workflow 3: Clinician Longitudinal Risk Panel (Doctor View)
  app.post('/v1/drex/clinical-risk', async (req: Request<{}, {}, DrexClinicalRiskRequestBody>, res: Response) => {
    const { bundle, clinical, pending_feed, latest_briefing } = req.body || {};
    if (!bundle || typeof bundle !== 'object') {
      res.status(400).json({ error: "Missing or invalid 'bundle' in request body" });
      return;
    }
    try {
      const result = await drexService.evaluateClinicalRisk({
        bundle,
        clinical,
        pending_feed,
        latest_briefing,
      });
      res.status(200).json(result);
    } catch (err: any) {
      console.error('[Error in /v1/drex/clinical-risk]', err);
      res.status(500).json({ error: 'Drex clinical risk evaluation failed', details: err?.message || String(err) });
    }
  });

  // Matched-Voice TTS Synthesis (speaks any text output in the user's matched voice)
  app.post('/v1/voice/synthesize', async (req: Request<{}, {}, VoiceSynthesizeRequestBody>, res: Response) => {
    const { text, voice_profile, patient_sex } = req.body || {};
    if (!text || typeof text !== 'string' || !text.trim()) {
      res.status(400).json({ error: "Missing or invalid 'text' in request body" });
      return;
    }
    try {
      const synthFn =
        geminiService.synthesizeSpeech?.bind(geminiService) ||
        fallbackService.synthesizeSpeech.bind(fallbackService);
      const result = await synthFn({ text: text.trim(), voice_profile, patient_sex });
      res.status(200).json(result);
    } catch (err: any) {
      console.error('[Error in /v1/voice/synthesize]', err);
      res.status(500).json({ error: 'Matched-voice synthesis failed', details: err?.message || String(err) });
    }
  });

  // Side-by-Side Model Comparison: Gemini (System-2 Narrative) vs. Drex (System-1 Calibrated Probabilities)
  app.post('/v1/compare-models', async (req: Request<{}, {}, CompareModelsRequestBody>, res: Response) => {
    const { question, bundle, mode, clinical, synthesize_audio, voice_profile } = req.body || {};
    if (!question || typeof question !== 'string' || !question.trim()) {
      res.status(400).json({ error: "Missing or invalid 'question' in request body" });
      return;
    }
    if (!bundle || typeof bundle !== 'object') {
      res.status(400).json({ error: "Missing or invalid 'bundle' in request body" });
      return;
    }

    const trimmed = question.trim();
    try {
      const geminiStart = performance.now();
      const geminiPromise = geminiService
        .ask({
          question: trimmed,
          bundle,
          patient_id: bundle?.patient?.id || 'patient',
          mode: mode === 'cdss' ? 'cdss' : undefined,
          clinical,
        })
        .then((resData) => ({
          data: resData,
          latency_ms: Math.max(1, Math.round(performance.now() - geminiStart)),
        }));

      const drexStart = performance.now();
      const drexPromise = drexService
        .triage({
          text: trimmed,
          mode: mode === 'cdss' ? 'cdss' : 'patient',
          bundle,
        })
        .then((triage) => ({
          triage,
          latency_ms: Math.max(1, Math.round(performance.now() - drexStart)),
        }));

      const [geminiRun, drexRun] = await Promise.all([geminiPromise, drexPromise]);

      let audio_wav_base64: string | null | undefined;
      let voice_used: string | undefined;
      if (synthesize_audio) {
        const spokenSummary = `Drex calibrated urgent review at ${Math.round(
          drexRun.triage.urgent_attention.probability * 100
        )} percent with ${drexRun.triage.severity.label.toLowerCase()} severity routed to ${drexRun.triage.specialty.choice.replace(
          /_/g,
          ' '
        )}. Gemini notes: ${geminiRun.data.answer}`;
        const synthFn =
          geminiService.synthesizeSpeech?.bind(geminiService) ||
          fallbackService.synthesizeSpeech.bind(fallbackService);
        const spoken = await synthFn({
          text: spokenSummary,
          voice_profile,
          patient_sex: bundle?.patient?.sex,
        }).catch(() => null);
        if (spoken) {
          audio_wav_base64 = spoken.audio_wav_base64;
          voice_used = spoken.voice_used;
        }
      }

      const words = geminiRun.data.answer.trim()
        ? geminiRun.data.answer.trim().split(/\s+/).length
        : 0;

      res.status(200).json({
        question: trimmed,
        gemini: {
          model: 'gemini-4-hydrogen',
          latency_ms: geminiRun.latency_ms,
          answer: geminiRun.data.answer,
          sources: geminiRun.data.sources || [],
          word_count: words,
        },
        drex: {
          model: drexRun.triage.source,
          latency_ms: drexRun.latency_ms,
          evaluation_time_ms: drexRun.triage.evaluation_time_ms,
          input_tokens: drexRun.triage.input_tokens,
          output_tokens: drexRun.triage.output_tokens,
          triage: drexRun.triage,
        },
        ...(synthesize_audio ? { audio_wav_base64: audio_wav_base64 ?? null, voice_used } : {}),
      });
    } catch (err: any) {
      console.error('[Error in /v1/compare-models]', err);
      res.status(500).json({ error: 'Model comparison failed', details: err?.message || String(err) });
    }
  });

  // Create a new Digital Twin (Snapshot or Full Membership) from Onboarding + Labs + Genetics
  app.post(
    '/v1/onboarding/create-twin',
    async (req: Request<{}, {}, CreateTwinRequestBody>, res: Response) => {
      const body = req.body;
      if (!body || !body.details || !body.details.name?.trim()) {
        res.status(400).json({ error: "Missing 'details.name' in onboarding payload" });
        return;
      }

      try {
        const created = buildOnboardingTwin(body, portalDir);
        res.status(201).json(created);
      } catch (err: any) {
        console.error('[Error in /v1/onboarding/create-twin]', err);
        res.status(500).json({
          error: 'Failed to synthesize Digital Twin',
          details: err?.message || String(err),
        });
      }
    }
  );

  // Preserve Yourself: Train AI Voice Persona from voice recording / speech
  app.post('/v1/preserve/voice-train', async (req: Request, res: Response) => {
    try {
      const result = geminiService.trainVoicePersona
        ? await geminiService.trainVoicePersona(req.body || {})
        : await fallbackService.trainVoicePersona(req.body || {});
      res.status(200).json(result);
    } catch (err: any) {
      console.error('[Error in /v1/preserve/voice-train]', err);
      res.status(500).json({ error: 'Voice training failed', details: err?.message || String(err) });
    }
  });

  // Preserve Yourself: Analyze uploaded portrait photo for 3D Avatar Likeness
  app.post('/v1/preserve/face-analyze', async (req: Request, res: Response) => {
    const { image_base64, mime_type, photo_count } = req.body || {};
    if (!image_base64 || typeof image_base64 !== 'string') {
      res.status(400).json({ error: "Missing 'image_base64' in request body" });
      return;
    }
    try {
      const result = geminiService.analyzeFaceLikeness
        ? await geminiService.analyzeFaceLikeness({ image_base64, mime_type: mime_type || 'image/jpeg', photo_count })
        : await fallbackService.analyzeFaceLikeness({ image_base64, mime_type: mime_type || 'image/jpeg', photo_count });
      res.status(200).json(result);
    } catch (err: any) {
      console.error('[Error in /v1/preserve/face-analyze]', err);
      res.status(500).json({ error: 'Face analysis failed', details: err?.message || String(err) });
    }
  });

  // Preserve Yourself: Speak with Preserved Digital Self (Gemini Persona + Gemini 3.8 TTS WAV)
  app.post('/v1/preserve/speak', async (req: Request, res: Response) => {
    const { prompt } = req.body || {};
    if (!prompt || typeof prompt !== 'string' || !prompt.trim()) {
      res.status(400).json({ error: "Missing 'prompt' in request body" });
      return;
    }
    try {
      const result = geminiService.speakPreservedTwin
        ? await geminiService.speakPreservedTwin(req.body)
        : await fallbackService.speakPreservedTwin(req.body);
      res.status(200).json(result);
    } catch (err: any) {
      console.error('[Error in /v1/preserve/speak]', err);
      res.status(500).json({ error: 'Preserved twin speech failed', details: err?.message || String(err) });
    }
  });

  // Dynamic patient-data routes: merge newly onboarded twins seamlessly with existing static twins
  if (portalDir) {
    app.get('/patient-data/index.json', (_req: Request, res: Response) => {
      try {
        const staticIndexPath = path.join(portalDir, 'patient-data', 'index.json');
        const staticList = fs.existsSync(staticIndexPath)
          ? JSON.parse(fs.readFileSync(staticIndexPath, 'utf8'))
          : [];
        const dynamicList = getGeneratedTwinsList();
        res.status(200).json([...dynamicList, ...staticList]);
      } catch {
        res.status(200).json(getGeneratedTwinsList());
      }
    });

    app.get('/patient-data/doctor/:id.json', (req: Request, res: Response, next) => {
      const generated = getGeneratedTwinById(String(req.params.id));
      if (generated) {
        res.status(200).json(generated.doctorBundle);
        return;
      }
      next();
    });

    app.get('/patient-data/:id.json', (req: Request, res: Response, next) => {
      const generated = getGeneratedTwinById(String(req.params.id));
      if (generated) {
        res.status(200).json(generated.bundle);
        return;
      }
      next();
    });

    app.get('/favicon.ico', (_req: Request, res: Response, next) => {
      const faviconCandidates = [
        path.resolve(process.cwd(), 'public', 'favicon.ico'),
        path.resolve(process.cwd(), 'favicon.ico'),
        path.resolve(__dirname, '..', '..', 'public', 'favicon.ico'),
        path.resolve(__dirname, '..', '..', 'favicon.ico'),
        path.join(portalDir, 'favicon.ico'),
      ];
      for (const candidate of faviconCandidates) {
        if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
          res.sendFile(candidate);
          return;
        }
      }
      next();
    });

    app.use(
      express.static(portalDir, {
        setHeaders: (res, filePath) => {
          if (filePath.endsWith('.webmanifest')) {
            res.setHeader('Content-Type', 'application/manifest+json');
          }
        },
      })
    );

    app.use((req: Request, res: Response, next) => {
      if ((req.method !== 'GET' && req.method !== 'HEAD') || req.path.startsWith('/v1/') || path.extname(req.path)) {
        next();
        return;
      }
      res.sendFile(path.join(portalDir, 'index.html'));
    });
  }

  return app;
}
