"use client";

import Image from "next/image";
import { useEffect, useState, type ReactNode } from "react";
import initialDashboardState from "../public/dashboard-state.json";

type ViewName = "dashboard" | "evidence" | "journal" | "system";
type StatusTone = "verified" | "paused" | "blocked" | "unknown";

type Candle = { t: string; o: number; h: number; l: number; c: number };
type DashboardState = {
  schema_version: number;
  mode: "REPLAY" | "LIVE" | "PAPER";
  operational_state: "HEALTHY" | "DEGRADED" | "PAUSED";
  truth_label: string;
  updated_at: string;
  controls: {
    entry_enabled: boolean;
    entry_armed: boolean;
    position_management_enabled: boolean;
    position_management_armed: boolean;
    broker_lock_active: boolean;
    broker_submission_allowed: boolean;
  };
  connections: Array<{
    id: string;
    label: string;
    value: string;
    state: StatusTone;
    detail: string;
  }>;
  account: {
    equity: number | null;
    buying_power: number | null;
    options_buying_power: number | null;
    day_pl: number | null;
    open_pl: number | null;
    position_count: number;
    open_order_count: number;
    as_of: string;
    source: string;
  };
  market: {
    symbol: string;
    price: number | null;
    previous_close: number | null;
    change: number | null;
    change_percent: number | null;
    last_update: string;
    session: string;
    data_state: string;
    feed: string;
    candles: Candle[];
  };
  regime: {
    state: "BULLISH" | "NEUTRAL" | "BEARISH";
    support: number;
    opposition: number;
    session: string;
    detail: string;
  };
  options: {
    feed: string;
    status: string;
    chain_health: string;
    atm_iv: number | null;
    skew: number | null;
    skew_reason: string;
    last_update: string;
    surface: Array<{ label: string; value: number }>;
  };
  proposal: {
    direction: "LONG_CALL" | "LONG_PUT" | "NO_TRADE";
    time_horizon: string;
    thesis: string;
    counterargument: string;
    uncertainty: string;
    evidence_count: number;
    invalidation: string;
  };
  decision: {
    verdict: "APPROVE" | "REDUCE" | "ABSTAIN" | "BLOCK";
    state: string;
    symbol: string | null;
    contract_label: string | null;
    expiration: string | null;
    dte: number | null;
    quantity_authority: number | null;
    limit_price: number | null;
    authority_max_debit: number | null;
    risk_amount: number | null;
    risk_percent: number | null;
    uncertainty: string;
    reasons: string[];
  };
  passport: {
    id: string;
    fixture_id: string;
    sealed: boolean;
    source: string;
  };
  execution: Array<{ stage: string; status: string; detail: string }>;
  activity: Array<{ time: string; kind: string; text: string; mode: string }>;
  systems: Array<{ id: string; label: string; state: string; detail: string }>;
};

const navItems: Array<[ViewName, string, string]> = [
  ["dashboard", "COMMAND", "01"],
  ["evidence", "EVIDENCE", "02"],
  ["journal", "JOURNAL", "03"],
  ["system", "SYSTEM", "04"],
];

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function money(value: number | null): string {
  return value === null ? "—" : currency.format(value);
}

function signedMoney(value: number | null): string {
  if (value === null) return "—";
  return `${value > 0 ? "+" : ""}${currency.format(value)}`;
}

function timestamp(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: "America/Chicago",
    timeZoneName: "short",
  }).format(new Date(value));
}

function FrameHardware() {
  return (
    <>
      <i className="rivet rivet-nw" aria-hidden="true" />
      <i className="rivet rivet-ne" aria-hidden="true" />
      <i className="rivet rivet-sw" aria-hidden="true" />
      <i className="rivet rivet-se" aria-hidden="true" />
    </>
  );
}

function MetalPanel({ kicker, title, status, className = "", children }: {
  kicker: string;
  title: string;
  status?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <article className={`metal-panel ${className}`}>
      <FrameHardware />
      <header className="panel-titlebar">
        <div><span>{kicker}</span><h2>{title}</h2></div>
        {status && <b>{status}</b>}
      </header>
      {children}
    </article>
  );
}

function StatusCluster({ state }: { state: DashboardState }) {
  return (
    <div className="status-cluster" aria-label="Verified runtime status">
      {state.connections.map((item) => (
        <div className="status-node" key={item.id} title={item.detail}>
          <span className={`status-lamp ${item.state}`} aria-hidden="true" />
          <div><small>{item.label}</small><strong>{item.value}</strong></div>
        </div>
      ))}
    </div>
  );
}

