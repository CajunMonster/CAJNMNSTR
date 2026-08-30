"use client";

import { useEffect, useState } from "react";
import Image from "next/image";

type ViewName = "dashboard" | "evidence" | "journal" | "system";

const statusItems = [
  ["CAJNMNSTR", "PAUSED", "paused"],
  ["ALPACA", "PAPER AUTH", "ok"],
  ["MARKET DATA", "SIP / OPRA STALE", "paused"],
  ["AI", "TERRA REPLAY", "ok"],
  ["REFEREE", "LOCKED", "ok"],
];

const instruments = [
  { symbol: "XAU", name: "GOLD", price: "$2,386.71", change: "+0.78%", source: "GOLD-API", bars: [24, 27, 29, 26, 34, 38, 35, 45, 49, 54, 52, 64] },
  { symbol: "XAG", name: "SILVER", price: "$28.97", change: "+0.91%", source: "GOLD-API", bars: [18, 22, 19, 27, 29, 31, 28, 38, 42, 39, 47, 53] },
  { symbol: "BTC", name: "BITCOIN", price: "$67,842.50", change: "+1.87%", source: "ALPACA", bars: [28, 21, 31, 27, 36, 43, 39, 48, 45, 58, 55, 68] },
];

const spyBars = [36, 44, 39, 54, 49, 63, 57, 51, 66, 61, 73, 69, 77, 70, 82, 76, 85, 79];

const refereeChecks = [
  ["PAPER_MODE", "PASS", "Paper endpoint confirmed"],
  ["SYMBOL_ALLOWED", "PASS", "SPY is inside the approved universe"],
  ["DATA_FRESHNESS", "PASS", "Replay timestamps are internally fresh"],
  ["FEED_AUTHORITY", "PASS", "SIP and OPRA entitlements verified"],
  ["POSITION_LIMIT", "PASS", "Dedicated paper account remains empty"],
  ["PREMIUM_CAP", "PASS", "$4.05 ask is inside the locked $4.25 limit"],
  ["DAILY_LOSS", "PASS", "$0 of $1,000 lockout used"],
  ["BROKER_BOUNDARY", "PASS", "Replay stops before identity reservation or submission"],
];

const navItems: Array<[ViewName, string, string]> = [
  ["dashboard", "COMMAND", "01"],
  ["evidence", "EVIDENCE", "02"],
  ["journal", "JOURNAL", "03"],
  ["system", "SYSTEM", "04"],
];

function InstrumentCard({ instrument }: { instrument: typeof instruments[number] }) {
  return (
    <article className="instrument">
      <div>
        <p>{instrument.name} <span>/ {instrument.symbol}</span></p>
        <strong>{instrument.price}</strong>
        <small>{instrument.change}</small>
      </div>
      <div className="spark-bars" aria-hidden="true">
        {instrument.bars.map((height, index) => <i key={index} style={{ height: `${height}%` }} />)}
      </div>
      <span className="freshness">{instrument.source} · DEMO · 12s</span>
    </article>
  );
}

function Pipeline() {
  return (
    <div className="decision-flow" aria-label="Decision pipeline">
      {[["01", "ANALYZE"], ["02", "EVIDENCE"], ["03", "RISK"], ["04", "VERDICT"], ["05", "REVIEW"]].map(([number, label], index) => (
        <div className={index === 4 ? "flow-step active" : "flow-step"} key={label}>
          <span>{number}</span><strong>{label}</strong>
        </div>
      ))}
    </div>
  );
}

