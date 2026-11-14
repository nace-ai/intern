export type SourceKind = 'record' | 'finding' | 'kg' | 'web';

export interface AskSource {
  kind: SourceKind;
  title: string;
  detail?: string;
  finding_id?: string;
  url?: string;
}

export interface LabPoint {
  date: string;
  value: number;
  unit: string;
  lab?: string;
  flag?: 'high' | 'low' | string | null;
  rule_evaluated?: boolean;
}

export interface ExtractedLabItem {
  name: string;
  value: number;
  unit: string;
  range: string | null;
  flag: 'high' | 'low' | null;
  date: string | null;
}

export interface ExtractApiResponse {
  report_date: string | null;
  lab: string | null;
  results: ExtractedLabItem[];
}

export interface MatchedLabRow extends ExtractedLabItem {
  id: string;
  kept: boolean;
  matchedLabKey: string | null;
  prevValue: number | null;
  prevUnit: string | null;
  prevDate: string | null;
  pctChange: number | null;
  arrow: '↑' | '↓' | '→' | null;
}

export type FeedKind =
  | 'lab'
  | 'measure'
  | 'medicine'
  | 'symptom'
  | 'family'
  | 'lifestyle'
  | 'report';

export interface DrexNoulMetric {
  probability: number;
  confidence: number;
}

export interface DrexScoreMetric {
  score: number;
  label: string;
  legend: string[];
  probabilities: Record<string, number>;
  confidence: number;
}

export interface DrexChoiceMetric {
  choice: string;
  probabilities: Record<string, number>;
  confidence: number;
}

export interface DrexTriageData {
  source: 'drex-max-v1.1' | 'calibrated-fallback';
  evaluation_time_ms?: number;
  input_tokens?: number;
  output_tokens?: number;
  urgent_attention: DrexNoulMetric;
  med_or_lab_link: DrexNoulMetric;
  actionable_change?: DrexNoulMetric;
  severity: DrexScoreMetric;
  specialty: DrexChoiceMetric;
}

export interface DrexFeedVerificationData {
  source: 'drex-max-v1.1' | 'calibrated-fallback';
  evaluation_time_ms?: number;
  clinically_actionable: DrexNoulMetric;
  prompt_followup: DrexNoulMetric;
  review_priority: DrexScoreMetric;
  domain: DrexChoiceMetric;
}

export interface DrexClinicalRiskData {
  source: 'drex-max-v1.1' | 'calibrated-fallback';
  evaluation_time_ms?: number;
  evaluated_at: string;
  deterioration_30d: DrexNoulMetric;
  renal_or_med_adjustment: DrexNoulMetric;
  monitoring_gap: DrexNoulMetric;
  followup_urgency: DrexScoreMetric;
  lead_specialty: DrexChoiceMetric;
}

export interface CompareModelsData {
  question: string;
  gemini: {
    model: string;
    latency_ms: number;
    answer: string;
    sources: AskSource[];
    word_count: number;
  };
  drex: {
    model: string;
    latency_ms: number;
    evaluation_time_ms?: number;
    input_tokens?: number;
    output_tokens?: number;
    triage: DrexTriageData;
  };
  audio_wav_base64?: string | null;
  voice_used?: string;
}

export interface FeedEntry {
  id: string;
  at: string;
  kind: FeedKind;
  date: string;
  title: string;
  detail: string;
  drex_verification?: DrexFeedVerificationData;
  extracted?: {
    name: string;
    value: number;
    unit: string;
    range: string | null;
    flag: 'high' | 'low' | null;
    date: string | null;
    prevValue: number | null;
    prevUnit: string | null;
    prevDate: string | null;
    pctChange: number | null;
  };
}

export interface StoredBriefing {
  patient: string;
  clinician: string;
  date: string;
  at: string;
  labName?: string | null;
  count: number;
  drex_verification?: DrexFeedVerificationData;
}

export interface BriefingResultPayload {
  name: string;
  value: number;
  unit: string;
  range?: string | null;
  flag?: 'high' | 'low' | null;
  date?: string | null;
  prev_value?: number | null;
  prev_unit?: string | null;
  prev_date?: string | null;
  pct_change?: number | null;
}

export interface PortalHostBridge {
  getPatientId: () => string;
  readStore: <T>(key: string, fallback: T) => T;
  writeStore: (key: string, value: unknown) => void;
  formatDate: (dateStr: string) => string;
  useDoctor?: () => boolean;
  loadDoctorDoc?: () => Promise<any>;
  cdssPrompts?: string[];
  crisisRegex?: RegExp;
  CrisisBanner?: any;
  checkHealth?: () => Promise<boolean>;
  apiBase?: string;
  Hero?: any;
  InfoBanner?: any;
  Sheet?: any;
  Icon?: any;
  AskQuestions?: any;
}