function CandlestickChart({ candles }: { candles: Candle[] }) {
  if (candles.length === 0) {
    return <div className="candle-chart chart-unavailable">NO PRICE SERIES AVAILABLE</div>;
  }
  const highest = Math.max(...candles.map((candle) => candle.h));
  const lowest = Math.min(...candles.map((candle) => candle.l));
  const range = highest - lowest || 1;
  const y = (value: number) => ((highest - value) / range) * 100;

  return (
    <div className="candle-chart" aria-label="SPY replay candlestick chart">
      <div className="chart-grid" aria-hidden="true" />
      <div className="candles">
        {candles.map((candle) => {
          const rising = candle.c >= candle.o;
          const wickTop = y(candle.h);
          const wickHeight = Math.max(2, y(candle.l) - wickTop);
          const bodyTop = Math.min(y(candle.o), y(candle.c));
          const bodyHeight = Math.max(3, Math.abs(y(candle.o) - y(candle.c)));
          return (
            <div className={`candle ${rising ? "up" : "down"}`} key={candle.t} title={`${candle.t} O ${candle.o} H ${candle.h} L ${candle.l} C ${candle.c}`}>
              <i style={{ top: `${wickTop}%`, height: `${wickHeight}%` }} />
              <b style={{ top: `${bodyTop}%`, height: `${bodyHeight}%` }} />
            </div>
          );
        })}
      </div>
      <div className="chart-axis"><span>{highest.toFixed(2)}</span><span>{lowest.toFixed(2)}</span></div>
      <div className="chart-times"><span>{candles.at(0)?.t}</span><span>{candles.at(-1)?.t}</span></div>
    </div>
  );
}

function SpyPanel({ state }: { state: DashboardState }) {
  const changeTone = (state.market.change ?? 0) >= 0 ? "positive" : "negative";
  return (
    <MetalPanel kicker="PRIMARY UNDERLYING" title="SPY" status={`${state.market.data_state} DATA`} className="market-panel spy-market">
      <div className="spy-quote-row">
        <div><span>{state.market.data_state} PRICE</span><strong>{money(state.market.price)}</strong></div>
        <p className={changeTone}>{signedMoney(state.market.change)} <b>{state.market.change_percent === null ? "—" : `${state.market.change_percent > 0 ? "+" : ""}${state.market.change_percent.toFixed(2)}%`}</b></p>
      </div>
      <CandlestickChart candles={state.market.candles} />
      <footer className="data-footer"><span>{state.market.feed}</span><span>UPDATED {timestamp(state.market.last_update)}</span></footer>
    </MetalPanel>
  );
}

function RegimePanel({ state }: { state: DashboardState }) {
  const active = state.regime.state.toLowerCase();
  return (
    <MetalPanel kicker="DETERMINISTIC CONTEXT" title="MARKET REGIME" status={state.regime.session} className="market-panel regime-panel">
      <div className="regime-stage">
        <div className={`beast bull ${active === "bullish" ? "active" : ""}`}>
          <div className="beast-mark bull-mark" aria-hidden="true"><i /><b /><span /></div><small>BULL</small>
        </div>
        <div className="regime-dial"><span>{state.regime.state}</span><strong>{state.regime.support}</strong><small>SUPPORT</small></div>
        <div className={`beast bear ${active === "bearish" ? "active" : ""}`}>
          <div className="beast-mark bear-mark" aria-hidden="true"><i /><b /><span /></div><small>BEAR</small>
        </div>
      </div>
      <p className="panel-copy">{state.regime.detail}</p>
      <footer className="data-footer"><span>{state.market.session}</span><span>{state.regime.opposition} OPPOSING STATES</span></footer>
    </MetalPanel>
  );
}

function OptionsPanel({ state }: { state: DashboardState }) {
  const maximum = Math.max(...state.options.surface.map((item) => item.value), 1);
  return (
    <MetalPanel kicker="OPTIONS INTELLIGENCE" title="OPTIONS / OPRA" status={state.options.status} className="market-panel options-panel">
      <div className="options-metrics">
        <div><span>CHAIN HEALTH</span><strong>{state.options.chain_health}</strong></div>
        <div><span>ATM IV</span><strong>{state.options.atm_iv === null ? "—" : `${state.options.atm_iv.toFixed(2)}%`}</strong></div>
        <div><span>SKEW</span><strong>{state.options.skew === null ? "N/A" : `${state.options.skew.toFixed(2)}%`}</strong></div>
      </div>
      <div className="iv-surface" aria-label="Replay option implied volatility comparison">
        {state.options.surface.map((item) => (
          <div key={item.label}><i style={{ height: `${Math.max(18, (item.value / maximum) * 100)}%` }} /><span>{item.label}</span></div>
        ))}
      </div>
      <footer className="data-footer"><span>{state.options.skew_reason}</span><span>UPDATED {timestamp(state.options.last_update)}</span></footer>
    </MetalPanel>
  );
}

