import React, { useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Cpu,
  FileCode2,
  GitBranch,
  ShieldAlert,
  ShieldCheck,
  ShieldOff,
  Sparkles,
  Wrench,
  XCircle,
} from 'lucide-react'
import { fmtMs, fmtNum, fmtPct, stringifyPayload } from '../api'

export function DecisionBadge({ decision, size = 'sm' }) {
  const norm = String(decision || 'NOT_APPLICABLE').toUpperCase()
  const styles = {
    ANSWER:
      'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
    WARN: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
    ABSTAIN: 'bg-rose-500/15 text-rose-300 border-rose-500/40',
    NOT_APPLICABLE: 'bg-slate-500/15 text-slate-300 border-slate-500/40',
  }
  const icons = {
    ANSWER: CheckCircle2,
    WARN: AlertTriangle,
    ABSTAIN: ShieldAlert,
    NOT_APPLICABLE: ShieldOff,
  }
  const Icon = icons[norm] || ShieldOff
  const pad = size === 'lg' ? 'px-3 py-1.5 text-sm' : 'px-2 py-0.5 text-[11px]'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border font-mono font-semibold tracking-wide ${pad} ${
        styles[norm] || styles.NOT_APPLICABLE
      }`}
    >
      <Icon className={size === 'lg' ? 'h-4 w-4' : 'h-3 w-3'} />
      {norm}
    </span>
  )
}

export function ProvenanceBadge({ state }) {
  const norm = String(state || 'committed').toLowerCase()
  if (norm === 'discarded') {
    return (
      <span className="inline-flex items-center gap-1 rounded border border-rose-500/40 bg-rose-500/15 px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider text-rose-300">
        <XCircle className="h-3 w-3" />
        discarded
      </span>
    )
  }
  if (norm === 'final') {
    return (
      <span className="inline-flex items-center gap-1 rounded border border-cyan-500/40 bg-cyan-500/15 px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider text-cyan-300">
        <Sparkles className="h-3 w-3" />
        final
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded border border-emerald-500/40 bg-emerald-500/15 px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider text-emerald-300">
      <CheckCircle2 className="h-3 w-3" />
      committed
    </span>
  )
}

export function ClaimStatusBadge({ status }) {
  const norm = String(status || 'UNSUPPORTED').toUpperCase()
  const map = {
    SUPPORTED: 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300',
    CONTRADICTED: 'border-rose-500/40 bg-rose-500/15 text-rose-300',
    UNSUPPORTED: 'border-amber-500/40 bg-amber-500/15 text-amber-300',
  }
  return (
    <span
      className={`inline-flex items-center rounded border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider ${
        map[norm] || map.UNSUPPORTED
      }`}
    >
      {norm}
    </span>
  )
}

export function ToolBadge({ tool }) {
  const colors = {
    search: 'border-sky-500/30 bg-sky-500/10 text-sky-300',
    calculator: 'border-indigo-500/30 bg-indigo-500/10 text-indigo-300',
    python_exec: 'border-violet-500/30 bg-violet-500/10 text-violet-300',
    mock_api: 'border-teal-500/30 bg-teal-500/10 text-teal-300',
    final: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300',
  }
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 font-mono text-[11px] font-medium ${
        colors[tool] || 'border-slate-600 bg-slate-800 text-slate-300'
      }`}
    >
      <Wrench className="h-3 w-3" />
      {tool}
    </span>
  )
}

export function MetricBar({ label, value, weight, tone = 'cyan', sublabel }) {
  const isAvailable = value !== null && value !== undefined && !Number.isNaN(Number(value))
  const pct = isAvailable ? Math.max(0, Math.min(100, Number(value) * 100)) : 0
  const barColors = {
    cyan: 'bg-cyan-400',
    emerald: 'bg-emerald-400',
    violet: 'bg-violet-400',
    amber: 'bg-amber-400',
    rose: 'bg-rose-400',
  }
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-slate-300">
          {label}
          {weight !== undefined && weight !== null && (
            <span className="ml-1.5 font-mono text-[10px] text-slate-500">
              (w={Number(weight).toFixed(2)})
            </span>
          )}
        </span>
        <span className="font-mono text-xs text-slate-200 tabular-nums">
          {isAvailable ? fmtNum(value, 3) : 'N/A'}
          {sublabel ? <span className="ml-1.5 text-[10px] text-slate-500">{sublabel}</span> : null}
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-800/90">
        <div
          className={`h-full rounded-full transition-all duration-300 ${
            isAvailable ? barColors[tone] || barColors.cyan : 'bg-slate-700'
          }`}
          style={{ width: `${isAvailable ? pct : 0}%` }}
        />
      </div>
    </div>
  )
}

