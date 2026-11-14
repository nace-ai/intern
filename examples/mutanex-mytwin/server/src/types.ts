export type SourceKind = 'record' | 'finding' | 'kg' | 'web';

export interface SourceItem {
  kind: SourceKind;
  title: string;
  detail?: string;
  finding_id?: string;
  url?: string;
}

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

export interface DrexTriageRequestBody {
  text?: string;
  mode?: 'patient' | 'cdss' | 'onboarding';
  bundle?: any;
  questionnaire?: Record<string, any>;
  summary_lines?: Record<string, string>;
  uploaded_labs?: ExtractResultItem[];
  genetics?: OnboardingGeneItem[];
}

export interface DrexTriageResponse {
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

export interface DrexVerifyFeedRequestBody {
  entries: Array<{
    kind: string;
    date?: string;
    title: string;
    detail?: string;
    extracted?: any;
  }>;
  bundle?: any;
}

export interface DrexVerifyFeedResponse {
  source: 'drex-max-v1.1' | 'calibrated-fallback';
  evaluation_time_ms?: number;
  clinically_actionable: DrexNoulMetric;
  prompt_followup: DrexNoulMetric;
  review_priority: DrexScoreMetric;
  domain: DrexChoiceMetric;
}

export interface DrexClinicalRiskRequestBody {
  bundle: any;
  clinical?: any;
  pending_feed?: any[];
  latest_briefing?: any;
}

export interface DrexClinicalRiskResponse {
  source: 'drex-max-v1.1' | 'calibrated-fallback';
  evaluation_time_ms?: number;
  evaluated_at: string;
  deterioration_30d: DrexNoulMetric;
  renal_or_med_adjustment: DrexNoulMetric;
  monitoring_gap: DrexNoulMetric;
  followup_urgency: DrexScoreMetric;
  lead_specialty: DrexChoiceMetric;
}

export interface AskRequestBody {
  patient_id?: string;
  question: string;
  bundle: any;
  mode?: 'cdss' | string;
  clinical?: any;
  include_drex?: boolean;
  synthesize_audio?: boolean;
  voice_profile?: Partial<VoicePersonaProfile>;
}

export interface AskResponse {
  answer: string;
  sources: SourceItem[];
  drex_triage?: DrexTriageResponse;
  audio_wav_base64?: string | null;
  voice_used?: string;
}

export type ExtractMimeType = 'image/png' | 'image/jpeg' | 'application/pdf';

export interface ExtractRequestBody {
  file: string; // base64 string
  mime_type: ExtractMimeType;
}

export interface ExtractResultItem {
  name: string;
  value: number;
  unit: string;
  range: string | null;
  flag: 'high' | 'low' | null;
  date: string | null;
}

export interface ExtractResponse {
  report_date: string | null;
  lab: string | null;
  results: ExtractResultItem[];
}

export interface BriefingResultItem {
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

export interface BriefingRequestBody {
  bundle: any;
  new_results: BriefingResultItem[];
  include_drex?: boolean;
}

export interface BriefingResponse {
  patient: string;
  clinician: string;
  drex_verification?: DrexVerifyFeedResponse;
}

export interface OnboardingGeneItem {
  gene: string;
  variant?: string;
  phenotype?: string;
  role?: string;
}

export interface CreateTwinRequestBody {
  path_type: 'membership' | 'snapshot';
  tier?: string;
  details: {
    name: string;
    dob: string;
    region?: string;
    country?: string;
    phone?: string;
  };
  questionnaire?: Record<string, any>;
  summary_lines?: {
    concern: string;
    top_goal: string;
    barrier: string;
    support: string;
  };
  uploaded_labs?: ExtractResultItem[];
  linked_records?: string[];
  genetics?: OnboardingGeneItem[];
  upsell_panels?: string[];
  consult?: {
    doctor: string;
    slot: string;
    paid?: boolean;
  } | null;
}

export interface CreateTwinResponse {
  id: string;
  index_entry: {
    id: string;
    name: string;
    age: number;
    sex: string;
    headline: string;
    tags: string[];
  };
  research_pipeline: Array<{
    stage: string;
    detail: string;
    status: 'done';
  }>;
  bundle: any;
  doctor_bundle: any;
}

export interface VoicePersonaProfile {
  vocal_style: string;
  prebuilt_voice: 'Kore' | 'Puck' | 'Charon' | 'Fenrir' | 'Zephyr';
  cadence_wpm: number;
  warmth: number;
  clarity: number;
  pitch_hz?: number;
  brightness?: number;
  signature_phrases: string[];
  sample_count: number;
  fidelity_score: number;
  summary: string;
}

export interface VoiceTrainRequestBody {
  audio_base64?: string;
  mime_type?: string;
  transcript_hint?: string;
  patient_name?: string;
  sample_count?: number;
  measured_pitch_hz?: number;
  measured_brightness?: number;
  existing_profile?: Partial<VoicePersonaProfile>;
}

export interface VoiceTrainResponse {
  transcript: string;
  profile: VoicePersonaProfile;
}

export interface VoiceSynthesizeRequestBody {
  text: string;
  voice_profile?: Partial<VoicePersonaProfile>;
  patient_sex?: string;
}

export interface VoiceSynthesizeResponse {
  audio_wav_base64: string | null;
  voice_used: string;
  style_used: string;
  pitch_hz: number;
}

export interface CompareModelsRequestBody {
  question: string;
  bundle: any;
  mode?: 'patient' | 'cdss';
  clinical?: any;
  synthesize_audio?: boolean;
  voice_profile?: Partial<VoicePersonaProfile>;
}

export interface CompareModelsResponse {
  question: string;
  gemini: {
    model: string;
    latency_ms: number;
    answer: string;
    sources: SourceItem[];
    word_count: number;
  };
  drex: {
    model: string;
    latency_ms: number;
    evaluation_time_ms?: number;
    input_tokens?: number;
    output_tokens?: number;
    triage: DrexTriageResponse;
  };
  audio_wav_base64?: string | null;
  voice_used?: string;
}

export interface FaceLikenessProfile {
  skin_tone_hex: string;
  hair_color_hex: string;
  eye_color_hex: string;
  lip_color_hex: string;
  face_width_ratio: number;
  jaw_taper: number;
  cheek_prominence: number;
  brow_arch: number;
  hair_style: 'short' | 'medium' | 'long' | 'bun' | 'curly';
  glasses: boolean;
  likeness_score: number;
  likeness_summary: string;
}

export interface FaceAnalyzeRequestBody {
  image_base64: string;
  mime_type: string;
  photo_count?: number;
}

export interface PreserveSpeakRequestBody {
  prompt: string;
  patient_name?: string;
  bundle?: any;
  voice_profile?: Partial<VoicePersonaProfile>;
  memories?: string[];
}

export interface PreserveSpeakResponse {
  reply_text: string;
  audio_wav_base64: string | null;
  voice_used: string;
  style_used: string;
  updated_fidelity: number;
}


