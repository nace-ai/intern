/**
 * Pure formatting, calibration normalization, and time-domain voice acoustic helpers
 * decoupled from React DOM components so they can be unit-tested deterministically.
 */

const CHOICE_DISPLAY_LABELS: Record<string, string> = {
  nephrology: 'Nephrology',
  cardiology: 'Cardiology',
  endocrinology: 'Endocrinology',
  primary_care: 'Primary care',
  internal_medicine: 'Internal medicine',
  renal_electrolytes: 'Renal & electrolytes',
  cardiometabolic: 'Cardiometabolic',
  medication_safety: 'Medication safety',
  general_wellness: 'General wellness',
};

export function clampProbability(value: unknown, fallback = 0): number {
  const num = typeof value === 'number' && Number.isFinite(value) ? value : fallback;
  return Math.max(0, Math.min(1, num));
}

export function formatProbPct(probability: unknown, decimals = 1): string {
  const pct = clampProbability(probability, 0) * 100;
  if (decimals <= 0) {
    return `${Math.round(pct)}%`;
  }
  return `${pct.toFixed(decimals)}%`;
}

export function humanizeChoiceKey(key: string): string {
  if (!key) return 'General';
  if (CHOICE_DISPLAY_LABELS[key]) return CHOICE_DISPLAY_LABELS[key];
  return key
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

export function sortedChoiceProbabilities(
  probabilities: Record<string, number> | undefined | null
): Array<{ key: string; label: string; pct: number }> {
  if (!probabilities || typeof probabilities !== 'object') return [];
  return Object.entries(probabilities)
    .map(([key, raw]) => ({
      key,
      label: humanizeChoiceKey(key),
      pct: Number((clampProbability(raw, 0) * 100).toFixed(1)),
    }))
    .sort((a, b) => b.pct - a.pct);
}

export function scoreDistributionRows(
  legend: string[] | undefined | null,
  probabilities: Record<string, number> | undefined | null
): Array<{ index: number; label: string; pct: number }> {
  const safeLegend = Array.isArray(legend) ? legend : [];
  return safeLegend.map((label, index) => {
    const raw = probabilities ? probabilities[String(index)] : 0;
    return {
      index,
      label,
      pct: Number((clampProbability(raw, 0) * 100).toFixed(1)),
    };
  });
}

/**
 * Estimates the speaker's fundamental vocal pitch F0 (Hz) from PCM samples
 * using normalized time-domain autocorrelation across human voiced range (75..310 Hz).
 */
export function estimateAutocorrelationPitchHz(
  samples: Float32Array,
  sampleRate: number
): number | null {
  if (!samples || samples.length < 1024 || sampleRate < 4000) {
    return null;
  }

  const minLag = Math.max(8, Math.floor(sampleRate / 310));
  const maxLag = Math.min(samples.length >> 1, Math.ceil(sampleRate / 75));
  if (maxLag <= minLag) return null;

  const frameSize = Math.min(2048, samples.length - maxLag - 1);
  if (frameSize < 512) return null;

  const detectedF0s: number[] = [];
  const step = Math.max(512, Math.floor(samples.length / 8));

  for (let offset = 0; offset + frameSize + maxLag < samples.length; offset += step) {
    let rms = 0;
    for (let i = 0; i < frameSize; i++) {
      const s = samples[offset + i];
      rms += s * s;
    }
    rms = Math.sqrt(rms / frameSize);
    if (rms < 0.012) continue; // skip unvoiced/silent frame

    let bestCorr = 0;
    let bestLag = -1;

    for (let lag = minLag; lag <= maxLag; lag++) {
      let num = 0;
      let denA = 0;
      let denB = 0;
      for (let i = 0; i < frameSize; i += 2) {
        const a = samples[offset + i];
        const b = samples[offset + i + lag];
        num += a * b;
        denA += a * a;
        denB += b * b;
      }
      const denom = Math.sqrt(denA * denB);
      const corr = denom > 1e-8 ? num / denom : 0;
      if (corr > bestCorr) {
        bestCorr = corr;
        bestLag = lag;
      }
    }

    if (bestLag > 0 && bestCorr >= 0.45) {
      detectedF0s.push(sampleRate / bestLag);
    }
  }

  if (detectedF0s.length === 0) return null;
  detectedF0s.sort((a, b) => a - b);
  const median = detectedF0s[Math.floor(detectedF0s.length / 2)];
  return Math.round(median);
}

/**
 * Estimates vocal brightness (0..100) from zero-crossing density and first-difference energy.
 */
export function estimateSpectralBrightness(
  samples: Float32Array,
  sampleRate = 16000
): number {
  if (!samples || samples.length < 256) return 58;
  let crossings = 0;
  let prev = samples[0];
  for (let i = 1; i < samples.length; i++) {
    const cur = samples[i];
    if ((prev >= 0 && cur < 0) || (prev < 0 && cur >= 0)) {
      crossings++;
    }
    prev = cur;
  }
  const zcrHz = (crossings / samples.length) * (sampleRate / 2);
  const normalized = Math.round(35 + Math.min(60, (zcrHz / 1600) * 55));
  return Math.max(25, Math.min(95, normalized));
}