function DashboardView({ openEvidence }: { openEvidence: () => void }) {
  return (
    <>
      <div className="market-ribbon" aria-label="Informational instruments">
        {instruments.map((instrument) => <InstrumentCard instrument={instrument} key={instrument.symbol} />)}
      </div>
      <Pipeline />
      <div className="primary-grid">
        <article className="panel spy-panel">
          <div className="panel-heading">
            <div><span>PRIMARY MARKET</span><h2>SPY</h2></div>
            <div className="quote"><strong>$534.26</strong><span>+$2.11 · +0.40%</span></div>
          </div>
          <div className="chart-field" aria-label="Representative SPY intraday chart">
            <div className="axis-labels"><span>536</span><span>534</span><span>532</span><span>530</span></div>
            <div className="chart-bars" aria-hidden="true">
              {spyBars.map((height, index) => <i key={index} className={index % 4 === 0 ? "down" : "up"} style={{ height: `${height}%` }} />)}
            </div>
            <div className="chart-glow" />
          </div>
          <div className="market-facts">
            <div><span>DATA FEED</span><strong>SIP · DEMO</strong></div>
            <div><span>SESSION</span><strong>REGULAR</strong></div>
            <div><span>REGIME</span><strong>LOW VOL</strong></div>
          </div>
        </article>

        <article className="panel case-panel">
          <div className="panel-heading">
            <div><span>VERIFIED REPLAY</span><h2>BULLISH APPROVE</h2></div>
            <span className="case-state">REPLAY ONLY</span>
          </div>
          <div className="case-body">
            <section className="case-copy">
              <p className="section-label">AI PROPOSAL</p>
              <h3>BULLISH CALL</h3>
              <p className="thesis">Positive 5-, 15-, and 60-minute returns align above VWAP and the opening range.</p>
              <p className="section-label">STRONGEST COUNTERARGUMENT</p>
              <p className="counter">A sharp reversal through VWAP and the opening range would invalidate the directional alignment.</p>
              <div className="contract-line"><span>DETERMINISTIC REPLAY CANDIDATE</span><strong>SPY · 504 CALL · 11 DTE · 2 MAX</strong></div>
              <div className="uncertainty"><span>UNCERTAINTY</span><strong>MEDIUM</strong></div>
              <button className="evidence-button" type="button" onClick={openEvidence}>INSPECT EVIDENCE PASSPORT <span>→</span></button>
            </section>
            <section className="verdict-card">
              <p>REFEREE VERDICT</p>
              <div className="verdict-seal" aria-hidden="true">✓</div>
              <h3>APPROVE</h3>
              <span>REPLAY AUTHORITY ONLY</span>
              <ul>
                <li>Paper mode confirmed</li>
                <li>Six directional states align</li>
                <li>OPRA-shaped quote validated</li>
                <li>Broker submission prohibited</li>
              </ul>
            </section>
          </div>
        </article>
      </div>
      <div className="summary-grid">
        <article className="summary-card">
          <p>BROKER REALITY</p><strong>NOT SUBMITTED</strong><span>Execution remains disabled in this prototype.</span>
        </article>
        <article className="summary-card">
          <p>PAPER PERFORMANCE</p><strong className="positive">$0.00 · 0.00%</strong><span>No broker positions or fills.</span>
        </article>
        <article className="summary-card">
          <p>REPLAY AUTHORITY</p><strong>READY FOR REVIEW</strong><span>Sealed Passport; broker submission remains prohibited.</span>
        </article>
      </div>
    </>
  );
}

function EvidenceView() {
  return (
    <section className="view-panel" aria-labelledby="evidence-title">
      <div className="view-heading">
        <div><p className="eyebrow">BULLISH APPROVE · REPLAY PASSPORT</p><h2 id="evidence-title">Evidence, not intuition.</h2></div>
        <span className="passport-id">CHECKED-IN FIXTURE</span>
      </div>
      <div className="evidence-layout">
        <article className="evidence-brief">
          <p className="section-label">THE CASE</p>
          <h3>LONG CALL · MEDIUM UNCERTAINTY</h3>
          <p>Terra cited aligned returns, VWAP, and opening-range evidence. Deterministic code then selected an eligible 11-DTE call and stopped at operator review.</p>
          <dl>
            <div><dt>MODEL</dt><dd>GPT-5.6-TERRA · STRICT STRUCTURED OUTPUT</dd></div>
            <div><dt>HORIZON</dt><dd>INTRADAY</dd></div>
            <div><dt>INVALIDATION</dt><dd>LOSS OF VWAP AND OPENING RANGE</dd></div>
            <div><dt>PROVENANCE</dt><dd>CHECKED-IN BARS · OPRA-SHAPED FIXTURE</dd></div>
          </dl>
        </article>
        <article className="referee-ledger">
          <p className="section-label">DETERMINISTIC REFEREE</p>
          {refereeChecks.map(([code, result, detail]) => (
            <div className="ledger-row" key={code}>
              <div><strong>{code}</strong><span>{detail}</span></div>
              <b className={result === "BLOCK" ? "blocked" : result === "REDUCE" ? "reduced" : "passed"}>{result}</b>
            </div>
          ))}
        </article>
      </div>
      <div className="provenance-strip">
        <div><span>MARKET AS OF</span><strong>2026-08-24 REPLAY</strong></div>
        <div><span>REPLAY QUOTE AGE</span><strong>5 SECONDS</strong></div>
        <div><span>POLICY</span><strong>SPY-REPLAY-V0.1</strong></div>
        <div><span>LIVE AUTHORITY</span><strong className="amber">NONE</strong></div>
      </div>
    </section>
  );
}

function JournalView() {
  const entries = [
    ["14:30:05", "STOP", "Operator review ready · broker submission false"],
    ["14:30:04", "EVIDENCE", "Complete replay Passport sealed"],
    ["14:30:03", "REFEREE", "APPROVE · six supporting states · zero opposition"],
    ["14:30:02", "AI", "Terra LONG_CALL · medium uncertainty · citations valid"],
    ["14:30:01", "MARKET", "Checked-in SPY features calculated deterministically"],
  ];
  return (
    <section className="view-panel" aria-labelledby="journal-title">
      <div className="view-heading"><div><p className="eyebrow">AUDIT TRAIL</p><h2 id="journal-title">Every step leaves a mark.</h2></div><span className="passport-id">LOCAL DEMO</span></div>
      <div className="journal-list">
        {entries.map(([time, kind, text], index) => (
          <article className="journal-entry" key={time}>
            <span>{time}</span><b>{kind}</b><p>{text}</p><i>{String(index + 1).padStart(2, "0")}</i>
          </article>
        ))}
      </div>
      <div className="shadow-note"><strong>SHADOW EVALUATION</strong><span>No hypothetical fill has been created. A blocked decision would be marked separately from broker P&amp;L.</span></div>
    </section>
  );
}

