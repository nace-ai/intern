import { useEffect, useState } from "react";
import type { Usage } from "./types";
import { fetchUsage } from "./api";

interface Props {
  demo: Usage | null; // when set, show this instead of polling /api/usage
}

function n(v: unknown): string {
  return typeof v === "number" && Number.isFinite(v) ? v.toLocaleString("en-US") : "–";
}

export default function Footer({ demo }: Props) {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [down, setDown] = useState(false);

  useEffect(() => {
    if (demo) return;
    let alive = true;
    const tick = () => {
      fetchUsage()
        .then((u) => {
          if (!alive) return;
          setUsage(u);
          setDown(false);
        })
        .catch(() => alive && setDown(true));
    };
    tick();
    const id = window.setInterval(tick, 5000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [demo]);

  const u = demo ?? usage;
  return (
    <footer className="footer" data-tour="usage">
      {demo && <span className="badge demo">sample data</span>}
      {down && !demo && <span className="badge down">usage unavailable</span>}
      <span>
        Drex calls <b>{n(u?.nace?.drex_calls)}</b>
      </span>
      <span>
        NDI jobs <b>{n(u?.nace?.ndi_jobs)}</b> · pages <b>{n(u?.nace?.ndi_pages)}</b>
      </span>
      <span>
        Drex Search calls <b>{n(u?.nace?.search_calls)}</b> · workspace files <b>{n(u?.nace?.workspace_files)}</b>
      </span>
      <span>
        Intern runs <b>{n(u?.intern?.runs)}</b> · turns <b>{n(u?.intern?.turns)}</b> · Drex asks <b>{n(u?.intern?.drex_asks)}</b>
      </span>
      <span>
        Attacker tokens in <b>{n(u?.arena?.attacker_input_tokens)}</b> · out <b>{n(u?.arena?.attacker_output_tokens)}</b>
      </span>
    </footer>
  );
}
