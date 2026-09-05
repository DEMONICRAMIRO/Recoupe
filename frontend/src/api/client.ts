// API client — all calls to backend endpoints
// Vite proxies /api/* → http://localhost:8000/*

const BASE = '/api'

async function get<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(`${BASE}${path}`, window.location.origin)
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v))
  const res = await fetch(url.toString())
  if (!res.ok) throw new Error(`API ${path}: ${res.status} ${res.statusText}`)
  return res.json()
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`API POST ${path}: ${res.status}`)
  return res.json()
}

// --- Types ---
export interface MetricsSummary {
  window_days: number
  events_processed: number
  blocked_by_gate: number
  recovered_this_week: number
  recovery_rate_pct: number
  per_agent: Record<string, {
    events: number
    recovered: number
    blocked: number
    recovery_rate_pct: number
    recovered_amount: number
  }>
}

export interface AuditEntry {
  id: number
  event_id: string
  customer_id: string
  event_type: string
  event_label: string
  action_taken: string
  reasoning: string
  amount: string
  timestamp: string
  status: string
  status_label: string
  trail: number[]
}

export interface AuditResponse {
  total: number
  offset: number
  limit: number
  entries: AuditEntry[]
}

export interface Customer {
  customer_id: string
  initials: string
  preferred_channel: string
  reliability: string
  contact_count_7d: number
  past_failures: number
  event_count: number
  event_types: string[]
  meta_label: string
  recovered_amount: number
  recovered_amount_display: string
  contacts_label: string
}

export interface GateRules {
  deterministic: boolean
  description: string
  agents: Record<string, Record<string, unknown>>
}

export interface CommsChannel {
  name: string
  status: string
  status_label: string
  description: string
  provider: string
  used_by: string[]
  meta: string
}

export interface LiveEvent {
  event_id: string
  agent: string | null
  stage: string
  action: string | null
  started_at: string
  updated_at: string
  complete: boolean
  stages_done: string[]
}

export interface LiveStatus {
  events: LiveEvent[]
  stats: { total: number; complete: number; in_flight: number; by_agent: Record<string, number> }
}

// --- API functions ---
export const fetchMetrics = () => get<MetricsSummary>('/metrics/summary')

export const fetchAudit = (status?: string, customer_id?: string) => {
  const p: Record<string, string> = { days: '30', limit: '200' }
  if (status && status !== 'all') p.status = status
  if (customer_id) p.customer_id = customer_id
  return get<AuditResponse>('/audit/entries', p)
}

export const fetchCustomers = () => get<{ customers: Customer[]; total: number }>('/customers/')

export const fetchGateRules = () => get<GateRules>('/gate-rules/')

export const fetchCommsStatus = () => get<{ channels: CommsChannel[] }>('/comms/status')

export const fetchLiveStatus = () => get<LiveStatus>('/live-status/')

export const postRunBatch = () => post<{ status: string; message: string }>('/events/run-batch')
