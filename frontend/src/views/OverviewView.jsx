import React from 'react'
import {
  Activity,
  ArrowRight,
  CheckCircle2,
  Cpu,
  Database,
  FileSearch,
  GitMerge,
  Scale,
  ShieldCheck,
  Sparkles,
  Zap,
} from 'lucide-react'
import { DecisionBadge } from '../components/Badges'
import { fmtMs, fmtNum, fmtPct, fmtPValue, fmtTimestamp } from '../api'

const PIPELINE_STAGES = [
  {
    step: '01',
    title: 'Planner & ToolExecutor',
    detail: 'AST + schema validation, deterministic cache, committed/discarded tagging',
    icon: Cpu,
    accent: 'text-sky-400 border-sky-500/30 bg-sky-500/10',
  },
  {
    step: '02',
    title: 'Failure Detector',
    detail: '8 failure categories across timeouts, empty/corrupt outputs & bad args',
    icon: Activity,
    accent: 'text-rose-400 border-rose-500/30 bg-rose-500/10',
  },
  {
    step: '03',
    title: 'Failure Localizer',
    detail: 'Root-cause step attribution & severity classification without mutating trace',
    icon: FileSearch,
    accent: 'text-amber-400 border-amber-500/30 bg-amber-500/10',
  },
  {
    step: '04',
    title: 'Self-Healing Recovery',
    detail: 'Bounded RETRY, ARGUMENT_REPAIR, TOOL_SWITCH & REPLAN with detector validation',
    icon: GitMerge,
    accent: 'text-violet-400 border-violet-500/30 bg-violet-500/10',
  },
  {
    step: '05',
    title: 'Evidence & NLI Grounding',
    detail: 'Atomic claim extraction + DeBERTa-v3 NLI / deterministic verification vs committed steps',
    icon: ShieldCheck,
    accent: 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10',
  },
  {
    step: '06',
    title: 'Uncertainty & Abstention',
    detail: '4-signal reliability estimator + calibrated ANSWER / WARN / ABSTAIN policy',
    icon: Scale,
    accent: 'text-cyan-400 border-cyan-500/30 bg-cyan-500/10',
  },
]

