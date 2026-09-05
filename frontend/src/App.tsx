import React, { useState, useEffect, useRef, useCallback } from 'react'
import {
  fetchMetrics, fetchAudit, fetchCustomers,
  fetchGateRules, fetchCommsStatus, fetchLiveStatus, postRunBatch,
  MetricsSummary, AuditEntry, Customer, GateRules, CommsChannel, LiveEvent,
} from './api/client'

// ── Helpers ──────────────────────────────────────────────────────────────────
const PIPELINE_STAGES = ['fetch', 'enrich', 'classify', 'gate', 'decide', 'execute_log']
const STAGE_LABELS: Record<string, string> = {
  fetch: 'Fetch', enrich: 'Enrich', classify: 'Classify',
  gate: 'Gate', decide: 'Decide', execute_log: 'Execute',
}

function TrailDots({ trail }: { trail: number[] }) {
  return (
    <div className="trail">
      {trail.map((t, i) => (
        <span key={i} className={t === 1 ? 'done' : t === -1 ? 'blocked' : ''} />
      ))}
    </div>
  )
}

function StatusBadge({ status, label }: { status: string; label: string }) {
  return <span className={`status-badge ${status}`}>{label}</span>
}

function Initials({ id }: { id: string }) {
  const parts = id.replace(/_/g, ' ').replace(/-/g, ' ').split(' ').filter(Boolean)
  const init = parts.length >= 2
    ? (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
    : id.slice(0, 2).toUpperCase()
  return <div className="cust-avatar">{init}</div>
}

function Loading() { return <div className="loading">Loading…</div> }
function ErrMsg({ msg }: { msg: string }) { return <div className="error-msg">Error: {msg}</div> }

function useData<T>(fetcher: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetcher().then(d => { if (!cancelled) { setData(d); setLoading(false) } })
             .catch(e => { if (!cancelled) { setErr(String(e)); setLoading(false) } })
    return () => { cancelled = true }
  }, [])
  return { data, loading, err }
}