export function RunInspectionPanel({ data, compact = false, onInspectEvidence }) {
  const [showRawJson, setShowRawJson] = useState(false)
  if (!data) return null

  const decision = String(data.final_decision || 'NOT_APPLICABLE').toUpperCase()
  const bannerStyles = {
    ANSWER:
      'border-emerald-500/40 bg-gradient-to-r from-emerald-500/15 via-emerald-500/5 to-transparent',
    WARN:
      'border-amber-500/40 bg-gradient-to-r from-amber-500/15 via-amber-500/5 to-transparent',
    ABSTAIN:
      'border-rose-500/40 bg-gradient-to-r from-rose-500/15 via-rose-500/5 to-transparent',
    NOT_APPLICABLE:
      'border-slate-600/60 bg-gradient-to-r from-slate-700/20 via-slate-800/10 to-transparent',
  }

  const unc = data.uncertainty_report || {}
  const gr = data.grounding_report || {}
  const fr = data.failure_report || {}
  const rr = data.recovery_report || {}
  const steps = data.steps || []
  const claims = gr.claims || []
  const prov = data.provenance || {}
  const signals = unc.signal_values || {}
  const normWeights = unc.normalized_signal_weights || {}

  return (
    <div className="space-y-4">
      {/* Decision Banner */}
      <div className={`rounded-xl border p-4 ${bannerStyles[decision] || bannerStyles.NOT_APPLICABLE}`}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <DecisionBadge decision={decision} size="lg" />
              {data.trace_id && (
                <span className="rounded bg-obsidian/80 px-2 py-1 font-mono text-[11px] text-slate-400 border border-borderSubtle">
                  {data.trace_id}
                </span>
              )}
              {data.elapsed_seconds !== undefined && (
                <span className="inline-flex items-center gap-1 font-mono text-xs text-slate-400">
                  <Clock className="h-3.5 w-3.5 text-cyan-400" />
                  {fmtMs(data.elapsed_seconds)}
                </span>
              )}
            </div>
            <p className="text-xs text-slate-300">
              {unc.decision_explanation ||
                (decision === 'NOT_APPLICABLE'
                  ? 'Uncertainty estimation was disabled for this run.'
                  : 'Decision evaluated by AbstentionPolicy.')}
            </p>
          </div>
          <div className="flex flex-wrap gap-3 text-right">
            <div className="rounded-lg border border-borderSubtle bg-obsidian/70 px-3 py-1.5">
              <div className="text-[10px] uppercase tracking-wider text-slate-400">Uncertainty</div>
              <div className="font-mono text-base font-bold text-slate-100 tabular-nums">
                {unc.uncertainty_score !== null && unc.uncertainty_score !== undefined
                  ? fmtNum(unc.uncertainty_score, 3)
                  : 'N/A'}
              </div>
            </div>
            <div className="rounded-lg border border-borderSubtle bg-obsidian/70 px-3 py-1.5">
              <div className="text-[10px] uppercase tracking-wider text-slate-400">Grounding</div>
              <div className="font-mono text-base font-bold text-cyan-300 tabular-nums">
                {gr.grounding_score !== null && gr.grounding_score !== undefined
                  ? fmtPct(gr.grounding_score, 0)
                  : gr.status === 'DISABLED'
                  ? 'OFF'
                  : 'N/A'}
              </div>
            </div>
            <div className="rounded-lg border border-borderSubtle bg-obsidian/70 px-3 py-1.5">
              <div className="text-[10px] uppercase tracking-wider text-slate-400">Provenance</div>
              <div className="font-mono text-sm font-semibold tabular-nums">
                <span className="text-emerald-400">{prov.committed_steps ?? 0}c</span>
                <span className="mx-1 text-slate-600">/</span>
                <span className="text-rose-400">{prov.discarded_steps ?? 0}d</span>
              </div>
            </div>
          </div>
        </div>

        <div className="mt-3 rounded-lg border border-borderSubtle/80 bg-obsidian/80 p-3">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            Final User-Facing Response
          </div>
          <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-slate-100">
            {data.final_response || data.answer || '(empty response)'}
          </pre>
          {data.answer && data.answer !== data.final_response && (
            <div className="mt-2 border-t border-borderSubtle/60 pt-2 text-[11px] text-slate-400">
              <span className="font-semibold text-slate-300">Raw candidate answer (audit): </span>
              <span className="font-mono">{data.answer}</span>
            </div>
          )}
        </div>
      </div>

      {/* Step-by-Step Execution Timeline */}
      <div className="rounded-xl border border-borderSubtle bg-panel p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <GitBranch className="h-4 w-4 text-cyan-400" />
            <h4 className="text-sm font-semibold text-slate-100">
              Execution Trace Timeline ({steps.length} steps)
            </h4>
          </div>
          <div className="flex items-center gap-2 text-xs font-mono">
            <span className="text-rose-300">
              Failures: {fr.total_failures ?? 0}
            </span>
            <span className="text-slate-600">|</span>
            <span className="text-violet-300">
              Recovered: {rr.successful_recoveries ?? 0}/{rr.total_detected_failures ?? 0}
            </span>
            {rr.strategies_used && rr.strategies_used.length > 0 && (
              <>
                <span className="text-slate-600">|</span>
                <span className="rounded bg-violet-500/20 px-1.5 py-0.5 text-[11px] text-violet-200">
                  {rr.strategies_used.join(', ')}
                </span>
              </>
            )}
          </div>
        </div>

        <div className="space-y-2.5">
          {steps.map((step) => {
            const state = step.provenance_state || step.output_state || 'committed'
            const isDiscarded = state === 'discarded'
            const isRecovered = Boolean(step.recovered)
            return (
              <div
                key={`${step.step_id}-${step.timestamp || ''}`}
                className={`rounded-lg border p-3 transition-colors ${
                  isDiscarded
                    ? 'border-rose-500/35 bg-rose-950/15'
                    : isRecovered
                    ? 'border-violet-500/40 bg-violet-950/15'
                    : 'border-borderSubtle bg-graphite/80'
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-[11px] font-bold text-slate-200">
                      Step {step.step_id}
                    </span>
                    <ToolBadge tool={step.tool} />
                    <ProvenanceBadge state={state} />
                    {step.failure_injected && (
                      <span className="rounded border border-rose-500/40 bg-rose-500/20 px-2 py-0.5 font-mono text-[10px] font-semibold uppercase text-rose-200">
                        INJECTED: {step.failure_type || 'fault'}
                      </span>
                    )}
                    {isRecovered && (
                      <span className="rounded border border-violet-500/40 bg-violet-500/20 px-2 py-0.5 font-mono text-[10px] font-semibold uppercase text-violet-200">
                        HEALED via {step.recovery_strategy} (from step {step.original_step_id})
                      </span>
                    )}
                  </div>
                  <span className="font-mono text-[11px] text-slate-400 tabular-nums">
                    {fmtMs(step.latency)}
                  </span>
                </div>

                {step.thought && (
                  <p className="mt-2 text-xs text-slate-300">
                    <span className="font-semibold text-slate-400">Thought: </span>
                    {step.thought}
                  </p>
                )}

                <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-2">
                  <div className="rounded border border-borderSubtle/70 bg-obsidian/70 p-2">
                    <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">
                      Input Arguments
                    </div>
                    <pre className="max-h-28 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-slate-300">
                      {stringifyPayload(step.input)}
                    </pre>
                  </div>
                  <div className="rounded border border-borderSubtle/70 bg-obsidian/70 p-2">
                    <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">
                      {step.error ? 'Error' : 'Output'}
                    </div>
                    {step.error ? (
                      <pre className="max-h-28 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-rose-300">
                        {step.error}
                      </pre>
                    ) : (
                      <pre className="max-h-28 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-emerald-200">
                        {stringifyPayload(step.output)}
                      </pre>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Failure Localization + Recovery Detail (only when failures exist) */}
      {(fr.failures?.length > 0 || rr.failures?.length > 0) && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="rounded-xl border border-rose-500/30 bg-panel p-4">
            <h4 className="mb-2.5 flex items-center gap-2 text-sm font-semibold text-rose-200">
              <AlertTriangle className="h-4 w-4 text-rose-400" />
              Localized Failures ({fr.total_failures ?? 0})
            </h4>
            <div className="space-y-2">
              {(fr.failures || []).map((f, idx) => (
                <div
                  key={idx}
                  className="rounded-lg border border-rose-500/25 bg-obsidian/70 p-2.5 text-xs"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-mono font-semibold text-rose-300">
                      Step {f.step_id} · {f.tool_name} · {f.failure_category}
                    </span>
                    <span className="rounded bg-rose-500/15 px-1.5 py-0.5 font-mono text-[10px] uppercase text-rose-200">
                      {f.severity || 'high'} / {f.failure_origin || 'injected'}
                    </span>
                  </div>
                  {f.explanation && (
                    <p className="mt-1 text-slate-400">{f.explanation}</p>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-xl border border-violet-500/30 bg-panel p-4">
            <h4 className="mb-2.5 flex items-center gap-2 text-sm font-semibold text-violet-200">
              <Cpu className="h-4 w-4 text-violet-400" />
              Self-Healing Recovery Attempts ({rr.total_recovery_attempts ?? 0})
            </h4>
            <div className="space-y-2">
              {(rr.failures || []).flatMap((inc) => inc.attempts || []).map((att, idx) => (
                <div
                  key={idx}
                  className="rounded-lg border border-violet-500/25 bg-obsidian/70 p-2.5 text-xs"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-mono font-semibold text-violet-200">
                      Attempt #{att.attempt_number} · {att.strategy} ({att.recovery_tool})
                    </span>
                    <span
                      className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase ${
                        att.recovery_success
                          ? 'bg-emerald-500/20 text-emerald-300'
                          : 'bg-rose-500/20 text-rose-300'
                      }`}
                    >
                      {att.recovery_success ? 'VALIDATED' : 'FAILED'}
                    </span>
                  </div>
                  {att.reason && <p className="mt-1 text-slate-400">{att.reason}</p>}
                  {att.recovery_trace_step_id && (
                    <div className="mt-1 font-mono text-[11px] text-emerald-300">
                      Committed as trace step {att.recovery_trace_step_id}
                    </div>
                  )}
                </div>
              ))}
              {(!rr.failures || rr.failures.length === 0) && (
                <p className="text-xs text-slate-400">
                  Recovery was disabled or no localized incidents triggered recovery.
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Atomic Claims + Uncertainty Signals */}
      <div className={`grid grid-cols-1 gap-4 ${compact ? '' : 'lg:grid-cols-12'}`}>
        <div className={`rounded-xl border border-borderSubtle bg-panel p-4 ${compact ? '' : 'lg:col-span-7'}`}>
          <div className="mb-3 flex items-center justify-between gap-2">
            <h4 className="flex items-center gap-2 text-sm font-semibold text-slate-100">
              <ShieldCheck className="h-4 w-4 text-emerald-400" />
              Atomic Claim Verification ({claims.length})
            </h4>
            {onInspectEvidence && data.trace_id && (
              <button
                type="button"
                onClick={() => onInspectEvidence(data.trace_id)}
                className="text-xs font-medium text-cyan-400 hover:text-cyan-300"
              >
                Open in Evidence Inspector →
              </button>
            )}
          </div>
          {claims.length === 0 ? (
            <p className="text-xs text-slate-400">
              {gr.status === 'DISABLED'
                ? 'Evidence grounding was disabled for this run.'
                : 'No factual claims were extracted from the candidate answer.'}
            </p>
          ) : (
            <div className="space-y-2.5">
              {claims.map((claim) => {
                const citedSteps = [...new Set((claim.evidence || []).map((e) => e.step_id))]
                const firstNli = (claim.evidence || []).find((e) => e.nli)?.nli
                return (
                  <div
                    key={claim.claim_id}
                    className="rounded-lg border border-borderSubtle bg-graphite/80 p-3"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[11px] text-slate-400">
                          {claim.claim_id}
                        </span>
                        <ClaimStatusBadge status={claim.status} />
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5">
                        {citedSteps.length > 0 ? (
                          citedSteps.map((sid) => (
                            <span
                              key={sid}
                              className="rounded bg-cyan-500/15 px-2 py-0.5 font-mono text-[10px] text-cyan-300 border border-cyan-500/30"
                            >
                              step {sid}
                            </span>
                          ))
                        ) : (
                          <span className="font-mono text-[10px] text-slate-500">
                            no citation
                          </span>
                        )}
                      </div>
                    </div>
                    <p className="mt-1.5 font-mono text-xs text-slate-100">{claim.claim}</p>
                    {claim.explanation && (
                      <p className="mt-1 text-[11px] text-slate-400">{claim.explanation}</p>
                    )}
                    {firstNli && (
                      <div className="mt-2 flex flex-wrap items-center gap-3 rounded bg-obsidian/80 px-2.5 py-1.5 font-mono text-[10px] text-slate-300">
                        <span className="text-cyan-400">NLI ({firstNli.backend}):</span>
                        <span className="font-semibold">{firstNli.label}</span>
                        <span className="text-emerald-300">
                          E={fmtNum(firstNli.scores?.ENTAILMENT, 2)}
                        </span>
                        <span className="text-slate-400">
                          N={fmtNum(firstNli.scores?.NEUTRAL, 2)}
                        </span>
                        <span className="text-rose-300">
                          C={fmtNum(firstNli.scores?.CONTRADICTION, 2)}
                        </span>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>

        <div className={`rounded-xl border border-borderSubtle bg-panel p-4 ${compact ? '' : 'lg:col-span-5'}`}>
          <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-100">
            <AlertTriangle className="h-4 w-4 text-amber-400" />
            Uncertainty Signal Breakdown
          </h4>
          {unc.status === 'DISABLED' ? (
            <p className="text-xs text-slate-400">
              Uncertainty estimation and abstention thresholds were disabled for this run.
            </p>
          ) : (
            <div className="space-y-3">
              <MetricBar
                label="Grounding Score"
                value={signals.grounding ?? unc.grounding_score}
                weight={normWeights.grounding}
                tone="emerald"
              />
              <MetricBar
                label="Tool Success Ratio"
                value={signals.tool_success ?? unc.tool_success_ratio}
                weight={normWeights.tool_success}
                tone="cyan"
                sublabel={
                  unc.tool_execution_count
                    ? `${unc.tool_success_count}/${unc.tool_execution_count}`
                    : ''
                }
              />
              <MetricBar
                label="Recovery Reliability"
                value={signals.recovery_reliability ?? unc.recovery_reliability}
                weight={normWeights.recovery_reliability}
                tone="violet"
              />
              <MetricBar
                label="Self-Consistency"
                value={signals.self_consistency ?? unc.self_consistency_score}
                weight={normWeights.self_consistency}
                tone="amber"
              />

              <div className="mt-3 rounded-lg border border-borderSubtle bg-obsidian/80 p-3 space-y-1.5 text-xs">
                <div className="flex justify-between font-mono">
                  <span className="text-slate-400">Combined Reliability</span>
                  <span className="text-slate-100">
                    {fmtNum(unc.combined_reliability, 3)}
                  </span>
                </div>
                <div className="flex justify-between font-mono">
                  <span className="text-slate-400">Uncertainty (1 - rel)</span>
                  <span className="font-bold text-cyan-300">
                    {fmtNum(unc.uncertainty_score, 3)}
                  </span>
                </div>
                {unc.thresholds && (
                  <div className="flex justify-between font-mono text-[11px] text-slate-400 pt-1 border-t border-borderSubtle/70">
                    <span>Policy Thresholds</span>
                    <span>
                      ANS ≤ {unc.thresholds.answer_threshold} · ABS ≥ {unc.thresholds.abstain_threshold}
                    </span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Collapsible Raw JSON Payload */}
      <div className="rounded-xl border border-borderSubtle bg-panel">
        <button
          type="button"
          onClick={() => setShowRawJson((v) => !v)}
          className="flex w-full items-center justify-between px-4 py-2.5 text-xs font-medium text-slate-300 hover:bg-panelHover rounded-xl"
        >
          <span className="flex items-center gap-2">
            <FileCode2 className="h-4 w-4 text-slate-400" />
            Raw Structured Execution Payload (JSON)
          </span>
          {showRawJson ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>
        {showRawJson && (
          <pre className="max-h-96 overflow-auto border-t border-borderSubtle bg-obsidian p-4 font-mono text-[11px] text-slate-300">
            {JSON.stringify(data, null, 2)}
          </pre>
        )}
      </div>
    </div>
  )
}