function ProposalPanel({ state, openEvidence }: { state: DashboardState; openEvidence: () => void }) {
  return (
    <MetalPanel kicker="ANALYST LAYER" title="TERRA PROPOSAL" status={`${state.mode} · ${state.proposal.time_horizon}`} className="decision-panel proposal-panel">
      <div className="direction-lockup"><span>DIRECTION</span><strong>{state.proposal.direction.replace("_", " ")}</strong><b>{state.proposal.uncertainty} UNCERTAINTY</b></div>
      <section className="argument"><span>THESIS</span><p>{state.proposal.thesis}</p></section>
      <section className="argument counterargument"><span>STRONGEST COUNTERARGUMENT</span><p>{state.proposal.counterargument}</p></section>
      <button className="passport-button" type="button" onClick={openEvidence}><span>OPEN EVIDENCE PASSPORT</span><b>{state.proposal.evidence_count} CITATIONS</b></button>
    </MetalPanel>
  );
}

function DecisionPanel({ state }: { state: DashboardState }) {
  return (
    <MetalPanel kicker="DETERMINISTIC SELECTION" title="CURRENT DECISION" status={state.decision.state.replaceAll("_", " ")} className="decision-panel current-decision">
      <div className="contract-hero"><span>SELECTED SPY OPTION</span><strong>{state.decision.contract_label ?? "NO CONTRACT"}</strong><small>{state.decision.symbol ?? "Selection unavailable"}</small></div>
      <div className="decision-metrics">
        <div><span>EXPIRATION</span><strong>{state.decision.expiration ?? "—"}</strong></div>
        <div><span>DTE</span><strong>{state.decision.dte ?? "—"}</strong></div>
        <div><span>QUANTITY AUTHORITY</span><strong>{state.decision.quantity_authority ?? "—"}</strong></div>
        <div><span>LIMIT PRICE</span><strong>{money(state.decision.limit_price)}</strong></div>
        <div><span>MAX DEBIT AUTHORITY</span><strong>{money(state.decision.authority_max_debit)}</strong></div>
        <div><span>PROPOSED RISK</span><strong>{money(state.decision.risk_amount)} · {state.decision.risk_percent === null ? "—" : `${state.decision.risk_percent.toFixed(2)}%`}</strong></div>
      </div>
      <div className="decision-warning"><span>UNCERTAINTY</span><strong>{state.decision.uncertainty}</strong><p>Replay candidate only. No client-order identity was reserved.</p></div>
    </MetalPanel>
  );
}

