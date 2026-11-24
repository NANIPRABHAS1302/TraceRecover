const JSON_HEADERS = { 'Content-Type': 'application/json' }

async function request(url, options = {}) {
  const res = await fetch(url, options)
  const text = await res.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { detail: text }
  }
  if (!res.ok) {
    const message =
      (data && (data.detail || data.message)) ||
      `Request failed with status ${res.status}`
    throw new Error(typeof message === 'string' ? message : JSON.stringify(message))
  }
  return data
}

export const api = {
  getHealth: () => request('/api/health'),
  getPresets: () => request('/api/presets'),
  getBenchmark: () => request('/api/benchmark'),
  getSettings: () => request('/api/settings'),
  getTraces: ({ limit = 60, decision = '', search = '' } = {}) => {
    const params = new URLSearchParams()
    params.set('limit', String(limit))
    if (decision && decision !== 'ALL') params.set('decision', decision)
    if (search && search.trim()) params.set('search', search.trim())
    return request(`/api/traces?${params.toString()}`)
  },
  getTraceDetail: (traceId) =>
    request(`/api/traces/${encodeURIComponent(traceId)}`),
  runAgent: (payload) =>
    request('/api/run', {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    }),
}

export function fmtPct(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return 'N/A'
  }
  return `${(Number(value) * 100).toFixed(digits)}%`
}

export function fmtNum(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return 'N/A'
  }
  return Number(value).toFixed(digits)
}

export function fmtMs(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) {
    return 'N/A'
  }
  const ms = Number(seconds) * 1000
  return ms < 10 ? `${ms.toFixed(2)} ms` : `${ms.toFixed(1)} ms`
}

export function fmtPValue(p) {
  if (p === null || p === undefined || Number.isNaN(Number(p))) return 'N/A'
  const num = Number(p)
  if (num < 0.0001) return num.toExponential(2)
  return num.toFixed(4)
}

export function fmtTimestamp(iso) {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, {
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    })
  } catch {
    return String(iso)
  }
}

export function stringifyPayload(value) {
  if (value === null || value === undefined) return 'null'
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
