import React, { useEffect, useState } from 'react'
import {
  Cpu,
  KeyRound,
  Lock,
  Settings2,
  ShieldCheck,
  Sliders,
} from 'lucide-react'
import { api, fmtNum, stringifyPayload } from '../api'
import {
  ClaimStatusBadge,
  DecisionBadge,
  ProvenanceBadge,
  ToolBadge,
} from '../components/Badges'

export default function EvidenceSettingsView({
  settings,
  traces,
  selectedTraceId,
  onSelectTraceId,
}) {
  const [traceDetail, setTraceDetail] = useState(null)
  const [loadingTrace, setLoadingTrace] = useState(false)

  const activeTraceId =
    selectedTraceId || (traces && traces.length > 0 ? traces[0].trace_id : null)

  useEffect(() => {
    if (!activeTraceId) {
      setTraceDetail(null)
      return
    }
    let cancelled = false
    setLoadingTrace(true)
    api
      .getTraceDetail(activeTraceId)
      .then((res) => {
        if (!cancelled) setTraceDetail(res)
      })
      .catch(() => {
        if (!cancelled) setTraceDetail(null)
      })
      .finally(() => {
        if (!cancelled) setLoadingTrace(false)
      })
    return () => {
      cancelled = true
    }
  }, [activeTraceId])

  const stepsById = {}
  for (const s of traceDetail?.steps || []) {
    stepsById[String(s.step_id)] = s
  }
  const discardedSteps = (traceDetail?.steps || []).filter(
    (s) => (s.provenance_state || s.output_state) === 'discarded'
  )
  const claims = traceDetail?.grounding_report?.claims || []

  return (
    <div className="space-y-6">
      {/* Section 1: Atomic Claim Provenance Inspector */}
      <div className="rounded-xl border border-borderSubtle bg-panel p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-emerald-400" />
            <h3 className="text-sm font-semibold text-white">
              Atomic Claim Provenance Inspector
            </h3>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-slate-400">Inspect Trace:</label>
            <select
              value={activeTraceId || ''}
              onChange={(e) => onSelectTraceId(e.target.value)}
              className="max-w-xs rounded-lg border border-borderSubtle bg-obsidian px-2.5 py-1.5 font-mono text-xs text-slate-100 focus:border-cyan-400 focus:outline-none"
            >
              {(traces || []).slice(0, 30).map((t) => (
                <option key={t.trace_id} value={t.trace_id}>
                  {t.trace_id} · {t.final_decision} · {t.question.slice(0, 36)}
                </option>
              ))}
            </select>
          </div>
        </div>

        {loadingTrace ? (
          <div className="py-8 text-center font-mono text-xs text-slate-400">
            Loading claim provenance...
          </div>
        ) : traceDetail ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-borderSubtle bg-graphite/80 px-3.5 py-2.5 text-xs">
              <div className="truncate max-w-2xl">
                <span className="text-slate-400">Query: </span>
                <span className="font-medium text-slate-100">{traceDetail.question}</span>
              </div>
              <div className="flex items-center gap-2">
                <DecisionBadge decision={traceDetail.final_decision} />
                <span className="font-mono text-[11px] text-cyan-300">
                  Score: {fmtNum(traceDetail.grounding_report?.grounding_score, 3)}
                </span>
              </div>
            </div>

            {claims.length === 0 ? (
              <div className="rounded-lg border border-dashed border-borderSubtle p-6 text-center text-xs text-slate-400">
                No factual claims recorded for this trace (grounding disabled or zero-claim output).
              </div>
            ) : (
              <div className="space-y-3">
                {claims.map((claim) => (
                  <div
                    key={claim.claim_id}
                    className="grid grid-cols-1 gap-3 rounded-xl border border-borderSubtle bg-graphite/90 p-4 lg:grid-cols-12"
                  >
                    <div className="lg:col-span-6 space-y-2">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs text-slate-400">{claim.claim_id}</span>
                        <ClaimStatusBadge status={claim.status} />
                      </div>
                      <p className="font-mono text-xs text-white leading-relaxed">{claim.claim}</p>
                      <p className="text-[11px] text-slate-400">{claim.explanation}</p>
                    </div>

                    <div className="lg:col-span-6 space-y-2 border-t lg:border-t-0 lg:border-l border-borderSubtle pt-3 lg:pt-0 lg:pl-4">
                      <div className="font-mono text-[10px] uppercase tracking-wider text-slate-400">
                        Linked Committed Evidence Sources ({(claim.evidence || []).length})
                      </div>
                      {(claim.evidence || []).length === 0 ? (
                        <div className="rounded border border-amber-500/30 bg-amber-500/10 px-2.5 py-2 text-[11px] text-amber-200">
                          No eligible committed tool step supports this claim.
                        </div>
                      ) : (
                        (claim.evidence || []).map((ev, idx) => {
                          const linkedStep = stepsById[String(ev.step_id)]
                          return (
                            <div
                              key={idx}
                              className="rounded-lg border border-borderSubtle bg-obsidian/80 p-2.5 space-y-1.5 text-xs"
                            >
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <div className="flex items-center gap-1.5">
                                  <span className="rounded bg-cyan-500/15 px-1.5 py-0.5 font-mono text-[10px] font-bold text-cyan-300">
                                    Step {ev.step_id}
                                  </span>
                                  <ToolBadge tool={ev.tool} />
                                  <ProvenanceBadge
                                    state={linkedStep?.provenance_state || 'committed'}
                                  />
                                </div>
                                <span className="font-mono text-[10px] text-slate-400">
                                  {ev.source_type} · {ev.relation}
                                </span>
                              </div>
                              <pre className="max-h-24 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-emerald-200/90">
                                {stringifyPayload(ev.matched_text)}
                              </pre>
                              {ev.nli && (
                                <div className="flex flex-wrap gap-3 font-mono text-[10px] text-slate-400 pt-1 border-t border-borderSubtle/60">
                                  <span className="text-cyan-300">{ev.nli.backend}</span>
                                  <span>Label: {ev.nli.label}</span>
                                  <span className="text-emerald-300">
                                    E {fmtNum(ev.nli.scores?.ENTAILMENT, 3)}
                                  </span>
                                  <span>N {fmtNum(ev.nli.scores?.NEUTRAL, 3)}</span>
                                  <span className="text-rose-300">
                                    C {fmtNum(ev.nli.scores?.CONTRADICTION, 3)}
                                  </span>
                                </div>
                              )}
                            </div>
                          )
                        })
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {discardedSteps.length > 0 && (
              <div className="rounded-lg border border-rose-500/30 bg-rose-950/15 p-3">
                <div className="mb-1.5 font-mono text-[11px] font-semibold text-rose-300">
                  Excluded Discarded Steps ({discardedSteps.length} — quarantined from citation eligibility)
                </div>
                <div className="flex flex-wrap gap-2">
                  {discardedSteps.map((s) => (
                    <span
                      key={s.step_id}
                      className="rounded border border-rose-500/30 bg-obsidian px-2 py-1 font-mono text-[11px] text-rose-200"
                    >
                      Step {s.step_id} ({s.tool}): {s.failure_type || s.error || 'discarded'}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <p className="text-xs text-slate-400">No persisted traces available yet.</p>
        )}
      </div>

      {/* Section 2: Read-Only Active System Settings & Calibrated Thresholds */}
      {settings && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {/* Thresholds & Weights */}
          <div className="rounded-xl border border-borderSubtle bg-panel p-5 space-y-4">
            <div className="flex items-center gap-2">
              <Sliders className="h-4 w-4 text-cyan-400" />
              <h3 className="text-sm font-semibold text-white">
                Uncertainty Weights &amp; Calibrated Thresholds
              </h3>
            </div>

            <div className="grid grid-cols-2 gap-3 font-mono text-xs">
              <div className="rounded-lg border border-borderSubtle bg-graphite p-3">
                <div className="text-[10px] uppercase text-slate-400">Default Policy</div>
                <div className="mt-1 text-slate-100">
                  ANSWER ≤ {settings.uncertainty_and_abstention.default_thresholds.answer_threshold}
                </div>
                <div className="text-slate-100">
                  ABSTAIN ≥ {settings.uncertainty_and_abstention.default_thresholds.abstain_threshold}
                </div>
              </div>
              <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3">
                <div className="text-[10px] uppercase text-emerald-300">5-Fold Calibrated Mean</div>
                {settings.uncertainty_and_abstention.calibrated_5fold_thresholds?.summary ? (
                  <>
                    <div className="mt-1 text-white">
                      ANSWER ≤{' '}
                      {fmtNum(
                        settings.uncertainty_and_abstention.calibrated_5fold_thresholds.summary
                          .answer_threshold_mean,
                        4
                      )}
                    </div>
                    <div className="text-white">
                      ABSTAIN ≥{' '}
                      {fmtNum(
                        settings.uncertainty_and_abstention.calibrated_5fold_thresholds.summary
                          .abstain_threshold_mean,
                        4
                      )}
                    </div>
                  </>
                ) : (
                  <div className="mt-1 text-slate-400">N/A</div>
                )}
              </div>
            </div>

            <div className="space-y-1.5">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                Signal Weights (DEFAULT_SIGNAL_WEIGHTS)
              </div>
              {Object.entries(settings.uncertainty_and_abstention.signal_weights).map(
                ([name, weight]) => (
                  <div
                    key={name}
                    className="flex items-center justify-between rounded border border-borderSubtle/70 bg-obsidian/70 px-3 py-1.5 font-mono text-xs"
                  >
                    <span className="text-slate-300">{name}</span>
                    <span className="text-cyan-300">{Number(weight).toFixed(2)}</span>
                  </div>
                )
              )}
              <div className="flex items-center justify-between rounded border border-borderSubtle/70 bg-obsidian/70 px-3 py-1.5 font-mono text-xs">
                <span className="text-slate-400">repeated_attempt_penalty</span>
                <span className="text-amber-300">
                  {settings.uncertainty_and_abstention.repeated_attempt_penalty}
                </span>
              </div>
            </div>
          </div>

          {/* Grounding, NLI & Recovery Budgets */}
          <div className="rounded-xl border border-borderSubtle bg-panel p-5 space-y-4">
            <div className="flex items-center gap-2">
              <Settings2 className="h-4 w-4 text-violet-400" />
              <h3 className="text-sm font-semibold text-white">
                NLI Verifier, Recovery Budgets &amp; AST Guards
              </h3>
            </div>

            <div className="space-y-2 font-mono text-xs">
              <div className="rounded-lg border border-borderSubtle bg-graphite p-3 space-y-1">
                <div className="text-[10px] uppercase text-slate-400">NLI Grounding Engine</div>
                <div className="text-cyan-300 break-all">
                  {settings.grounding_and_nli.default_nli_model}
                </div>
                <div className="text-slate-400 text-[11px]">
                  Active backend: <span className="text-slate-200">{settings.grounding_and_nli.active_nli_backend}</span> ·
                  Entail ≥ {settings.grounding_and_nli.entailment_threshold} · Contra ≥{' '}
                  {settings.grounding_and_nli.contradiction_threshold}
                </div>
              </div>

              <div className="rounded-lg border border-borderSubtle bg-graphite p-3 space-y-1">
                <div className="text-[10px] uppercase text-slate-400">Recovery Engine Limits</div>
                <div className="flex flex-wrap gap-4 text-slate-200">
                  <span>max_retries={settings.recovery.max_retries}</span>
                  <span>max_attempts={settings.recovery.max_total_attempts}</span>
                  <span>timeout={settings.recovery.max_duration_seconds}s</span>
                </div>
              </div>

              <div className="rounded-lg border border-borderSubtle bg-graphite p-3 space-y-1">
                <div className="text-[10px] uppercase text-slate-400 flex items-center gap-1.5">
                  <Lock className="h-3 w-3 text-emerald-400" />
                  Pre-Execution AST &amp; Schema Guards
                </div>
                <div className="text-[11px] text-slate-300">
                  Max agent steps: {settings.runtime.agent_max_steps} · Calc len ≤{' '}
                  {settings.runtime.ast_and_schema_guards.calculator_max_expression_length} · Python len ≤{' '}
                  {settings.runtime.ast_and_schema_guards.python_exec_max_code_length}
                </div>
                <div className="text-[11px] text-slate-400">
                  Safe Python builtins:{' '}
                  {settings.runtime.ast_and_schema_guards.python_exec_allowed_calls.join(', ')} · Ticket regex:{' '}
                  <code>{settings.runtime.ast_and_schema_guards.mock_api_ticket_pattern}</code>
                </div>
              </div>
            </div>
          </div>

          {/* Providers (Masked Flags Only) */}
          <div className="rounded-xl border border-borderSubtle bg-panel p-5 space-y-3">
            <div className="flex items-center gap-2">
              <KeyRound className="h-4 w-4 text-amber-400" />
              <h3 className="text-sm font-semibold text-white">
                Provider Registry (Masked Availability Only)
              </h3>
            </div>
            <div className="space-y-2">
              {(settings.providers || []).map((p) => (
                <div
                  key={p.id}
                  className="flex items-center justify-between rounded-lg border border-borderSubtle bg-graphite px-3 py-2 text-xs"
                >
                  <div>
                    <div className="font-mono font-semibold text-slate-100">
                      {p.id}{' '}
                      <span className="font-normal text-slate-400">({p.model})</span>
                    </div>
                    <div className="font-mono text-[10px] text-slate-500">
                      {p.api_key_env_var || 'No API key required (offline deterministic)'}
                    </div>
                  </div>
                  <span
                    className={`rounded-full border px-2.5 py-0.5 font-mono text-[10px] font-semibold uppercase ${
                      p.api_key_configured
                        ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-300'
                        : 'border-slate-600 bg-slate-800 text-slate-400'
                    }`}
                  >
                    {p.api_key_configured ? 'READY' : 'KEY UNSET'}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Failure -> Recovery Strategy Map */}
          <div className="rounded-xl border border-borderSubtle bg-panel p-5 space-y-3">
            <div className="flex items-center gap-2">
              <Cpu className="h-4 w-4 text-cyan-400" />
              <h3 className="text-sm font-semibold text-white">
                Failure Category → Recovery Strategy Policy
              </h3>
            </div>
            <div className="space-y-1.5 font-mono text-[11px]">
              {Object.entries(settings.recovery.strategy_map || {}).map(([cat, strats]) => (
                <div
                  key={cat}
                  className="flex items-center justify-between gap-2 rounded border border-borderSubtle/70 bg-obsidian/70 px-3 py-1.5"
                >
                  <span className="text-slate-300">{cat}</span>
                  <div className="flex flex-wrap gap-1">
                    {strats.map((s) => (
                      <span
                        key={s}
                        className="rounded bg-violet-500/20 px-1.5 py-0.5 text-[10px] text-violet-200"
                      >
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
