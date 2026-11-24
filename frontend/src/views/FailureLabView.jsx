import React, { useState } from 'react'
import {
  AlertOctagon,
  Columns2,
  FlaskConical,
  Play,
  ShieldCheck,
  ShieldOff,
  Zap,
} from 'lucide-react'
import { api, fmtMs, fmtNum } from '../api'
import { DecisionBadge, RunInspectionPanel } from '../components/Badges'

const FAILURE_MODES = [
  {
    id: 'timeout',
    label: 'Timeout',
    desc: 'Simulates TimeoutError -> triggers RETRY strategy',
    color: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  },
  {
    id: 'wrong_output',
    label: 'Wrong Output',
    desc: 'Corrupts numeric/text return -> triggers TOOL_SWITCH / REPLAN',
    color: 'border-rose-500/40 bg-rose-500/10 text-rose-300',
  },
  {
    id: 'empty_result',
    label: 'Empty Result',
    desc: 'Returns None payload -> triggers RETRY then TOOL_SWITCH',
    color: 'border-violet-500/40 bg-violet-500/10 text-violet-300',
  },
  {
    id: 'bad_arguments',
    label: 'Bad Arguments',
    desc: 'Strips required args -> triggers ARGUMENT_REPAIR',
    color: 'border-cyan-500/40 bg-cyan-500/10 text-cyan-300',
  },
]

const SAMPLE_PROBES = [
  { label: 'Arithmetic (Calculator -> Python fallback)', q: 'What is 19 * 23 + 4?' },
  { label: 'Factual Search (Paris)', q: 'What is the capital of France?' },
  { label: 'SLA Ticket Lookup (T-401)', q: 'What is the status and owner of ticket T-401?' },
  { label: 'Missing Entity (Abstain test)', q: 'Who discovered the fictional element unobtainium?' },
]

