import publicSite from "../public-site.json";
import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { motion } from "motion/react";
import {
  Shield,
  ArrowRight,
  Lock,
  Radio,
  ScanLine,
  Layers,
} from "lucide-react";
import { missions, type MissionId } from "./missions";
export function Hub({
  select,
  selecting,
  play,
  liveAvailable = false,
}: {
  select: (id: MissionId) => void;
  selecting: boolean;
  play: () => void;
  liveAvailable?: boolean;
}) {
  const [qr, setQr] = useState("");
  const publicUrl =
    (import.meta as unknown as { env: Record<string, string> }).env
      .VITE_PUBLIC_URL || publicSite.url;
  useEffect(() => {
    if (publicUrl && /^https:\/\//.test(publicUrl))
      void QRCode.toDataURL(publicUrl, {
        width: 160,
        margin: 1,
        color: { dark: "#8af9cf", light: "#080c12" },
      }).then(setQr);
  }, [publicUrl]);
  return (
    <div className="hub">
      <header className="s-header">
        <a className="s-brand" href="/">
          <Shield /> AGENT<span>HEIST</span>
        </a>
        <span className="hub-status">
          DREXATHON 2026 / FICTIONAL CYBER RANGE
        </span>
      </header>
      <main className="hub-main">
        <motion.section
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: "easeOut" }}
          className="hub-hero"
        >
          <img
            className="hub-hero-art"
            src="/art/vaultcorp-firefly.webp"
            alt="VaultCorp's fictional robot guardian standing before a sealed digital vault"
            width="1920"
            height="1098"
            fetchPriority="high"
          />
          <div className="hub-hero-shade" aria-hidden="true" />
          <div className="hero-art-credit">
            ORIGINAL ART / ADOBE FIREFLY IMAGE 5
          </div>
          <div className="hub-hero-copy">
            <div className="hero-wordmark">
              <Shield size={18} /> AGENT <span>HEIST</span>
            </div>
            <div className="s-kicker">HUMAN INGENUITY vs MACHINE JUDGMENT</div>
            <h1>
              Every agent has
              <br />
              <em>a breaking point.</em>
            </h1>
            <p>
              Talk your way past an AI custodian. Forge a fictional
              authorization. Poison a tool response. The model decides — the
              engine determines what actually happens.
            </p>
            <div className="hero-actions">
              <button className="begin-mission" onClick={play}>
                Play Agent Heist <ArrowRight size={18} />
              </button>
              <button
                onClick={() =>
                  document
                    .querySelector(".how-play")
                    ?.scrollIntoView({ behavior: "auto" })
                }
              >
                How to play
              </button>
            </div>
            <button
              className="hero-demo-link"
              onClick={() => select("insider")}
            >
              Launch 2-minute Training demo <ArrowRight size={14} />
            </button>
            <div className="hero-tags">
              <span>3 missions</span>
              <span>Keyless Training</span>
              <span>
                {liveAvailable
                  ? "Bring your own Nace key for Live"
                  : "Live temporarily unavailable"}
              </span>
            </div>
          </div>
        </motion.section>
        <section
          id="mission-select"
          className="mission-select"
          aria-label="Mission selection"
        >
          <div className="section-heading">
            <div>
              <div className="s-kicker">CHOOSE YOUR ENTRY POINT</div>
              <h2>
                {selecting
                  ? "Select a mission."
                  : "Three ways to breach trust."}
              </h2>
            </div>
            <span>
              {liveAvailable
                ? "TRAINING PLAYABLE / LIVE REQUIRES KEYS"
                : "TRAINING PLAYABLE / LIVE TEMPORARILY UNAVAILABLE"}
            </span>
          </div>
          <div className="mission-cards">
            {missions.map((m) => (
              <motion.button
                whileHover={{ y: -5 }}
                key={m.id}
                className={"mission-card mission-" + m.id}
                onClick={() => select(m.id)}
                style={{ "--mission-color": m.color } as React.CSSProperties}
              >
                <span className="mission-number">
                  {m.number} <Lock size={18} />
                </span>
                <small>{m.mechanic}</small>
                <h3>{m.title}</h3>
                <p>{m.description}</p>
                <span className="mission-target">TARGET / {m.target}</span>
                <b>
                  Enter mission <ArrowRight size={15} />
                </b>
              </motion.button>
            ))}
          </div>
        </section>
        <section className="how-play">
          <div>
            <div className="s-kicker">HOW TO PLAY</div>
            <h2>Your words are the attack surface.</h2>
          </div>
          <ol>
            <li>
              <b>Investigate</b>
              <p>
                Read the briefing, ask questions and uncover clues. Training
                explains the vulnerable pattern.
              </p>
            </li>
            <li>
              <b>Commit</b>
              <p>
                Choose a strategy and submit an access attempt. Watch your
                attempts, heat and fictional credits.
              </p>
            </li>
            <li>
              <b>Capture</b>
              <p>
                A real engine breach releases a unique flag. Verify it, copy it
                and compare your score.
              </p>
            </li>
          </ol>
        </section>
        <section className="powered-by">
          <div>
            <Radio />
            <h3>Groq gives Sentinel a voice.</h3>
            <p>
              Context-aware dialogue and personality. Conversation cannot
              execute a privileged tool.
            </p>
          </div>
          <div>
            <Layers />
            <h3>Drex makes the decisions.</h3>
            <p>
              Security and tool-use choices with actual decision probabilities
              in Forensics. Live has no scripted wins.
            </p>
          </div>
          <div>
            <ScanLine />
            <h3>NDI reads the evidence.</h3>
            <p>
              Opt-in document Parse and Extract reuse the same Parse result.
              Training uses an explicitly labeled local simulation.
            </p>
          </div>
        </section>
        <div className="share-placeholder">
          {qr ? (
            <>
              <img
                src={qr}
                width={100}
                height={100}
                alt="QR code for the configured public mission URL"
              />
              <p>
                <a href={publicUrl}>{publicUrl}</a>
                <br />
                <a href={publicSite.qr} download="agent-heist-qr.png">
                  Download 2048px judges’ QR code
                </a>
              </p>
            </>
          ) : (
            <>
              <div
                className="qr-placeholder"
                aria-label="QR code pending public deployment"
              >
                QR
                <br />
                PENDING
              </div>
              <p>
                Public mission link coming after deployment verification.
                <br />
                <small>No public URL has been configured.</small>
              </p>
            </>
          )}
        </div>
      </main>
      <footer className="s-footer">
        FICTIONAL TARGETS. REAL DECISION BOUNDARIES. / AGENT HEIST
      </footer>
    </div>
  );
}
