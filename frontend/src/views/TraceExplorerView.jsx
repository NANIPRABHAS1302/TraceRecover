import React, { useEffect, useState } from 'react'
import {
  RefreshCw,
  ScrollText,
  Search,
  X,
} from 'lucide-react'
import { api, fmtMs, fmtNum, fmtPct, fmtTimestamp } from '../api'
import { DecisionBadge, RunInspectionPanel, ToolBadge } from '../components/Badges'

const DECISION_FILTERS = ['ALL', 'ANSWER', 'WARN', 'ABSTAIN', 'NOT_APPLICABLE']

export default function TraceExplorerView({
  initialTraceId,
  onClearInitialTrace,
  onInspectEvidence,
}) {
  const [traces, setTraces] = useState([])
  const [totalFiles, setTotalFiles] = useState(0)
  const [decision, setDecision] = useState('ALL')
  const [searchText, setSearchText] = useState('')
  const [loading, setLoading] = useState(false)

  const [selectedId, setSelectedId] = useState(initialTraceId || null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState(null)

  const fetchTraces = async () => {
    setLoading(true)
    try {
      const res = await api.getTraces({
        limit: 80,
        decision,
        search: searchText,
      })
      setTraces(res.traces || [])
      setTotalFiles(res.total_files || 0)
    } catch {
      setTraces([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchTraces()
  }, [decision])

  useEffect(() => {
    if (initialTraceId) {
      setSelectedId(initialTraceId)
      if (onClearInitialTrace) onClearInitialTrace()
    }
  }, [initialTraceId])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      return
    }
    let cancelled = false
    setDetailLoading(true)
    setDetailError(null)
    api
      .getTraceDetail(selectedId)
      .then((res) => {
        if (!cancelled) setDetail(res)
      })
      .catch((err) => {
        if (!cancelled) setDetailError(err.message || 'Failed to load trace detail.')
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selectedId])

  const handleSearchSubmit = (e) => {
    e.preventDefault()
    fetchTraces()
  }

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="rounded-xl border border-borderSubtle bg-panel p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <ScrollText className="h-4 w-4 text-cyan-400" />
            <h3 className="text-sm font-semibold text-white">
              Execution Trace Explorer
            </h3>
            <span className="rounded-full border border-borderSubtle bg-obsidian px-2.5 py-0.5 font-mono text-[11px] text-slate-300">
              {traces.length} shown / {totalFiles} total JSONL files in logs/
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <form onSubmit={handleSearchSubmit} className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-slate-500" />
              <input
                type="text"
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
                placeholder="Filter by trace_id, query, tool..."
                className="w-60 rounded-lg border border-borderSubtle bg-obsidian py-1.5 pl-8 pr-3 font-mono text-xs text-slate-100 placeholder-slate-500 focus:border-cyan-400 focus:outline-none"
              />
            </form>
            <button
              type="button"
              onClick={fetchTraces}
              className="inline-flex items-center gap-1.5 rounded-lg border border-borderSubtle bg-graphite px-3 py-1.5 text-xs text-slate-200 hover:bg-panelHover"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-1.5">
          {DECISION_FILTERS.map((d) => (
            <button
              key={d}
              type="button"
              onClick={() => setDecision(d)}
              className={`rounded-md px-2.5 py-1 font-mono text-[11px] font-medium transition-colors ${
                decision === d
                  ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40'
                  : 'bg-graphite text-slate-400 border border-borderSubtle hover:text-slate-200'
              }`}
            >
              {d}
            </button>
          ))}
        </div>
      </div>

      {/* Trace Table */}
      <div className="rounded-xl border border-borderSubtle bg-panel overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-borderSubtle bg-graphite/70 text-[11px] uppercase tracking-wider text-slate-400">
                <th className="px-4 py-3 font-medium">Trace ID</th>
                <th className="px-4 py-3 font-medium">Question / Input</th>
                <th className="px-4 py-3 font-medium">Decision</th>
                <th className="px-4 py-3 font-medium">Tools</th>
                <th className="px-4 py-3 font-medium">Committed / Discarded</th>
                <th className="px-4 py-3 font-medium">Failures / Healed</th>
                <th className="px-4 py-3 font-medium">Grounding</th>
                <th className="px-4 py-3 font-medium">Uncertainty</th>
                <th className="px-4 py-3 font-medium text-right">Latency</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-borderSubtle/60">
              {traces.map((t) => {
                const isSelected = selectedId === t.trace_id
                return (
                  <tr
                    key={t.trace_id}
                    onClick={() => setSelectedId(t.trace_id)}
                    className={`cursor-pointer transition-colors ${
                      isSelected ? 'bg-cyan-500/10' : 'hover:bg-panelHover/80'
                    }`}
                  >
                    <td className="px-4 py-3 font-mono text-[11px] text-cyan-300 whitespace-nowrap">
                      <div>{t.trace_id}</div>
                      <div className="text-[10px] text-slate-500">{fmtTimestamp(t.timestamp)}</div>
                    </td>
                    <td className="px-4 py-3 text-slate-200 max-w-xs truncate" title={t.question}>
                      {t.question}
                    </td>
                    <td className="px-4 py-3">
                      <DecisionBadge decision={t.final_decision} />
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {(t.tools_used || []).map((tool) => (
                          <ToolBadge key={tool} tool={tool} />
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-3 font-mono text-[11px] tabular-nums whitespace-nowrap">
                      <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-emerald-300">
                        {t.provenance?.committed_steps ?? 0} committed
                      </span>{' '}
                      <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-rose-300">
                        {t.provenance?.discarded_steps ?? 0} discarded
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-[11px] tabular-nums whitespace-nowrap">
                      <span className={t.total_failures > 0 ? 'text-rose-300' : 'text-slate-500'}>
                        {t.total_failures} fail
                      </span>
                      <span className="mx-1 text-slate-600">/</span>
                      <span className={t.successful_recoveries > 0 ? 'text-violet-300' : 'text-slate-500'}>
                        {t.successful_recoveries} healed
                      </span>
                    </td>
                    <td className="px-4 py-3 font-mono text-slate-200 tabular-nums">
                      {fmtPct(t.grounding_score, 0)}
                    </td>
                    <td className="px-4 py-3 font-mono text-slate-200 tabular-nums">
                      {fmtNum(t.uncertainty_score, 3)}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-slate-400 tabular-nums whitespace-nowrap">
                      {fmtMs(t.total_latency_seconds)}
                    </td>
                  </tr>
                )
              })}
              {!loading && traces.length === 0 && (
                <tr>
                  <td colSpan={9} className="px-4 py-10 text-center text-slate-500">
                    No matching JSONL traces found in logs/.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Sliding Detail Drawer */}
      {selectedId && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm">
          <div className="relative h-full w-full max-w-4xl overflow-y-auto border-l border-borderSubtle bg-obsidian p-6 shadow-2xl">
            <div className="sticky top-0 z-10 -mx-6 -mt-6 mb-5 flex items-center justify-between border-b border-borderSubtle bg-graphite/95 px-6 py-4 backdrop-blur">
              <div>
                <div className="font-mono text-xs text-cyan-400">{selectedId}</div>
                <h3 className="text-sm font-semibold text-white truncate max-w-2xl">
                  {detail?.question || 'Loading persisted JSONL trace...'}
                </h3>
              </div>
              <button
                type="button"
                onClick={() => setSelectedId(null)}
                className="rounded-lg border border-borderSubtle bg-panel p-1.5 text-slate-300 hover:bg-panelHover hover:text-white"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {detailLoading && (
              <div className="py-12 text-center font-mono text-xs text-slate-400">
                Loading trace steps and sidecar JSON reports...
              </div>
            )}
            {detailError && (
              <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-200">
                {detailError}
              </div>
            )}
            {!detailLoading && detail && (
              <RunInspectionPanel
                data={detail}
                compact
                onInspectEvidence={(tid) => {
                  setSelectedId(null)
                  onInspectEvidence(tid)
                }}
              />
            )}
          </div>
        </div>
      )}
    </div>
  )
}