function SystemView() {
  return (
    <section className="view-panel" aria-labelledby="system-title">
      <div className="view-heading"><div><p className="eyebrow">LOCAL CONTROL ROOM</p><h2 id="system-title">Safe by construction.</h2></div><span className="danger-state">PAUSED · EXECUTION DISABLED</span></div>
      <div className="system-grid">
        <article><span>HEALTH STATE</span><strong>PAUSED</strong><p>Authenticated data exceeds the closed-market freshness policy.</p></article>
        <article><span>ALPACA ADAPTER</span><strong>AUTHENTICATED</strong><p>Dedicated paper account reads succeeded; execution remains disabled.</p></article>
        <article><span>DATA ENTITLEMENT</span><strong>SIP + OPRA</strong><p>Algo Trader Plus feeds are authorized; closed-market timestamps still enforce PAUSED.</p></article>
        <article><span>MCP SURFACE</span><strong>READ ONLY</strong><p>Assets, stock data, options data, and news only. Trading tools are excluded.</p></article>
        <article><span>TERRA ADAPTER</span><strong>9-CASE VERIFIED</strong><p>Live replay run: 5 approve, 0 reduce, 2 abstain, 2 block; no tools or broker interface.</p></article>
        <article><span>ORDER GATE</span><strong>DOUBLE LOCKED</strong><p>A paper-only flag and exact confirmation are both required.</p></article>
        <article><span>EVIDENCE STORE</span><strong>HEALTHY</strong><p>Connection and data-health events persisted without credentials or account identifiers.</p></article>
        <article><span>INCIDENT POLICY</span><strong>FAIL LOUD</strong><p>Critical failures attach protective action and force PAUSED authority.</p></article>
      </div>
    </section>
  );
}

export default function Home() {
  const [activeView, setActiveView] = useState<ViewName>("dashboard");
  const [clock, setClock] = useState("--:--:--");

  useEffect(() => {
    const updateClock = () => setClock(new Intl.DateTimeFormat("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "America/Chicago" }).format(new Date()));
    updateClock();
    const timer = window.setInterval(updateClock, 1000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <main className="page-shell">
      <section className="terminal-frame" aria-label="CAJNMNSTR trading agent dashboard">
        <header className="masthead">
          <div className="brand-lockup">
            <div className="brand-mark" aria-hidden="true"><Image src="/cajnmstr-icon.png" alt="" width={76} height={76} priority /></div>
            <div><p className="eyebrow">AUTONOMOUS EVIDENCE ENGINE</p><h1>CAJNMNSTR</h1><p className="tagline">AI MAKES THE CASE. <strong>EVIDENCE DECIDES.</strong></p></div>
          </div>
          <div className="system-strip" aria-label="System status">
            {statusItems.map(([label, value, state]) => <div className="status-chip" key={label}><span className={`status-light ${state}`} aria-hidden="true" /><span>{label}</span><strong>{value}</strong></div>)}
          </div>
          <div className="equity-block"><span>VERIFIED PAPER EQUITY</span><strong>$100,000.00</strong><small>{clock} CT · READ ONLY</small></div>
        </header>

        <nav className="nav-rail" aria-label="Dashboard sections">
          {navItems.map(([view, label, number]) => (
            <button type="button" key={view} className={activeView === view ? "active" : ""} aria-pressed={activeView === view} onClick={() => setActiveView(view)}>
              <span>{number}</span>{label}
            </button>
          ))}
          <div className="mode-lock"><span aria-hidden="true">◆</span> PAPER MODE · LOCKED</div>
        </nav>

        <div className="health-banner" role="status">
          <strong>PAUSED</strong><span>Paper authentication, SIP, OPRA, and the nine-case Terra replay pipeline are verified. Live market data remains closed-market stale; the replay stops before broker submission and execution is disabled.</span>
        </div>

        {activeView === "dashboard" && <DashboardView openEvidence={() => setActiveView("evidence")} />}
        {activeView === "evidence" && <EvidenceView />}
        {activeView === "journal" && <JournalView />}
        {activeView === "system" && <SystemView />}

        <footer className="terminal-footer">
          <span>AUTHENTICATED READ ONLY · REPRESENTATIVE DATA</span><strong>EXECUTION DISABLED</strong><span>AI ARGUES · CODE DECIDES · BROKER VERIFIES</span>
        </footer>
      </section>
    </main>
  );
}