// ── AuditTable shared component ───────────────────────────────────────────────
function AuditTable({ rows, compact }: { rows: AuditEntry[]; compact?: boolean }) {
  if (!rows.length) return <div className="loading">No entries.</div>
  return (
    <table>
      <thead>
        <tr>
          <th>Customer</th>
          <th>Event</th>
          <th>Pipeline</th>
          {!compact && <th>Reasoning</th>}
          <th>Amount</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.id}>
            <td>
              <div className="cust">
                <Initials id={r.customer_id} />
                <div>
                  <div className="cust-name">{r.customer_id}</div>
                  <div className="cust-sub">{r.event_label}</div>
                </div>
              </div>
            </td>
            <td><span className="type-tag">{r.event_label}</span></td>
            <td><TrailDots trail={r.trail} /></td>
            {!compact && <td><span className="reason">{r.reasoning.slice(0, 100)}{r.reasoning.length > 100 ? '…' : ''}</span></td>}
            <td><span className="amount">{r.amount}</span></td>
            <td><StatusBadge status={r.status} label={r.status_label} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ── Page 1: Dashboard ─────────────────────────────────────────────────────────
function DashboardPage({ onAgent }: { onAgent: (a: string) => void }) {
  const { data: metrics, loading: mLoad, err: mErr } = useData(fetchMetrics)
  const { data: audit, loading: aLoad, err: aErr } = useData(() => fetchAudit())

  const agentOrder = ['payment_agent', 'cart_agent', 'renewal_agent', 'invoice_agent']
  const agentLabels: Record<string, string> = {
    payment_agent: 'Payment', cart_agent: 'Cart', renewal_agent: 'Renewal', invoice_agent: 'Invoice',
  }

  return (
    <>
      {/* Hero stats */}
      {mLoad && <Loading />}
      {mErr && <ErrMsg msg={mErr} />}
      {metrics && (
        <div className="stat-row">
          <div className="stat-card">
            <div className="stat-label">Recovered this week</div>
            <div className="stat-figure">₹{metrics.recovered_this_week.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</div>
            <div className="stat-sub">{metrics.recovery_rate_pct}% recovery rate</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Events processed</div>
            <div className="stat-figure">{metrics.events_processed}</div>
            <div className="stat-sub">Last 7 days</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Blocked by gate</div>
            <div className="stat-figure">{metrics.blocked_by_gate}</div>
            <div className="stat-sub">Risk & compliance</div>
          </div>
          <div className="stat-card">
            <div className="stat-label">Active agents</div>
            <div className="stat-figure">4</div>
            <div className="stat-sub">All live</div>
          </div>
        </div>
      )}

      {/* Agent cards */}
      {metrics && (
        <div>
          <div className="section-head" style={{ marginBottom: 12 }}>
            <h2>Agent performance</h2>
            <span>Click to drill down</span>
          </div>
          <div className="agent-grid">
            {agentOrder.map(key => {
              const a = metrics.per_agent[key] || { recovery_rate_pct: 0, events: 0, recovered_amount: 0 }
              return (
                <div key={key} className="agent-card" onClick={() => onAgent(key)}>
                  <div className="agent-top">
                    <div className="agent-name">{agentLabels[key]}</div>
                    <div className="agent-badge live">Live</div>
                  </div>
                  <div className="agent-metric-row">
                    <span>Recovery rate</span>
                    <b>{a.recovery_rate_pct.toFixed(0)}%</b>
                  </div>
                  <div className="agent-metric-row">
                    <span>Events</span>
                    <b>{a.events}</b>
                  </div>
                  <div className="agent-bar">
                    <div className="agent-bar-fill" style={{ width: `${a.recovery_rate_pct}%` }} />
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Audit preview */}
      <div>
        <div className="section-head" style={{ marginBottom: 12 }}>
          <h2>Recent decisions</h2>
          <span>Last 4 events</span>
        </div>
        <div className="audit-card">
          {aLoad && <Loading />}
          {aErr && <ErrMsg msg={aErr} />}
          {audit && <AuditTable rows={audit.entries.slice(0, 4)} compact />}
        </div>
      </div>
    </>
  )
}

// ── Page 2: Audit Log ─────────────────────────────────────────────────────────
function AuditPage() {
  const [filter, setFilter] = useState('all')
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    fetchAudit(filter === 'all' ? undefined : filter)
      .then(r => { setEntries(r.entries); setLoading(false) })
      .catch(e => { setErr(String(e)); setLoading(false) })
  }, [filter])

  const filters = ['all', 'recovered', 'blocked', 'pending', 'sent']

  return (
    <div className="audit-card">
      <div className="audit-toolbar">
        <h2>Audit log</h2>
        <div className="filters">
          {filters.map(f => (
            <button key={f} className={`filter ${filter === f ? 'active' : ''}`} onClick={() => setFilter(f)}>
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
      </div>
      {loading && <Loading />}
      {err && <ErrMsg msg={err} />}
      {!loading && !err && <AuditTable rows={entries} />}
    </div>
  )
}

// ── Page 3: Customers ─────────────────────────────────────────────────────────
function CustomersPage() {
  const { data, loading, err } = useData(fetchCustomers)
  if (loading) return <Loading />
  if (err) return <ErrMsg msg={err} />
  const customers = data?.customers || []
  return (
    <div className="cust-grid">
      {customers.map((c: Customer) => (
        <div key={c.customer_id} className="cust-card">
          <div className="cust-card-top">
            <div className="cust-avatar" style={{ width: 36, height: 36, fontSize: 13 }}>{c.initials}</div>
            <div>
              <div className="cust-card-name">{c.customer_id}</div>
              <div className="cust-card-meta">{c.meta_label}</div>
            </div>
          </div>
          <div className="cust-stat-row"><span>Recovered</span><b>{c.recovered_amount_display}</b></div>
          <div className="cust-stat-row"><span>Contacts</span><b>{c.contacts_label}</b></div>
          <div className="cust-stat-row"><span>Reliability</span><b>{c.reliability}</b></div>
        </div>
      ))}
      {customers.length === 0 && <div className="loading">No customers yet. Run a batch first.</div>}
    </div>
  )
}

// ── Pages 4–7: Agent Detail ───────────────────────────────────────────────────
const AGENT_SPECS: Record<string, {
  name: string; desc: string;
  stages: { n: number; name: string; tag: string; llm?: boolean }[];
  eventType: string;
}> = {
  payment_agent: {
    name: 'Payment', desc: 'Diagnoses why a payment failed and decides whether to retry, retry on an alternate route, or send a payment link.',
    eventType: 'payment_failed',
    stages: [
      { n: 1, name: 'Fetch', tag: 'Rules' }, { n: 2, name: 'Enrich', tag: 'Rules' },
      { n: 3, name: 'Classify', tag: 'Rules, LLM if ambiguous', llm: true },
      { n: 4, name: 'Gate', tag: 'Rules' }, { n: 5, name: 'Decide', tag: 'Rules' }, { n: 6, name: 'Execute', tag: 'Rules' },
    ],
  },
  cart_agent: {
    name: 'Cart', desc: 'Scores the likely reason a cart was abandoned and nudges the customer, escalating to a voice call if unresponsive.',
    eventType: 'cart_abandoned',
    stages: [
      { n: 1, name: 'Fetch', tag: 'Rules' }, { n: 2, name: 'Enrich', tag: 'Rules' },
      { n: 3, name: 'Diagnose', tag: 'LLM – no clean rule signal', llm: true },
      { n: 4, name: 'Gate', tag: 'Rules' }, { n: 5, name: 'Decide', tag: 'Rules' }, { n: 6, name: 'Execute', tag: 'Rules' },
    ],
  },
  renewal_agent: {
    name: 'Renewal', desc: 'Classifies why a subscription renewal failed and prompts a card update, applies a grace period, or escalates.',
    eventType: 'renewal_failed',
    stages: [
      { n: 1, name: 'Fetch', tag: 'Rules' }, { n: 2, name: 'Enrich', tag: 'Rules' },
      { n: 3, name: 'Classify', tag: 'Rules' },
      { n: 4, name: 'Gate', tag: 'Rules' }, { n: 5, name: 'Decide', tag: 'Rules' }, { n: 6, name: 'Execute', tag: 'Rules' },
    ],
  },
  invoice_agent: {
    name: 'Invoice', desc: 'Buckets overdue invoices by days overdue and scores call priority to decide who gets a phone call.',
    eventType: 'invoice_overdue',
    stages: [
      { n: 1, name: 'Fetch', tag: 'Rules' }, { n: 2, name: 'Enrich', tag: 'Rules' },
      { n: 3, name: 'Classify', tag: 'Rules' }, { n: 4, name: 'Gate', tag: 'Rules' },
      { n: 5, name: 'Decide', tag: 'Rules, LLM for call priority', llm: true }, { n: 6, name: 'Execute', tag: 'Rules' },
    ],
  },
}

function AgentDetailPage({ agentKey, onBack }: { agentKey: string; onBack: () => void }) {
  const spec = AGENT_SPECS[agentKey]
  const { data: metrics } = useData(fetchMetrics)
  const { data: audit, loading, err } = useData(() => fetchAudit())

  if (!spec) return <div className="error-msg">Unknown agent: {agentKey}</div>

  const agentMetrics = metrics?.per_agent[agentKey]
  const agentRows = (audit?.entries || []).filter(r => r.event_type === spec.eventType)

  return (
    <>
      <button className="back-link" onClick={onBack}>← Back to dashboard</button>
      <div className="agent-detail-head">
        <div className="agent-detail-title">
          <div className="agent-icon">{spec.name[0]}</div>
          <div>
            <h1>{spec.name} agent</h1>
            <p>{spec.desc}</p>
          </div>
        </div>
        {agentMetrics && (
          <div className="stat-card" style={{ minWidth: 140 }}>
            <div className="stat-label">Recovery rate</div>
            <div className="stat-figure">{agentMetrics.recovery_rate_pct.toFixed(0)}%</div>
            <div className="stat-sub">{agentMetrics.events} events</div>
          </div>
        )}
      </div>

      <div>
        <div className="section-head" style={{ marginBottom: 12 }}>
          <h2>Pipeline</h2><span>Six stages, same shape as every agent</span>
        </div>
        <div className="stage-flow">
          {spec.stages.map(s => (
            <div key={s.n} className={`stage${s.llm ? ' llm' : ''}`}>
              <div className="stage-num">{s.n}</div>
              <div className="stage-name">{s.name}</div>
              <div className="stage-tag">{s.tag}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="audit-card">
        <div className="audit-toolbar"><h2>Recent decisions</h2></div>
        {loading && <Loading />}
        {err && <ErrMsg msg={err} />}
        {!loading && <AuditTable rows={agentRows.slice(0, 10)} />}
      </div>
    </>
  )
}

// ── Page 8: Gate Rules ────────────────────────────────────────────────────────
function GateRulesPage() {
  const { data, loading, err } = useData(fetchGateRules)
  if (loading) return <Loading />
  if (err) return <ErrMsg msg={err} />
  if (!data) return null
  const agentOrder = ['payment_agent', 'cart_agent', 'renewal_agent', 'invoice_agent']
  const agentLabels: Record<string, string> = {
    payment_agent: 'Payment Agent', cart_agent: 'Cart Agent',
    renewal_agent: 'Renewal Agent', invoice_agent: 'Invoice Agent',
  }
  return (
    <>
      <div className="section-head" style={{ marginBottom: 16 }}>
        <h2>Risk gate rules</h2>
        <span className="deterministic-tag">Deterministic — never an LLM call</span>
      </div>
      <p style={{ fontSize: 13, color: 'var(--text-2)', marginBottom: 16, lineHeight: 1.5 }}>{data.description}</p>
      <div className="gate-grid">
        {agentOrder.map(key => {
          const rules = data.agents[key] || {}
          const entries = Object.entries(rules).filter(([k]) => k !== 'notes')
          return (
            <div key={key} className="gate-card">
              <div className="gate-card-head">
                <h3>{agentLabels[key]}</h3>
                <span className="deterministic-tag">Deterministic</span>
              </div>
              {entries.map(([k, v]) => (
                <div key={k} className="gate-rule">
                  <span className="gate-rule-name">{k.replace(/_/g, ' ')}</span>
                  <span className="gate-rule-val">{String(v)}</span>
                </div>
              ))}
              {rules.notes && (
                <p style={{ fontSize: 11.5, color: 'var(--text-3)', marginTop: 8, lineHeight: 1.4 }}>{rules.notes as string}</p>
              )}
            </div>
          )
        })}
      </div>
    </>
  )
}

// ── Page 9: Comms Channels ────────────────────────────────────────────────────
function CommsPage() {
  const { data, loading, err } = useData(fetchCommsStatus)
  if (loading) return <Loading />
  if (err) return <ErrMsg msg={err} />
  return (
    <div className="channel-grid">
      {(data?.channels || []).map((ch: CommsChannel) => (
        <div key={ch.name} className="channel-card">
          <div className="channel-top">
            <div className="channel-name">{ch.name}</div>
            <span className={`channel-status ${ch.status}`}>{ch.status_label}</span>
          </div>
          <p className="channel-desc">{ch.description}</p>
          <div className="channel-meta">{ch.meta}</div>
        </div>
      ))}
    </div>
  )
}

// ── Page 10: Live Run ─────────────────────────────────────────────────────────
function LiveRunPage() {
  const [events, setEvents] = useState<LiveEvent[]>([])
  const [stats, setStats] = useState<{ total: number; complete: number; in_flight: number } | null>(null)
  const [polling, setPolling] = useState(false)
  const [running, setRunning] = useState(false)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const poll = useCallback(async () => {
    try {
      const s = await fetchLiveStatus()
      setEvents(s.events)
      setStats(s.stats)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    poll()
    intervalRef.current = setInterval(poll, 1500)
    setPolling(true)
    return () => { if (intervalRef.current) clearInterval(intervalRef.current) }
  }, [poll])

  const handleRunBatch = async () => {
    setRunning(true)
    try { await postRunBatch() } catch { /* already started or error */ }
    setTimeout(() => setRunning(false), 5000)
  }

  return (
    <>
      <div className="live-header">
        <div>
          <h2 style={{ fontSize: 14, fontWeight: 600 }}>Live pipeline run</h2>
          <p style={{ fontSize: 12.5, color: 'var(--text-2)', marginTop: 2 }}>
            {stats ? `${stats.in_flight} in-flight · ${stats.complete} complete` : 'Waiting for batch…'}
          </p>
        </div>
        <button className="run-btn" onClick={handleRunBatch} disabled={running}>
          {running ? 'Starting…' : '▶ Run batch'}
        </button>
      </div>

      <div className="live-table-wrap">
        {events.length === 0 ? (
          <div className="live-empty">
            No events yet. Click "Run batch" or start <code>python scripts/run_batch.py</code> in a terminal.
          </div>
        ) : (
          events.map(ev => {
            const stagesDone = new Set(ev.stages_done || [])
            const currentIdx = PIPELINE_STAGES.indexOf(ev.stage)
            return (
              <div key={ev.event_id} className="live-event-row">
                <div className="live-event-id">{ev.event_id.slice(0, 16)}…</div>
                <div className="live-agent">{(ev.agent || 'routing').replace('_agent', '')}</div>
                <div className="live-stages">
                  {PIPELINE_STAGES.map((s, i) => {
                    const done = stagesDone.has(s)
                    const active = s === ev.stage && !ev.complete
                    const cls = ev.complete && s === 'execute_log' ? 'done'
                      : active ? 'active'
                      : done ? 'done'
                      : ''
                    return (
                      <div key={s} className={`live-stage-dot ${cls}`}>
                        <div className="dot-circle" />
                        <div className="live-stage-label">{STAGE_LABELS[s]}</div>
                      </div>
                    )
                  })}
                </div>
                <div className="live-action">
                  {ev.complete ? (ev.action || 'done') : ev.stage}
                </div>
              </div>
            )
          })
        )}
      </div>
    </>
  )
}

// ── App shell ─────────────────────────────────────────────────────────────────
type View = 'dashboard' | 'audit' | 'customers' | 'agent' | 'gate-rules' | 'comms' | 'live'

const VIEW_TITLES: Record<string, [string, string]> = {
  dashboard:  ['Recovery overview', 'What the agents did today, and why.'],
  audit:      ['Audit log', 'Every action taken, with the reasoning behind it.'],
  customers:  ['Customers', 'Recovery history by customer.'],
  agent:      ['Agent detail', 'Six-stage pipeline.'],
  'gate-rules': ['Risk gate rules', 'Deterministic bounds — never an LLM call.'],
  comms:      ['Comms channels', 'Shared senders, used by all four agents.'],
  live:       ['Live run', 'Watch each event move through the pipeline in real time.'],
}

const AGENT_VIEW_TITLES: Record<string, [string, string]> = {
  payment_agent: ['Payment agent', 'Six-stage pipeline for failed payments.'],
  cart_agent:    ['Cart agent', 'Six-stage pipeline for abandoned carts.'],
  renewal_agent: ['Renewal agent', 'Six-stage pipeline for failed renewals.'],
  invoice_agent: ['Invoice agent', 'Six-stage pipeline for overdue invoices.'],
}

export default function App() {
  const [view, setView] = useState<View>('dashboard')
  const [agentKey, setAgentKey] = useState<string>('payment_agent')

  const goAgent = (key: string) => { setAgentKey(key); setView('agent') }
  const titles = view === 'agent' ? AGENT_VIEW_TITLES[agentKey] : VIEW_TITLES[view]

  const navItem = (v: View | string, label: string, isAgent?: boolean, agentK?: string) => {
    const active = isAgent ? (view === 'agent' && agentKey === agentK) : view === v
    return (
      <button
        className={`nav-item ${active ? 'active' : ''}`}
        onClick={() => isAgent && agentK ? goAgent(agentK) : setView(v as View)}
      >
        <span className="nav-dot" />{label}
      </button>
    )
  }

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          <div className="brand-mark">R</div>
          <div>
            <div className="brand-name">Recoupe</div>
            <div className="brand-sub">Revenue recovery</div>
          </div>
        </div>
        <nav>
          <div className="nav-label">Overview</div>
          {navItem('dashboard', 'Dashboard')}
          {navItem('audit', 'Audit log')}
          {navItem('customers', 'Customers')}
          {navItem('live', 'Live run')}

          <div className="nav-label">Agents</div>
          {navItem('agent', 'Payment', true, 'payment_agent')}
          {navItem('agent', 'Cart', true, 'cart_agent')}
          {navItem('agent', 'Renewal', true, 'renewal_agent')}
          {navItem('agent', 'Invoice', true, 'invoice_agent')}

          <div className="nav-label">System</div>
          {navItem('gate-rules', 'Risk gate rules')}
          {navItem('comms', 'Comms channels')}
        </nav>
        <div className="side-foot">Recoupe <b>v5.0</b> · Phase 5</div>
      </aside>

      <div className="main">
        <div className="topbar">
          <div>
            <h1>{titles?.[0] || 'Recoupe'}</h1>
            <p>{titles?.[1] || ''}</p>
          </div>
          <div className="top-right">
            <div className="pill"><span className="dot" />All agents live</div>
            <div className="avatar">AK</div>
          </div>
        </div>

        <div className="content">
          {view === 'dashboard'  && <DashboardPage onAgent={goAgent} />}
          {view === 'audit'      && <AuditPage />}
          {view === 'customers'  && <CustomersPage />}
          {view === 'agent'      && <AgentDetailPage agentKey={agentKey} onBack={() => setView('dashboard')} />}
          {view === 'gate-rules' && <GateRulesPage />}
          {view === 'comms'      && <CommsPage />}
          {view === 'live'       && <LiveRunPage />}
        </div>
      </div>
    </div>
  )
}