function RefereePanel({ state }: { state: DashboardState }) {
  const verdictMark = {
    APPROVE: "✓",
    REDUCE: "↓",
    ABSTAIN: "•",
    BLOCK: "×",
  }[state.decision.verdict];
  return (
    <MetalPanel kicker="AUTHORITY LAYER" title="REFEREE VERDICT" status="DETERMINISTIC" className={`decision-panel referee-panel verdict-${state.decision.verdict.toLowerCase()}`}>
      <div className="verdict-emblem"><span aria-hidden="true">{verdictMark}</span><strong>{state.decision.verdict}</strong><small>{state.mode} AUTHORITY ONLY</small></div>
      <ul className="reason-list">{state.decision.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
      <div className="broker-stop"><span>BROKER SUBMISSION</span><strong>{state.controls.broker_submission_allowed ? "ALLOWED" : "PROHIBITED"}</strong></div>
    </MetalPanel>
  );
}

function AccountPanel({ state }: { state: DashboardState }) {
  return (
    <MetalPanel kicker="DEDICATED JUDGING ACCOUNT" title="PAPER ACCOUNT" status="LAST VERIFIED" className="lower-panel account-panel">
      <div className="account-metrics">
        <div><span>EQUITY</span><strong>{money(state.account.equity)}</strong></div>
        <div><span>BUYING POWER</span><strong>{money(state.account.buying_power)}</strong></div>
        <div><span>DAY P&amp;L</span><strong>{signedMoney(state.account.day_pl)}</strong><small>Not returned by checkpoint</small></div>
        <div><span>OPEN P&amp;L</span><strong>{signedMoney(state.account.open_pl)}</strong><small>{state.account.position_count} positions</small></div>
      </div>
      <footer className="data-footer"><span>{state.account.source}</span><span>{timestamp(state.account.as_of)}</span></footer>
    </MetalPanel>
  );
}

function AuthorityRack({ state }: { state: DashboardState }) {
  const authorities = [
    {
      label: "ENTRY",
      value: state.controls.entry_enabled ? "ENABLED" : "DISABLED",
      detail: state.controls.entry_armed ? "PAPER GATE ARMED" : "PAPER GATE UNARMED",
      tone: state.controls.entry_enabled ? "enabled" : "disabled",
    },
    {
      label: "POSITION MANAGEMENT",
      value: state.controls.position_management_enabled ? "ENABLED" : "DISABLED",
      detail: state.controls.position_management_armed
        ? "VERIFIED EXITS MAY PROCEED"
        : "BROKER GATE UNARMED",
      tone: state.controls.position_management_enabled ? "enabled" : "disabled",
    },
    {
      label: "BROKER LOCK",
      value: state.controls.broker_lock_active ? "ACTIVE" : "CLEAR",
      detail: state.controls.broker_lock_active
        ? "ALL SUBMISSIONS FROZEN"
        : "COMPONENT HEALTH STILL APPLIES",
      tone: state.controls.broker_lock_active ? "active" : "clear",
    },
  ];
  return (
    <div className="authority-rack" aria-label="Broker authority controls">
      {authorities.map((authority) => (
        <div className={`authority-cell authority-${authority.tone}`} key={authority.label}>
          <span>{authority.label}</span>
          <strong>{authority.value}</strong>
          <small>{authority.detail}</small>
        </div>
      ))}
    </div>
  );
}

function ExecutionPanel({ state }: { state: DashboardState }) {
  return (
    <MetalPanel kicker="BROKER LIFECYCLE" title="EXECUTION STATUS" status={state.controls.broker_lock_active ? "BROKER LOCK ACTIVE" : "BROKER LOCK CLEAR"} className="lower-panel execution-panel">
      <AuthorityRack state={state} />
      <div className="execution-track">
        {state.execution.map((item, index) => (
          <div className={`execution-step ${item.status.toLowerCase().replace(" ", "-")}`} key={item.stage}>
            <i>{String(index + 1).padStart(2, "0")}</i><span>{item.stage}</span><strong>{item.status}</strong><small>{item.detail}</small>
          </div>
        ))}
      </div>
    </MetalPanel>
  );
}

function ActivityPanel({ state }: { state: DashboardState }) {
  return (
    <MetalPanel kicker="DURABLE JOURNAL" title="RECENT ACTIVITY" status={`${state.mode} EVENTS`} className="lower-panel activity-panel">
      <div className="activity-list">
        {state.activity.slice(0, 5).map((item) => (
          <div key={`${item.time}-${item.kind}`}><time>{item.time}</time><b>{item.kind}</b><p>{item.text}</p><span>{item.mode}</span></div>
        ))}
      </div>
    </MetalPanel>
  );
}

function HealthPanel({ state, showAuthority = false }: { state: DashboardState; showAuthority?: boolean }) {
  return (
    <MetalPanel kicker="FAIL-LOUD MONITORING" title="SYSTEM HEALTH" status={state.operational_state} className="lower-panel health-panel">
      {showAuthority && <AuthorityRack state={state} />}
      <div className="health-list">
        {state.systems.map((item) => (
          <div key={item.id}><span className={`health-dot state-${item.state.toLowerCase()}`} /><p><strong>{item.label}</strong><small>{item.detail}</small></p><b>{item.state}</b></div>
        ))}
      </div>
    </MetalPanel>
  );
}

function CommandView({ state, openEvidence }: { state: DashboardState; openEvidence: () => void }) {
  return (
    <div className="command-view">
      <section className="market-deck" aria-label="SPY market overview"><SpyPanel state={state} /><RegimePanel state={state} /><OptionsPanel state={state} /></section>
      <section className="decision-deck" aria-label="Current SPY decision"><ProposalPanel state={state} openEvidence={openEvidence} /><DecisionPanel state={state} /><RefereePanel state={state} /></section>
      <section className="lower-deck" aria-label="Account and operational status"><AccountPanel state={state} /><ExecutionPanel state={state} /><ActivityPanel state={state} /><HealthPanel state={state} /></section>
    </div>
  );
}

function EvidenceView({ state }: { state: DashboardState }) {
  return (
    <section className="detail-view">
      <MetalPanel kicker={`${state.mode} EVIDENCE PASSPORT`} title={state.passport.id} status={state.passport.sealed ? "SEALED" : "OPEN"} className="evidence-overview">
        <div className="evidence-columns">
          <section><span>THESIS</span><p>{state.proposal.thesis}</p><span>COUNTERARGUMENT</span><p>{state.proposal.counterargument}</p><span>STRUCTURED INVALIDATION</span><p>{state.proposal.invalidation}</p></section>
          <section className="passport-facts"><div><span>FIXTURE</span><strong>{state.passport.fixture_id}</strong></div><div><span>DIRECTION</span><strong>{state.proposal.direction}</strong></div><div><span>UNCERTAINTY</span><strong>{state.proposal.uncertainty}</strong></div><div><span>VERDICT</span><strong>{state.decision.verdict}</strong></div><div><span>AUTHORITY</span><strong>{state.decision.state}</strong></div><div><span>BROKER</span><strong>NOT SUBMITTED</strong></div></section>
        </div>
      </MetalPanel>
    </section>
  );
}

function JournalView({ state }: { state: DashboardState }) {
  return <section className="detail-view"><ActivityPanel state={state} /></section>;
}

function SystemView({ state }: { state: DashboardState }) {
  return <section className="detail-view"><HealthPanel state={state} showAuthority /></section>;
}

export default function Home() {
  const [activeView, setActiveView] = useState<ViewName>("dashboard");
  const [state, setState] = useState<DashboardState>(initialDashboardState as DashboardState);
  const [clock, setClock] = useState("--:--:--");

  useEffect(() => {
    const refreshClock = () => setClock(new Intl.DateTimeFormat("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "America/Chicago" }).format(new Date()));
    refreshClock();
    const clockTimer = window.setInterval(refreshClock, 1000);
    const refreshState = async () => {
      try {
        const response = await fetch(`/dashboard-state.json?t=${Date.now()}`, { cache: "no-store" });
        if (response.ok) setState(await response.json() as DashboardState);
      } catch {
        // Preserve the last verified state; its source and timestamp remain visible.
      }
    };
    void refreshState();
    const stateTimer = window.setInterval(refreshState, 30000);
    return () => { window.clearInterval(clockTimer); window.clearInterval(stateTimer); };
  }, []);

  return (
    <main className="page-shell">
      <section className={`terminal-frame mode-${state.mode.toLowerCase()}`} aria-label="CAJNMNSTR SPY options agent dashboard">
        <div className="frame-corner corner-nw" aria-hidden="true">♠</div><div className="frame-corner corner-ne" aria-hidden="true">⚙</div><div className="frame-corner corner-sw" aria-hidden="true">⚙</div><div className="frame-corner corner-se" aria-hidden="true">♠</div>
        <header className="masthead">
          <div className="brand-lockup">
            <div className="brand-mark"><Image src="/cajnmstr-icon.png" alt="Skull in a top hat inside a steel spade emblem" width={108} height={108} priority /></div>
            <div className="brand-type"><h1>CAJNMNSTR</h1><p>SPY OPTIONS AGENT</p></div>
          </div>
          <StatusCluster state={state} />
          <div className="account-head"><span>PAPER EQUITY · LAST VERIFIED</span><strong>{money(state.account.equity)}</strong><div><small>BUYING POWER</small><b>{money(state.account.buying_power)}</b></div><time>{timestamp(state.account.as_of)}</time></div>
        </header>

        <div className="truth-banner" role="status"><strong>{state.operational_state}</strong><span>{state.truth_label}</span><b>{clock} CT</b></div>

        {activeView === "dashboard" && <CommandView state={state} openEvidence={() => setActiveView("evidence")} />}
        {activeView === "evidence" && <EvidenceView state={state} />}
        {activeView === "journal" && <JournalView state={state} />}
        {activeView === "system" && <SystemView state={state} />}

        <nav className="bottom-nav" aria-label="Dashboard sections">
          <span className="nav-engraving" aria-hidden="true">♠</span>
          {navItems.map(([view, label, number]) => (
            <button type="button" key={view} className={activeView === view ? "active" : ""} aria-pressed={activeView === view} onClick={() => setActiveView(view)}><span>{number}</span><strong>{label}</strong></button>
          ))}
          <div className="mode-lock"><span className={`status-lamp ${state.controls.broker_lock_active ? "blocked" : "paused"}`} />ENTRY {state.controls.entry_enabled ? "ENABLED" : "DISABLED"} · PM {state.controls.position_management_enabled ? "ENABLED" : "DISABLED"} · LOCK {state.controls.broker_lock_active ? "ACTIVE" : "CLEAR"}</div>
        </nav>
        <footer className="terminal-footer"><span>EVIDENCE PASSPORT</span><i>◆</i><span>DETERMINISTIC REFEREE</span><i>◆</i><strong>STOP BEFORE BROKER</strong><i>◆</i><span>{state.account.open_order_count} OPEN ORDERS</span></footer>
      </section>
    </main>
  );
}
