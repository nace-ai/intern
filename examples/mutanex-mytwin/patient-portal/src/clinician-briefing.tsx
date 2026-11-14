import React, { useEffect, useState } from 'react';
import { formatProbPct, humanizeChoiceKey } from './drex-utils';
import { BRIEFING_EVENT, BRIEFING_STORAGE_KEY } from './feed';
import type { StoredBriefing } from './types';

export function ClinicianBriefingCard({
  b,
  readStore,
  formatDate,
}: {
  b: any;
  doc?: any;
  readStore: <T>(key: string, fallback: T) => T;
  formatDate: (d: string) => string;
}) {
  const [briefing, setBriefing] = useState<StoredBriefing | null>(() =>
    readStore<StoredBriefing | null>(BRIEFING_STORAGE_KEY, null)
  );

  useEffect(() => {
    const sync = () => {
      setBriefing(readStore<StoredBriefing | null>(BRIEFING_STORAGE_KEY, null));
    };
    sync();
    window.addEventListener(BRIEFING_EVENT, sync);
    return () => window.removeEventListener(BRIEFING_EVENT, sync);
  }, [b, readStore]);

  if (!briefing || !briefing.clinician) {
    return null;
  }

  const dv = briefing.drex_verification;

  return (
    <section
      className="card rs-wide mx-clinician-briefing"
      aria-label="Clinician report briefing"
    >
      <div className="sec">
        <div>
          <span className="mx-eyebrow">Clinician note · Uploaded lab report</span>
          <h2>
            {b?.patient?.preferred_name || 'Patient'}: unverified report delta summary
          </h2>
        </div>
        <span className="state warn">
          <i />
          Waiting for check
        </span>
      </div>
      <p className="mx-briefing-body">{briefing.clinician}</p>
      {dv && (
        <div className="mx-drex-inline-meta">
          <span>
            Drex ({dv.source}): <b>{dv.review_priority.label}</b>
          </span>
          <span aria-hidden="true">·</span>
          <span className="mx-tabular">
            Actionable {formatProbPct(dv.clinically_actionable.probability, 0)}
          </span>
          <span aria-hidden="true">·</span>
          <span className="mx-tabular">
            Prompt follow-up {formatProbPct(dv.prompt_followup.probability, 0)}
          </span>
          <span aria-hidden="true">·</span>
          <span>{humanizeChoiceKey(dv.domain.choice)}</span>
        </div>
      )}
      <p className="note">
        {briefing.labName ? `${briefing.labName} · ` : ''}
        Report date {formatDate(briefing.date)} · {briefing.count} analyte
        {briefing.count === 1 ? '' : 's'} submitted by patient · For physician review
      </p>
    </section>
  );
}
