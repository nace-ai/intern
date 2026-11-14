import React, { useCallback, useEffect, useState } from 'react';
import {
  formatProbPct,
  humanizeChoiceKey,
  scoreDistributionRows,
  sortedChoiceProbabilities,
} from './drex-utils';
import { BRIEFING_EVENT, BRIEFING_STORAGE_KEY, FEED_EVENT } from './feed';
import { ModelComparisonPanel, ProductDrexDemoModal } from './model-comparison';
import type {
  DrexClinicalRiskData,
  FeedEntry,
  PortalHostBridge,
  StoredBriefing,
} from './types';
import { resolvePortalApiUrl } from './voice-engine';

export function DrexClinicalPanel({
  b,
  doc,
  readStore,
  writeStore,
  formatDate,
  apiBase,
  setDoctorMode,
}: {
  b: any;
  doc?: any;
  readStore: <T>(key: string, fallback: T) => T;
  writeStore?: (key: string, value: unknown) => void;
  formatDate: (d: string) => string;
  apiBase?: string;
  setDoctorMode?: (on: boolean) => void;
}) {
  const [riskData, setRiskData] = useState<DrexClinicalRiskData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCompareModal, setShowCompareModal] = useState(false);
  const [showDemoModal, setShowDemoModal] = useState(false);

  const evaluateRisk = useCallback(async () => {
    if (!b) return;
    setLoading(true);
    setError(null);
    try {
      const pendingFeed = readStore<FeedEntry[]>('mx-feed', []);
      const latestBriefing = readStore<StoredBriefing | null>(BRIEFING_STORAGE_KEY, null);
      const res = await fetch(resolvePortalApiUrl('/v1/drex/clinical-risk', apiBase), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bundle: b,
          clinical: doc,
          pending_feed: pendingFeed,
          latest_briefing: latestBriefing,
        }),
      });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const data = (await res.json()) as DrexClinicalRiskData;
      setRiskData(data);
    } catch (err: any) {
      setError(err?.message || 'Could not reach Drex clinical risk evaluator.');
    } finally {
      setLoading(false);
    }
  }, [b, doc, readStore, apiBase]);

  useEffect(() => {
    void evaluateRisk();
  }, [evaluateRisk]);

  useEffect(() => {
    const onUpdate = () => {
      void evaluateRisk();
    };
    window.addEventListener(FEED_EVENT, onUpdate);
    window.addEventListener(BRIEFING_EVENT, onUpdate);
    return () => {
      window.removeEventListener(FEED_EVENT, onUpdate);
      window.removeEventListener(BRIEFING_EVENT, onUpdate);
    };
  }, [evaluateRisk]);

  const hostShim: PortalHostBridge = {
    getPatientId: () => b?.patient?.id || 'patient',
    readStore,
    writeStore: writeStore || (() => {}),
    formatDate,
    useDoctor: () => true,
    apiBase,
  };

  const urgencyRows = riskData
    ? scoreDistributionRows(
        riskData.followup_urgency.legend,
        riskData.followup_urgency.probabilities
      )
    : [];
  const specialtyRows = riskData
    ? sortedChoiceProbabilities(riskData.lead_specialty.probabilities)
    : [];

  return (
    <>
      <section
        className="card rs-wide mx-drex-clinical-card"
        aria-label="Calibrated clinical risk panel"
        data-testid="drex-clinical-panel"
      >
        <div className="sec">
          <div>
            <span className="mx-eyebrow">
              Calibrated epistemic decision support · {riskData?.source || 'drex-max-v1.1'}
              {riskData?.evaluation_time_ms ? ` · ${riskData.evaluation_time_ms} ms` : ''}
            </span>
            <h2>
              {b?.patient?.preferred_name || 'Patient'}: longitudinal calibrated risk probabilities
            </h2>
          </div>
          <div className="mx-drex-head-actions">
            <button
              type="button"
              className="secondary sm"
              disabled={loading}
              onClick={() => void evaluateRisk()}
            >
              {loading ? 'Evaluating…' : 'Re-evaluate'}
            </button>
            <button
              type="button"
              className="secondary sm"
              onClick={() => setShowCompareModal(true)}
            >
              Compare Gemini vs. Drex
            </button>
            <button
              type="button"
              className="link"
              onClick={() => setShowDemoModal(true)}
            >
              Product &amp; Drex Demo
            </button>
          </div>
        </div>

        {error && (
          <p className="note mx-err-note" role="alert">
            {error}
          </p>
        )}

        {loading && !riskData ? (
          <div className="mx-status-line" role="status">
            <span className="ap-dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            <span>Computing calibrated probabilities across labs, medications, and pending entries…</span>
          </div>
        ) : riskData ? (
          <div className="mx-drex-clinical-grid">
            {/* Column 1: Calibrated binary noul signals */}
            <div className="mx-drex-col">
              <h3 className="mx-drex-subhead">Calibrated clinical risk signals</h3>
              <ul className="rs-rows mx-drex-rows">
                <li>
                  <span>
                    <b>30-day biomarker progression risk</b>
                    <small>
                      Likelihood of organ or lab trajectory progression without near-term review
                    </small>
                    <div className="onb-meter">
                      <span
                        style={{
                          width: `${Math.round(riskData.deterioration_30d.probability * 100)}%`,
                        }}
                      />
                    </div>
                  </span>
                  <span className="rs-val mx-tabular">
                    {formatProbPct(riskData.deterioration_30d.probability, 1)}
                  </span>
                </li>
                <li>
                  <span>
                    <b>Medication or renal dosing review</b>
                    <small>
                      Electrolyte, kidney filtration, or pharmacogenomic regimen adjustment signal
                    </small>
                    <div className="onb-meter">
                      <span
                        style={{
                          width: `${Math.round(
                            riskData.renal_or_med_adjustment.probability * 100
                          )}%`,
                        }}
                      />
                    </div>
                  </span>
                  <span className="rs-val mx-tabular">
                    {formatProbPct(riskData.renal_or_med_adjustment.probability, 1)}
                  </span>
                </li>
                <li>
                  <span>
                    <b>Diagnostic monitoring gap</b>
                    <small>
                      Pending patient-submitted labs or unassessed panel targets warranting order
                    </small>
                    <div className="onb-meter">
                      <span
                        style={{
                          width: `${Math.round(riskData.monitoring_gap.probability * 100)}%`,
                        }}
                      />
                    </div>
                  </span>
                  <span className="rs-val mx-tabular">
                    {formatProbPct(riskData.monitoring_gap.probability, 1)}
                  </span>
                </li>
              </ul>
            </div>

            {/* Column 2: Calibrated follow-up score distribution + specialty choice */}
            <div className="mx-drex-col">
              <h3 className="mx-drex-subhead">
                Follow-up horizon: {riskData.followup_urgency.label} (expected{' '}
                {riskData.followup_urgency.score.toFixed(2)} / 3)
              </h3>
              <ul className="rs-rows mx-drex-rows">
                {urgencyRows.map((row) => (
                  <li key={row.label}>
                    <span>
                      <b>{row.label}</b>
                      <div className="onb-meter">
                        <span style={{ width: `${Math.round(row.pct)}%` }} />
                      </div>
                    </span>
                    <span className="rs-val mx-tabular">{row.pct.toFixed(1)}%</span>
                  </li>
                ))}
              </ul>

              <div className="mx-drex-inline-meta">
                <span>
                  Lead specialty:{' '}
                  <b>{humanizeChoiceKey(riskData.lead_specialty.choice)}</b>
                </span>
                {specialtyRows.slice(0, 3).map((sp) => (
                  <React.Fragment key={sp.key}>
                    <span aria-hidden="true">·</span>
                    <span className="mx-tabular">
                      {sp.label} {sp.pct.toFixed(0)}%
                    </span>
                  </React.Fragment>
                ))}
              </div>
            </div>
          </div>
        ) : null}

        <p className="note">
          Probabilities are Brier-calibrated epistemic estimates over structured twin state · Never diagnoses or prescribes · For physician review
        </p>
      </section>

      {showCompareModal && (
        <div
          className="mx-tour-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Compare Gemini and Drex Models"
        >
          <div className="card mx-tour-modal mx-wide-modal">
            <div className="sec">
              <div>
                <span className="mx-eyebrow">Side-by-Side Clinical Model Workbench</span>
                <h2>Gemini vs. Drex Live Comparison</h2>
              </div>
              <button
                type="button"
                className="secondary sm"
                onClick={() => setShowCompareModal(false)}
              >
                Close
              </button>
            </div>
            <ModelComparisonPanel b={b} host={hostShim} clinicalDoc={doc} />
          </div>
        </div>
      )}

      {showDemoModal && (
        <ProductDrexDemoModal
          b={b}
          host={hostShim}
          onClose={() => setShowDemoModal(false)}
          setDoctorMode={setDoctorMode}
        />
      )}
    </>
  );
}