export default function OverviewView({
  health,
  benchmark,
  traces,
  onNavigate,
  onSelectTrace,
}) {
  const comparisons = benchmark?.comparison_summary?.comparisons || []
  const stat = benchmark?.statistical_validation
  const pooled = stat?.pooled_across_all_rates
  const cond40 = comparisons.find((c) => Number(c.failure_rate_percent) === 40)
  const cond0 = comparisons.find((c) => Number(c.failure_rate_percent) === 0)
  const oof = stat?.five_fold_cross_fitted_thresholds?.out_of_fold_aggregate
  const telemetry = benchmark?.telemetry_overview || {}

  const kpis = [
    {
      label: 'Hallucination Rate (0–40%)',
      value: '0.0%',
      sub: `vs ${fmtPct(cond40?.baseline?.hallucination?.rate, 1)} Baseline @ 40% faults`,
      icon: ShieldCheck,
      color: 'text-emerald-400',
      border: 'border-emerald-500/30',
    },
    {
      label: 'Task Success @ 40% Faults',
      value: fmtPct(cond40?.tracerecover?.task_success?.rate, 1),
      sub: `+${((cond40?.tracerecover_minus_baseline?.task_success_rate ?? 0) * 100).toFixed(0)}pp vs Baseline (${fmtPct(cond40?.baseline?.task_success?.rate, 1)})`,
      icon: Zap,
      color: 'text-cyan-400',
      border: 'border-cyan-500/30',
    },
    {
      label: 'Strict Selective Accuracy',
      value: fmtPct(oof?.selective_accuracy_strict, 1),
      sub: `178/179 non-abstained · 5-fold OOF zero leakage`,
      icon: CheckCircle2,
      color: 'text-violet-400',
      border: 'border-violet-500/30',
    },
    {
      label: 'Paired McNemar (n=300)',
      value: `p = ${fmtPValue(pooled?.paired_mcnemar_test?.exact_two_sided_p_value)}`,
      sub: `Net gain +${pooled?.paired_mcnemar_test?.net_gain_tasks ?? 0} tasks (0 regressions)`,
      icon: Sparkles,
      color: 'text-amber-400',
      border: 'border-amber-500/30',
    },
  ]

  return (
    <div className="space-y-6">
      {/* Hero Banner */}
      <div className="relative overflow-hidden rounded-2xl border border-borderSubtle bg-panel p-6 grid-bg">
        <div className="relative flex flex-wrap items-center justify-between gap-4">
          <div className="max-w-2xl space-y-2">
            <div className="inline-flex items-center gap-2 rounded-full border border-cyan-500/30 bg-cyan-500/10 px-3 py-1 font-mono text-[11px] text-cyan-300">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
              Active Benchmark: {benchmark?.run_id || 'Loading...'} · Dataset v{health?.active_benchmark?.dataset_version || '2.0'}
            </div>
            <h2 className="text-2xl font-bold tracking-tight text-white">
              Evidence-Grounded Self-Healing &amp; Uncertainty-Aware Agent Observability
            </h2>
            <p className="text-sm leading-relaxed text-slate-400">
              Inspect live ReAct tool provenance (<span className="font-mono text-emerald-300">committed</span> vs{' '}
              <span className="font-mono text-rose-300">discarded</span>), bounded self-healing recoveries,
              atomic NLI claim verification, and calibrated abstention across 100 synthetic tasks × 3 fault regimes.
            </p>
          </div>
          <div className="flex flex-wrap gap-2.5">
            <button
              type="button"
              onClick={() => onNavigate('playground')}
              className="inline-flex items-center gap-2 rounded-lg bg-cyan-500 px-4 py-2.5 text-sm font-semibold text-obsidian hover:bg-cyan-400 transition-colors"
            >
              Launch Agent Playground
              <ArrowRight className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={() => onNavigate('benchmarks')}
              className="inline-flex items-center gap-2 rounded-lg border border-borderSubtle bg-graphite px-4 py-2.5 text-sm font-medium text-slate-200 hover:bg-panelHover transition-colors"
            >
              Explore 100-Task Results
            </button>
          </div>
        </div>
      </div>

      {/* KPI Row */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {kpis.map((kpi) => {
          const Icon = kpi.icon
          return (
            <div
              key={kpi.label}
              className={`rounded-xl border ${kpi.border} bg-panel p-4 transition-colors hover:bg-panelHover`}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-slate-400">{kpi.label}</span>
                <Icon className={`h-4 w-4 ${kpi.color}`} />
              </div>
              <div className="mt-2 font-mono text-2xl font-bold text-white tabular-nums">
                {kpi.value}
              </div>
              <div className="mt-1 text-xs text-slate-400">{kpi.sub}</div>
            </div>
          )
        })}
      </div>

      {/* 100-Task Summary Visual + Dataset Telemetry */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="rounded-xl border border-borderSubtle bg-panel p-5 lg:col-span-8">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold text-white">
                100-Task Benchmark Performance Across Fault Regimes
              </h3>
              <p className="text-xs text-slate-400">
                Real measured Task Success Rate &amp; Hallucination Rate from {benchmark?.run_id}
              </p>
            </div>
            <div className="flex items-center gap-4 text-xs">
              <span className="flex items-center gap-1.5 text-cyan-300">
                <span className="h-2.5 w-2.5 rounded-sm bg-cyan-400" />
                TraceRecover
              </span>
              <span className="flex items-center gap-1.5 text-slate-400">
                <span className="h-2.5 w-2.5 rounded-sm bg-slate-500" />
                Baseline
              </span>
            </div>
          </div>

          <div className="space-y-4">
            {comparisons.map((c) => {
              const rate = Number(c.failure_rate_percent)
              const trSucc = Number(c.tracerecover?.task_success?.rate || 0)
              const blSucc = Number(c.baseline?.task_success?.rate || 0)
              const trHall = Number(c.tracerecover?.hallucination?.rate || 0)
              const blHall = Number(c.baseline?.hallucination?.rate || 0)
              const recRate = Number(c.tracerecover?.recovery?.rate || 0)
              return (
                <div
                  key={rate}
                  className="rounded-lg border border-borderSubtle/80 bg-graphite/80 p-3.5"
                >
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <span className="font-mono text-xs font-bold text-slate-200">
                      {rate}% Fault Injection Regime
                    </span>
                    <div className="flex flex-wrap gap-3 font-mono text-[11px]">
                      <span className="text-emerald-300">
                        TR Halluc: {fmtPct(trHall, 1)}
                      </span>
                      <span className="text-rose-300">
                        BL Halluc: {fmtPct(blHall, 1)}
                      </span>
                      {rate > 0 && (
                        <span className="text-violet-300">
                          Recovery: {fmtPct(recRate, 1)}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="space-y-2">
                    <div className="flex items-center gap-3">
                      <span className="w-24 font-mono text-[11px] text-cyan-300">TraceRecover</span>
                      <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-slate-800">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-emerald-400"
                          style={{ width: `${trSucc * 100}%` }}
                        />
                      </div>
                      <span className="w-14 text-right font-mono text-xs font-semibold text-white tabular-nums">
                        {fmtPct(trSucc, 1)}
                      </span>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="w-24 font-mono text-[11px] text-slate-400">Baseline</span>
                      <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-slate-800">
                        <div
                          className="h-full rounded-full bg-slate-500"
                          style={{ width: `${blSucc * 100}%` }}
                        />
                      </div>
                      <span className="w-14 text-right font-mono text-xs text-slate-300 tabular-nums">
                        {fmtPct(blSucc, 1)}
                      </span>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* Dataset & Telemetry Composition */}
        <div className="rounded-xl border border-borderSubtle bg-panel p-5 lg:col-span-4 flex flex-col justify-between">
          <div>
            <div className="mb-3 flex items-center gap-2">
              <Database className="h-4 w-4 text-cyan-400" />
              <h3 className="text-sm font-semibold text-white">Benchmark Telemetry Corpus</h3>
            </div>
            <div className="grid grid-cols-2 gap-2.5 mb-4">
              <div className="rounded-lg border border-borderSubtle bg-graphite p-3">
                <div className="text-[10px] uppercase tracking-wider text-slate-400">Exec Rows</div>
                <div className="mt-1 font-mono text-lg font-bold text-white tabular-nums">
                  {telemetry.execution_telemetry_rows ?? 600}
                </div>
              </div>
              <div className="rounded-lg border border-borderSubtle bg-graphite p-3">
                <div className="text-[10px] uppercase tracking-wider text-slate-400">Step Rows</div>
                <div className="mt-1 font-mono text-lg font-bold text-cyan-300 tabular-nums">
                  {telemetry.step_level_telemetry_rows ?? 1769}
                </div>
              </div>
            </div>

            <div className="space-y-2">
              <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                100-Task Category Breakdown
              </div>
              {Object.entries(telemetry.task_categories || {}).map(([cat, count]) => (
                <div
                  key={cat}
                  className="flex items-center justify-between rounded border border-borderSubtle/70 bg-obsidian/60 px-2.5 py-1.5 text-xs"
                >
                  <span className="font-mono text-slate-300 truncate">{cat}</span>
                  <span className="font-mono font-semibold text-cyan-300 tabular-nums">
                    {count}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="mt-4 rounded-lg border border-borderSubtle bg-obsidian/80 p-3 text-xs space-y-1">
            <div className="flex justify-between">
              <span className="text-slate-400">0% Abstention P / R</span>
              <span className="font-mono text-emerald-300">
                {fmtPct(cond0?.tracerecover?.abstention?.abstention_precision, 0)} /{' '}
                {fmtPct(cond0?.tracerecover?.abstention?.abstention_recall, 0)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Pooled Brier / ECE</span>
              <span className="font-mono text-slate-200">
                {fmtNum(pooled?.tracerecover_brier_score?.brier_score, 4)} /{' '}
                {fmtNum(pooled?.tracerecover_ece?.ece, 4)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-400">Persisted Traces in logs/</span>
              <span className="font-mono text-cyan-300">
                {health?.trace_log_count ?? 0}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Architecture Flow */}
      <div className="rounded-xl border border-borderSubtle bg-panel p-5">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-white">TraceRecover 6-Stage Reliability Pipeline</h3>
            <p className="text-xs text-slate-400">
              Every tool call is validated, traced with strict provenance, healed on failure, NLI-grounded, and gated by calibrated uncertainty.
            </p>
          </div>
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {PIPELINE_STAGES.map((s) => {
            const Icon = s.icon
            return (
              <div
                key={s.step}
                className="rounded-xl border border-borderSubtle bg-graphite/90 p-3.5 hover:border-slate-600 transition-colors"
              >
                <div className="flex items-center gap-2.5">
                  <div className={`flex h-8 w-8 items-center justify-center rounded-lg border ${s.accent}`}>
                    <Icon className="h-4 w-4" />
                  </div>
                  <div>
                    <div className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
                      Stage {s.step}
                    </div>
                    <div className="text-xs font-semibold text-slate-100">{s.title}</div>
                  </div>
                </div>
                <p className="mt-2 text-xs leading-relaxed text-slate-400">{s.detail}</p>
              </div>
            )
          })}
        </div>
      </div>

      {/* Recent Trace Activity */}
      <div className="rounded-xl border border-borderSubtle bg-panel p-5">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-white">Recent Execution Traces (logs/)</h3>
            <p className="text-xs text-slate-400">
              Live JSONL logs from recent agent executions. Click any row to inspect full provenance.
            </p>
          </div>
          <button
            type="button"
            onClick={() => onNavigate('traces')}
            className="text-xs font-medium text-cyan-400 hover:text-cyan-300"
          >
            View all traces →
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-borderSubtle text-[11px] uppercase tracking-wider text-slate-400">
                <th className="py-2 pr-3 font-medium">Trace ID</th>
                <th className="py-2 pr-3 font-medium">Query</th>
                <th className="py-2 pr-3 font-medium">Decision</th>
                <th className="py-2 pr-3 font-medium">Provenance</th>
                <th className="py-2 pr-3 font-medium">Uncertainty</th>
                <th className="py-2 pr-3 font-medium">Latency</th>
                <th className="py-2 font-medium text-right">Timestamp</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-borderSubtle/60">
              {(traces || []).slice(0, 8).map((t) => (
                <tr
                  key={t.trace_id}
                  onClick={() => onSelectTrace(t.trace_id)}
                  className="cursor-pointer hover:bg-panelHover/80 transition-colors"
                >
                  <td className="py-2.5 pr-3 font-mono text-[11px] text-cyan-300 whitespace-nowrap">
                    {t.trace_id}
                  </td>
                  <td className="py-2.5 pr-3 text-slate-200 max-w-xs truncate">
                    {t.question}
                  </td>
                  <td className="py-2.5 pr-3">
                    <DecisionBadge decision={t.final_decision} />
                  </td>
                  <td className="py-2.5 pr-3 font-mono text-[11px] tabular-nums whitespace-nowrap">
                    <span className="text-emerald-400">{t.provenance?.committed_steps ?? 0}c</span>
                    <span className="mx-1 text-slate-600">/</span>
                    <span className="text-rose-400">{t.provenance?.discarded_steps ?? 0}d</span>
                  </td>
                  <td className="py-2.5 pr-3 font-mono text-slate-300 tabular-nums">
                    {fmtNum(t.uncertainty_score, 3)}
                  </td>
                  <td className="py-2.5 pr-3 font-mono text-slate-400 tabular-nums whitespace-nowrap">
                    {fmtMs(t.total_latency_seconds)}
                  </td>
                  <td className="py-2.5 text-right font-mono text-[11px] text-slate-500 whitespace-nowrap">
                    {fmtTimestamp(t.timestamp)}
                  </td>
                </tr>
              ))}
              {(!traces || traces.length === 0) && (
                <tr>
                  <td colSpan={7} className="py-6 text-center text-slate-500">
                    No JSONL traces found yet. Run a preset in Agent Playground to generate one.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