export default function FailureLabView({ onRunRecorded, onInspectEvidence }) {
  const [question, setQuestion] = useState('What is 19 * 23 + 4?')
  const [selectedTypes, setSelectedTypes] = useState([
    'timeout',
    'wrong_output',
    'empty_result',
    'bad_arguments',
  ])
  const [failureRate, setFailureRate] = useState(40)
  const [seed, setSeed] = useState(13)
  const [maxRetries, setMaxRetries] = useState(1)
  const [maxAttempts, setMaxAttempts] = useState(4)
  const [useNli, setUseNli] = useState(true)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [singleResult, setSingleResult] = useState(null)
  const [comparison, setComparison] = useState(null)
  const [history, setHistory] = useState([])

  const toggleType = (id) => {
    setSelectedTypes((prev) =>
      prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]
    )
  }

  const runSingle = async () => {
    setLoading(true)
    setError(null)
    setComparison(null)
    try {
      const res = await api.runAgent({
        question,
        provider: 'local',
        failure_rate: failureRate,
        seed,
        enabled_failure_types: selectedTypes,
        use_nli: useNli,
        enable_recovery: true,
        enable_grounding: true,
        enable_uncertainty: true,
        max_retries: maxRetries,
        max_recovery_attempts: maxAttempts,
      })
      setSingleResult(res)
      setHistory((prev) => [{ mode: 'TraceRecover', result: res }, ...prev].slice(0, 12))
      if (onRunRecorded) onRunRecorded()
    } catch (err) {
      setError(err.message || 'Fault injection run failed.')
    } finally {
      setLoading(false)
    }
  }

  const runSideBySide = async () => {
    setLoading(true)
    setError(null)
    setSingleResult(null)
    try {
      const basePayload = {
        question,
        provider: 'local',
        failure_rate: failureRate,
        seed,
        enabled_failure_types: selectedTypes,
        max_retries: maxRetries,
        max_recovery_attempts: maxAttempts,
      }
      const [baselineRes, trRes] = await Promise.all([
        api.runAgent({
          ...basePayload,
          use_nli: false,
          enable_recovery: false,
          enable_grounding: false,
          enable_uncertainty: false,
          enable_localization: false,
        }),
        api.runAgent({
          ...basePayload,
          use_nli: useNli,
          enable_recovery: true,
          enable_grounding: true,
          enable_uncertainty: true,
          enable_localization: true,
        }),
      ])
      setComparison({ baseline: baselineRes, tracerecover: trRes })
      setHistory((prev) =>
        [
          { mode: 'TraceRecover (A/B)', result: trRes },
          { mode: 'Baseline (A/B)', result: baselineRes },
          ...prev,
        ].slice(0, 12)
      )
      if (onRunRecorded) onRunRecorded()
    } catch (err) {
      setError(err.message || 'Side-by-side fault experiment failed.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-borderSubtle bg-panel p-5 space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <FlaskConical className="h-4 w-4 text-violet-400" />
            <h3 className="text-sm font-semibold text-white">
              Seeded Fault-Injection &amp; Self-Healing Laboratory
            </h3>
          </div>
          <span className="font-mono text-[11px] text-slate-400">
            Reproducible local FailureInjector · zero core mutations
          </span>
        </div>

        {/* Failure Mode Checkcards */}
        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
          {FAILURE_MODES.map((m) => {
            const active = selectedTypes.includes(m.id)
            return (
              <button
                key={m.id}
                type="button"
                onClick={() => toggleType(m.id)}
                className={`rounded-xl border p-3 text-left transition-all ${
                  active
                    ? m.color
                    : 'border-borderSubtle bg-graphite/60 text-slate-500 opacity-60 hover:opacity-100'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs font-bold uppercase">{m.label}</span>
                  <span className="font-mono text-[10px]">{active ? 'ON' : 'OFF'}</span>
                </div>
                <p className="mt-1 text-[11px] text-slate-300/90">{m.desc}</p>
              </button>
            )
          })}
        </div>

        {/* Query & Parameters */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <div className="lg:col-span-6 space-y-2">
            <label className="text-xs font-medium text-slate-300">Target Query</label>
            <input
              type="text"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              className="w-full rounded-lg border border-borderSubtle bg-obsidian px-3 py-2 font-mono text-xs text-slate-100 focus:border-cyan-400 focus:outline-none"
            />
            <div className="flex flex-wrap gap-1.5">
              {SAMPLE_PROBES.map((p) => (
                <button
                  key={p.label}
                  type="button"
                  onClick={() => setQuestion(p.q)}
                  className="rounded border border-borderSubtle bg-graphite px-2 py-1 text-[10px] text-slate-300 hover:border-cyan-500/40 hover:text-cyan-200"
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <div className="lg:col-span-6 grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="col-span-2 space-y-1">
              <div className="flex justify-between text-xs">
                <span className="text-slate-300">Failure Rate</span>
                <span className="font-mono font-bold text-rose-300">{failureRate}%</span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                step={10}
                value={failureRate}
                onChange={(e) => setFailureRate(Number(e.target.value))}
                className="w-full accent-rose-400"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-slate-300">Seed</label>
              <input
                type="number"
                value={seed}
                onChange={(e) => setSeed(Number(e.target.value))}
                className="w-full rounded-lg border border-borderSubtle bg-obsidian px-2.5 py-1.5 font-mono text-xs text-slate-100"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-slate-300">Max Retries</label>
              <input
                type="number"
                min={0}
                max={5}
                value={maxRetries}
                onChange={(e) => setMaxRetries(Number(e.target.value))}
                className="w-full rounded-lg border border-borderSubtle bg-obsidian px-2.5 py-1.5 font-mono text-xs text-slate-100"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-slate-300">Max Attempts</label>
              <input
                type="number"
                min={0}
                max={10}
                value={maxAttempts}
                onChange={(e) => setMaxAttempts(Number(e.target.value))}
                className="w-full rounded-lg border border-borderSubtle bg-obsidian px-2.5 py-1.5 font-mono text-xs text-slate-100"
              />
            </div>
            <div className="col-span-3 flex items-end justify-end gap-2">
              <button
                type="button"
                disabled={loading || selectedTypes.length === 0}
                onClick={runSingle}
                className="inline-flex items-center gap-1.5 rounded-lg border border-cyan-500/40 bg-cyan-500/15 px-3.5 py-2 text-xs font-semibold text-cyan-200 hover:bg-cyan-500/25 disabled:opacity-50"
              >
                {loading ? <Zap className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                Inject &amp; Recover
              </button>
              <button
                type="button"
                disabled={loading || selectedTypes.length === 0}
                onClick={runSideBySide}
                className="inline-flex items-center gap-1.5 rounded-lg bg-violet-500 px-3.5 py-2 text-xs font-bold text-white hover:bg-violet-400 disabled:opacity-50"
              >
                <Columns2 className="h-3.5 w-3.5" />
                Compare Baseline vs. TraceRecover
              </button>
            </div>
          </div>
        </div>

        {error && (
          <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-200">
            {error}
          </div>
        )}
      </div>

      {/* Side-by-Side Comparison */}
      {comparison && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <div className="space-y-3 rounded-xl border border-slate-600/60 bg-panel/70 p-4">
            <div className="flex items-center justify-between border-b border-borderSubtle pb-2.5">
              <div className="flex items-center gap-2">
                <ShieldOff className="h-4 w-4 text-slate-400" />
                <span className="text-sm font-semibold text-slate-200">
                  Baseline (Recovery / Grounding / Uncertainty OFF)
                </span>
              </div>
              <DecisionBadge decision={comparison.baseline.final_decision} />
            </div>
            <RunInspectionPanel data={comparison.baseline} compact />
          </div>

          <div className="space-y-3 rounded-xl border border-cyan-500/40 bg-panel/70 p-4">
            <div className="flex items-center justify-between border-b border-borderSubtle pb-2.5">
              <div className="flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-cyan-400" />
                <span className="text-sm font-semibold text-white">
                  Full TraceRecover (Same Query, Fault Rate &amp; Seed)
                </span>
              </div>
              <DecisionBadge decision={comparison.tracerecover.final_decision} />
            </div>
            <RunInspectionPanel
              data={comparison.tracerecover}
              compact
              onInspectEvidence={onInspectEvidence}
            />
          </div>
        </div>
      )}

      {/* Single Result */}
      {singleResult && (
        <RunInspectionPanel
          data={singleResult}
          onInspectEvidence={onInspectEvidence}
        />
      )}

      {/* Session Experiment Log */}
      {history.length > 0 && (
        <div className="rounded-xl border border-borderSubtle bg-panel p-4">
          <h4 className="mb-2.5 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
            <AlertOctagon className="h-3.5 w-3.5 text-violet-400" />
            Session Fault Experiments ({history.length})
          </h4>
          <div className="space-y-1.5">
            {history.map((item, i) => (
              <div
                key={`${item.result.trace_id}-${i}`}
                onClick={() => {
                  setComparison(null)
                  setSingleResult(item.result)
                }}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-borderSubtle/70 bg-graphite px-3 py-2 text-xs cursor-pointer hover:bg-panelHover"
              >
                <div className="flex items-center gap-2">
                  <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                    {item.mode}
                  </span>
                  <span className="font-mono text-[11px] text-cyan-300">
                    {item.result.trace_id}
                  </span>
                  <span className="text-slate-300 truncate max-w-xs">{item.result.question}</span>
                </div>
                <div className="flex items-center gap-3 font-mono text-[11px]">
                  <span className="text-rose-300">
                    {item.result.failure_report?.total_failures ?? 0} fail
                  </span>
                  <span className="text-violet-300">
                    {item.result.recovery_report?.successful_recoveries ?? 0} healed
                  </span>
                  <span className="text-slate-400">
                    u={fmtNum(item.result.uncertainty_report?.uncertainty_score, 3)}
                  </span>
                  <span className="text-slate-500">{fmtMs(item.result.elapsed_seconds)}</span>
                  <DecisionBadge decision={item.result.final_decision} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
