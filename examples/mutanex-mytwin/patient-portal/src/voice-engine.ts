import {
  estimateAutocorrelationPitchHz,
  estimateSpectralBrightness,
} from './drex-utils';
import type { VoicePersonaData } from './preserve-math';

export const SHARED_VOICE_STORE_KEY = 'mx-preserve-voice-v1';
export const VOICE_UPDATED_EVENT = 'mx-voice-profile-updated';

const VITE_ASK_URL: string =
  (typeof import.meta !== 'undefined' && (import.meta as any).env?.VITE_ASK_URL) || '';

export function resolvePortalApiUrl(path: string, hostApiBase?: string): string {
  const base = (VITE_ASK_URL || hostApiBase || '').replace(/\/$/, '');
  return `${base}${path}`;
}

export function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const str = typeof reader.result === 'string' ? reader.result : '';
      const idx = str.indexOf(',');
      resolve(idx >= 0 ? str.slice(idx + 1) : str);
    };
    reader.onerror = () => reject(reader.error || new Error('Failed to read audio blob'));
    reader.readAsDataURL(blob);
  });
}

/**
 * Decodes a recorded microphone or uploaded audio Blob via Web Audio API
 * to measure the speaker's actual fundamental pitch F0 (Hz) and spectral brightness.
 */
export async function analyzeBlobAcoustics(
  blob: Blob
): Promise<{ pitchHz: number | null; brightness: number }> {
  try {
    const AudioCtx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtx) {
      return { pitchHz: null, brightness: 58 };
    }
    const arrayBuf = await blob.arrayBuffer();
    const ctx = new AudioCtx();
    try {
      const audioBuf = await ctx.decodeAudioData(arrayBuf.slice(0));
      const chan = audioBuf.getChannelData(0);
      const pitchHz = estimateAutocorrelationPitchHz(chan, audioBuf.sampleRate);
      const brightness = estimateSpectralBrightness(chan, audioBuf.sampleRate);
      return { pitchHz, brightness };
    } finally {
      await ctx.close().catch(() => {});
    }
  } catch {
    return { pitchHz: null, brightness: 58 };
  }
}

export async function trainAndTranscribeUserVoice(params: {
  audioBase64: string;
  mimeType: string;
  measuredPitchHz?: number | null;
  measuredBrightness?: number;
  transcriptHint?: string;
  patientName?: string;
  existingProfile?: VoicePersonaData | null;
  apiBase?: string;
}): Promise<{ transcript: string; profile: VoicePersonaData }> {
  const nextSamples = (params.existingProfile?.sample_count || 0) + 1;
  const res = await fetch(resolvePortalApiUrl('/v1/preserve/voice-train', params.apiBase), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      audio_base64: params.audioBase64,
      mime_type: params.mimeType,
      transcript_hint: params.transcriptHint,
      patient_name: params.patientName || 'Member',
      sample_count: nextSamples,
      measured_pitch_hz: params.measuredPitchHz ?? undefined,
      measured_brightness: params.measuredBrightness,
      existing_profile: params.existingProfile || undefined,
    }),
  });
  if (!res.ok) {
    throw new Error(`Voice transcription & cloning returned HTTP ${res.status}`);
  }
  return (await res.json()) as { transcript: string; profile: VoicePersonaData };
}

export async function requestMatchedVoiceSynthesis(params: {
  text: string;
  voiceProfile?: VoicePersonaData | null;
  patientSex?: string;
  apiBase?: string;
}): Promise<{ audio_wav_base64: string | null; voice_used: string; pitch_hz: number }> {
  const res = await fetch(resolvePortalApiUrl('/v1/voice/synthesize', params.apiBase), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text: params.text,
      voice_profile: params.voiceProfile || undefined,
      patient_sex: params.patientSex,
    }),
  });
  if (!res.ok) {
    throw new Error(`Matched-voice synthesis returned HTTP ${res.status}`);
  }
  return (await res.json()) as {
    audio_wav_base64: string | null;
    voice_used: string;
    pitch_hz: number;
  };
}

let activeAudioElement: HTMLAudioElement | null = null;

/**
 * Plays synthesized WAV audio matched to the user's voice profile (with browser
 * SpeechSynthesis pitch/rate fallback if offline or no WAV was returned).
 */
export async function playMatchedVoiceReply(
  wavBase64: string | null | undefined,
  fallbackText: string,
  voiceProfile?: VoicePersonaData | null
): Promise<void> {
  if (activeAudioElement) {
    activeAudioElement.pause();
    activeAudioElement = null;
  }

  if (wavBase64) {
    try {
      const audio = new Audio(`data:audio/wav;base64,${wavBase64}`);
      activeAudioElement = audio;
      if (voiceProfile?.cadence_wpm) {
        const rateRatio = Math.max(0.88, Math.min(1.16, voiceProfile.cadence_wpm / 138));
        audio.playbackRate = rateRatio;
      }
      await audio.play();
      return;
    } catch {
      // fall through to browser speech fallback if autoplay blocked or decode failed
    }
  }

  if (typeof window !== 'undefined' && 'speechSynthesis' in window && fallbackText.trim()) {
    try {
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(fallbackText.slice(0, 650));
      if (voiceProfile?.pitch_hz) {
        utter.pitch = Math.max(0.65, Math.min(1.45, voiceProfile.pitch_hz / 165));
      }
      if (voiceProfile?.cadence_wpm) {
        utter.rate = Math.max(0.85, Math.min(1.2, voiceProfile.cadence_wpm / 140));
      }
      window.speechSynthesis.speak(utter);
    } catch {
      // ignore if speechSynthesis unavailable in headless browser
    }
  }
}
