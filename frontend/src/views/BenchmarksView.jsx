import React, { useState } from 'react'
import {
  BarChart3,
  CheckCircle2,
  Expand,
  Info,
  Layers,
  X,
} from 'lucide-react'
import { fmtMs, fmtNum, fmtPct, fmtPValue } from '../api'

const REGIMES = [
  { id: '0', label: '0% Faults' },
  { id: '20', label: '20% Faults' },
  { id: '40', label: '40% Faults' },
  { id: 'all', label: 'All Regimes' },
]

export default function BenchmarksView({ benchmark }) {
  const [regime, setRegime] = useState('40')
  const [zoomPlot, setZoomPlot] = useState(null)

  if (!benchmark) {
    return (
      <div className="rounded-xl border border-borderSubtle bg-panel p-8 text-center text-sm text-slate-400">
        Loading real benchmark artifacts from results/...
      </div>
    )
  }

  const metricsRows = benchmark.metrics_summary || []
  const stat = benchmark.statistical_validation
  const statConditions = stat?.conditions || []
  const pooled = stat?.pooled_across_all_rates
  const cv = stat?.five_fold_cross_fitted_thresholds
  const plots = benchmark.plots || []

  const filteredRows =
    regime === 'all'
      ? metricsRows
      : metricsRows.filter((r) => Number(r.failure_rate_percent) === Number(regime))

  const activeStatCond =
    regime === 'all'
      ? null
      : statConditions.find((c) => Number(c.failure_rate_percent) === Number(regime))

  return (
    <div className="space-y-6">
      {/* Header & Regime Switcher */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-borderSubtle bg-panel p-4">
        <div>
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-cyan-400" />
            <h3 className="text-sm font-semibold text-white">
              100-Task Benchmarks, Ablations &amp; Statistical Validation
            </h3>
          </div>
          <p className="mt-0.5 font-mono text-[11px] text-slate-400">
            Run: {benchmark.run_id} · Dataset v{benchmark.comparison_summary?.dataset_version} · Seed{' '}
            {benchmark.comparison_summary?.random_seed} · Wilson 95% CIs + Exact McNemar
          </p>
        </div>
        <div className="flex items-center gap-1.5 rounded-lg border border-borderSubtle bg-obsidian p-1">
          {REGIMES.map((r) => (
            <button
              key={r.id}
              type="button"
              onClick={() => setRegime(r.id)}
              className={`rounded-md px-3 py-1.5 font-mono text-xs font-semibold transition-colors ${
                regime === r.id
                  ? 'bg-cyan-500 text-obsidian'
                  : 'text-slate-400 hover:text-slate-100'
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {/* Selected Regime Statistical Spotlight */}
      {activeStatCond ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <div className="rounded-xl border border-cyan-500/30 bg-panel p-4 space-y-2">
            <div className="text-xs font-medium text-slate-400">
              Task Success Rate (Wilson 95% CI) @ {regime}%
            </div>
            <div className="space-y-2 pt-1 font-mono text-xs">
              <div className="flex items-center justify-between">
                <span className="text-cyan-300 font-semibold">TraceRecover</span>
                <span className="text-white font-bold">
                  {fmtPct(activeStatCond.tracerecover.task_success_rate, 1)}{' '}
                  <span className="text-[11px] text-slate-400 font-normal">
                    [{fmtNum(activeStatCond.tracerecover.wilson_95_ci.lower, 3)},{' '}
                    {fmtNum(activeStatCond.tracerecover.wilson_95_ci.upper, 3)}]
                  </span>
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-slate-400">Baseline</span>
                <span className="text-slate-300">
                  {fmtPct(activeStatCond.baseline.task_success_rate, 1)}{' '}
                  <span className="text-[11px] text-slate-500">
                    [{fmtNum(activeStatCond.baseline.wilson_95_ci.lower, 3)},{' '}
                    {fmtNum(activeStatCond.baseline.wilson_95_ci.upper, 3)}]
                  </span>
                </span>
              </div>
              <div className="border-t border-borderSubtle pt-2 flex items-center justify-between text-[11px]">
                <span className="text-slate-400">Paired McNemar Exact p</span>
                <span className="font-bold text-emerald-300">
                  p = {fmtPValue(activeStatCond.paired_mcnemar_test.exact_two_sided_p_value)}
                </span>
              </div>
              <div className="flex items-center justify-between text-[11px] text-slate-400">
                <span>Contingency (n11/n10/n01/n00)</span>
                <span className="text-slate-200">
                  {activeStatCond.paired_mcnemar_test.contingency_table.both_correct_n11}/
                  {activeStatCond.paired_mcnemar_test.contingency_table.baseline_only_n10}/
                  {activeStatCond.paired_mcnemar_test.contingency_table.tracerecover_only_n01}/
                  {activeStatCond.paired_mcnemar_test.contingency_table.both_wrong_n00}
                </span>
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-emerald-500/30 bg-panel p-4 space-y-2">
            <div className="text-xs font-medium text-slate-400">
              Selective Accuracy &amp; NLI Grounding @ {regime}%
            </div>
            <div className="space-y-2 pt-1 font-mono text-xs">
              <div className="flex justify-between">
                <span className="text-slate-400">TR Strict Selective Acc</span>
                <span className="font-bold text-emerald-300">
                  {activeStatCond.tracerecover.accuracy_and_hallucination.selective_accuracy_strict.correct}/
                  {activeStatCond.tracerecover.accuracy_and_hallucination.selective_accuracy_strict.denominator}{' '}
                  ({fmtPct(activeStatCond.tracerecover.accuracy_and_hallucination.selective_accuracy_strict.rate, 1)})
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">TR Gold / NLI Hallucination</span>
                <span className="font-bold text-emerald-300">
                  {fmtPct(activeStatCond.tracerecover.accuracy_and_hallucination.gold_fact_hallucination_rate.rate, 1)} /{' '}
                  {fmtPct(activeStatCond.tracerecover.accuracy_and_hallucination.nli_grounded_hallucination_rate.answer_level_rate, 1)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-400">BL Gold / NLI Hallucination</span>
                <span className="text-rose-300">
                  {activeStatCond.baseline.accuracy_and_hallucination.gold_fact_hallucination_rate.hallucinating_answers}/
                  {activeStatCond.baseline.accuracy_and_hallucination.gold_fact_hallucination_rate.denominator} (
                  {fmtPct(activeStatCond.baseline.accuracy_and_hallucination.gold_fact_hallucination_rate.rate, 1)}) /{' '}
                  {fmtPct(activeStatCond.baseline.accuracy_and_hallucination.nli_grounded_hallucination_rate.answer_level_rate, 1)}
                </span>
              </div>
              <div className="border-t border-borderSubtle pt-2 flex justify-between text-[11px]">
                <span className="text-slate-400">TR Brier Score / ECE</span>
                <span className="text-cyan-300">
                  {fmtNum(activeStatCond.tracerecover.brier_score.brier_score, 4)} /{' '}
                  {fmtNum(activeStatCond.tracerecover.ece.ece, 4)}
                </span>
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-violet-500/30 bg-panel p-4 space-y-2">
            <div className="text-xs font-medium text-slate-400">
              Exact Decision Distribution @ {regime}%
            </div>
            <div className="grid grid-cols-3 gap-2 pt-1 text-center font-mono">
              <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-2.5">
                <div className="text-[10px] text-emerald-300">ANSWER</div>
                <div className="mt-1 text-lg font-bold text-white">
                  {activeStatCond.tracerecover.decisions.counts.ANSWER}
                </div>
              </div>
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2.5">
                <div className="text-[10px] text-amber-300">WARN</div>
                <div className="mt-1 text-lg font-bold text-white">
                  {activeStatCond.tracerecover.decisions.counts.WARN}
                </div>
              </div>
              <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-2.5">
                <div className="text-[10px] text-rose-300">ABSTAIN</div>
                <div className="mt-1 text-lg font-bold text-white">
                  {activeStatCond.tracerecover.decisions.counts.ABSTAIN}
                </div>
              </div>
            </div>
            <div className="pt-1 text-[11px] font-mono text-slate-400 flex justify-between">
              <span>Baseline Decisions:</span>
              <span className="text-slate-300">100 NOT_APPLICABLE (0 abstentions)</span>
            </div>
          </div>
        </div>
      ) : pooled ? (
        <div className="rounded-xl border border-cyan-500/30 bg-panel p-4 flex flex-wrap items-center justify-between gap-4 font-mono text-xs">
          <div>
            <span className="text-slate-400">Pooled (n={pooled.total_paired_runs}): </span>
            <span className="text-cyan-300 font-bold">
              TraceRecover {fmtPct(pooled.tracerecover_task_success_rate, 1)}
            </span>{' '}
            vs{' '}
            <span className="text-slate-300">
              Baseline {fmtPct(pooled.baseline_task_success_rate, 1)}
            </span>
          </div>
          <div className="text-emerald-300 font-bold">
            Paired McNemar p = {fmtPValue(pooled.paired_mcnemar_test.exact_two_sided_p_value)} (n10=0, n01=
            {pooled.paired_mcnemar_test.contingency_table.tracerecover_only_n01})
          </div>
          <div className="text-slate-300">
            Brier={fmtNum(pooled.tracerecover_brier_score.brier_score, 4)} · ECE=
            {fmtNum(pooled.tracerecover_ece.ece, 4)}
          </div>
        </div>
      ) : null}

      {/* Full Comparison & Ablation Table */}
      <div className="rounded-xl border border-borderSubtle bg-panel overflow-hidden">
        <div className="flex items-center justify-between border-b border-borderSubtle px-4 py-3">
          <div className="flex items-center gap-2">
            <Layers className="h-4 w-4 text-cyan-400" />
            <h4 className="text-sm font-semibold text-white">
              Systems &amp; Ablations Comparison Table (metrics_summary.csv)
            </h4>
          </div>
          <span className="font-mono text-[11px] text-slate-400">
            {filteredRows.length} condition rows
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-borderSubtle bg-graphite/80 text-[10px] uppercase tracking-wider text-slate-400">
                <th className="px-3 py-2.5 font-medium">System</th>
                <th className="px-3 py-2.5 font-medium">Fault %</th>
                <th className="px-3 py-2.5 font-medium">Task Success (95% CI)</th>
                <th className="px-3 py-2.5 font-medium">Recovery Rate</th>
                <th className="px-3 py-2.5 font-medium">Hallucination</th>
                <th className="px-3 py-2.5 font-medium">Unsup Claim %</th>
                <th className="px-3 py-2.5 font-medium">Evid Cov</th>
                <th className="px-3 py-2.5 font-medium">Abst P / R</th>
                <th className="px-3 py-2.5 font-medium">ECE</th>
                <th className="px-3 py-2.5 font-medium text-right">Avg Latency</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-borderSubtle/60 font-mono text-[11px] tabular-nums">
              {filteredRows.map((row, idx) => {
                const isTR = row.system === 'tracerecover'
                return (
                  <tr
                    key={`${row.system}-${row.failure_rate_percent}-${idx}`}
                    className={isTR ? 'bg-cyan-500/10' : 'hover:bg-panelHover/60'}
                  >
                    <td className={`px-3 py-2.5 font-semibold ${isTR ? 'text-cyan-300' : 'text-slate-200'}`}>
                      {row.system}
                    </td>
                    <td className="px-3 py-2.5 text-slate-300">{row.failure_rate_percent}%</td>
                    <td className="px-3 py-2.5">
                      <span className={isTR ? 'font-bold text-white' : 'text-slate-200'}>
                        {fmtPct(row.task_success_rate, 1)}
                      </span>{' '}
                      <span className="text-[10px] text-slate-500">
                        [{fmtNum(row.task_success_ci95_lower, 2)}, {fmtNum(row.task_success_ci95_upper, 2)}]
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-violet-300">
                      {row.detected_failures > 0
                        ? `${row.successful_recoveries}/${row.detected_failures} (${fmtPct(row.recovery_rate, 1)})`
                        : '—'}
                    </td>
                    <td className={`px-3 py-2.5 ${Number(row.hallucination_rate) === 0 ? 'text-emerald-300 font-bold' : 'text-rose-300'}`}>
                      {row.hallucinating_answers}/{row.answers_with_factual_claims} ({fmtPct(row.hallucination_rate, 1)})
                    </td>
                    <td className="px-3 py-2.5 text-slate-300">
                      {fmtPct(row.unsupported_claim_rate, 1)}
                    </td>
                    <td className="px-3 py-2.5 text-slate-300">
                      {fmtPct(row.evidence_coverage, 1)}
                    </td>
                    <td className="px-3 py-2.5 text-slate-300">
                      {fmtPct(row.abstention_precision, 0)} / {fmtPct(row.abstention_recall, 0)}
                    </td>
                    <td className="px-3 py-2.5 text-slate-300">
                      {fmtNum(row.ece, 3)}
                    </td>
                    <td className="px-3 py-2.5 text-right text-slate-400">
                      {fmtMs(row.average_latency_seconds)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <div className="border-t border-borderSubtle bg-obsidian/70 px-4 py-2.5 flex items-start gap-2 text-[11px] text-slate-400">
          <Info className="h-3.5 w-3.5 mt-0.5 shrink-0 text-cyan-400" />
          <span>
            <strong className="text-slate-300">/99 Denominator Audit:</strong> On Baseline across 0/20/40%, task{' '}
            <code className="text-cyan-300">MEA-20</code> (empty ticket <code className="text-cyan-300">T-419</code>) emits raw{' '}
            <code>&#123;&#125;</code> with 0 alphanumeric claims (excluded from claim-bearing hallucination denominator, still scored{' '}
            <code>success=False</code>). On TraceRecover @ 20%, 1 non-abstained task (<code className="text-cyan-300">NCT-16</code>) omitted sensor labels after REPLAN (Selective Acc 59/60 = 98.3%, 0% hallucination).
          </span>
        </div>
      </div>

      {/* 5-Fold Cross-Fitted Threshold Validation */}
      {cv && (
        <div className="rounded-xl border border-borderSubtle bg-panel p-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4 text-emerald-400" />
              <h4 className="text-sm font-semibold text-white">
                5-Fold Stratified Group-by-Task_ID Cross-Fitting (Zero Threshold Leakage)
              </h4>
            </div>
            <span className="font-mono text-[11px] text-emerald-300">
              Fitted: answer={fmtNum(cv.fitted_threshold_summary.answer_threshold_mean, 4)} ±{' '}
              {fmtNum(cv.fitted_threshold_summary.answer_threshold_stdev, 4)} | abstain=
              {fmtNum(cv.fitted_threshold_summary.abstain_threshold_mean, 4)} ±{' '}
              {fmtNum(cv.fitted_threshold_summary.abstain_threshold_stdev, 4)}
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left font-mono text-[11px] tabular-nums">
              <thead>
                <tr className="border-b border-borderSubtle text-[10px] uppercase text-slate-400">
                  <th className="py-2 pr-3">Fold</th>
                  <th className="py-2 pr-3">Train / Held-Out</th>
                  <th className="py-2 pr-3">Fitted (Ans / Abs)</th>
                  <th className="py-2 pr-3">Held-Out Success</th>
                  <th className="py-2 pr-3">Held-Out SelAcc</th>
                  <th className="py-2 pr-3">Abst Prec / Rec / F1</th>
                  <th className="py-2 text-right">Ans / Warn / Abst</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-borderSubtle/60">
                {(cv.folds || []).map((f) => (
                  <tr key={f.fold}>
                    <td className="py-2 pr-3 text-cyan-300">Fold {f.fold}</td>
                    <td className="py-2 pr-3 text-slate-400">
                      {f.train_run_count} / {f.test_run_count}
                    </td>
                    <td className="py-2 pr-3 text-slate-200">
                      {fmtNum(f.fitted_answer_threshold, 4)} / {fmtNum(f.fitted_abstain_threshold, 4)}
                    </td>
                    <td className="py-2 pr-3 text-slate-100">
                      {fmtPct(f.held_out_metrics.task_success_rate, 1)}
                    </td>
                    <td className="py-2 pr-3 text-emerald-300">
                      {fmtPct(f.held_out_metrics.selective_accuracy_strict, 1)}
                    </td>
                    <td className="py-2 pr-3 text-slate-300">
                      {fmtNum(f.held_out_metrics.abstain_precision, 3)} /{' '}
                      {fmtNum(f.held_out_metrics.abstain_recall, 3)} /{' '}
                      {fmtNum(f.held_out_metrics.abstain_f1, 3)}
                    </td>
                    <td className="py-2 text-right text-slate-300">
                      {f.held_out_metrics.answer_count} / {f.held_out_metrics.warn_count} /{' '}
                      {f.held_out_metrics.abstain_count}
                    </td>
                  </tr>
                ))}
                <tr className="bg-emerald-500/10 font-bold text-white">
                  <td className="py-2 pr-3">OOF Aggregate (n=300)</td>
                  <td className="py-2 pr-3">100% Held-Out</td>
                  <td className="py-2 pr-3">Cross-Fitted</td>
                  <td className="py-2 pr-3">{fmtPct(cv.out_of_fold_aggregate.task_success_rate, 1)}</td>
                  <td className="py-2 pr-3 text-emerald-300">
                    {fmtPct(cv.out_of_fold_aggregate.selective_accuracy_strict, 1)}
                  </td>
                  <td className="py-2 pr-3">
                    {fmtNum(cv.out_of_fold_aggregate.abstain_precision, 3)} /{' '}
                    {fmtNum(cv.out_of_fold_aggregate.abstain_recall, 3)} /{' '}
                    {fmtNum(cv.out_of_fold_aggregate.abstain_f1, 3)}
                  </td>
                  <td className="py-2 text-right">
                    {cv.out_of_fold_aggregate.answer_count} / {cv.out_of_fold_aggregate.warn_count} /{' '}
                    {cv.out_of_fold_aggregate.abstain_count}
                  </td>
                </tr>
                <tr className="bg-graphite/80 text-slate-300">
                  <td className="py-2 pr-3">Default Policy (0.15 / 0.55)</td>
                  <td className="py-2 pr-3">Fixed Prior</td>
                  <td className="py-2 pr-3">0.1500 / 0.5500</td>
                  <td className="py-2 pr-3">
                    {fmtPct(cv.default_threshold_baseline_0_15_0_55.task_success_rate, 1)}
                  </td>
                  <td className="py-2 pr-3 text-emerald-300">
                    {fmtPct(cv.default_threshold_baseline_0_15_0_55.selective_accuracy_strict, 1)}
                  </td>
                  <td className="py-2 pr-3">
                    {fmtNum(cv.default_threshold_baseline_0_15_0_55.abstain_precision, 3)} /{' '}
                    {fmtNum(cv.default_threshold_baseline_0_15_0_55.abstain_recall, 3)} /{' '}
                    {fmtNum(cv.default_threshold_baseline_0_15_0_55.abstain_f1, 3)}
                  </td>
                  <td className="py-2 text-right">
                    {cv.default_threshold_baseline_0_15_0_55.answer_count} /{' '}
                    {cv.default_threshold_baseline_0_15_0_55.warn_count} /{' '}
                    {cv.default_threshold_baseline_0_15_0_55.abstain_count}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* All 6 Real Benchmark SVG Plots */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-semibold text-white">
            Publication-Ready Benchmark Plots ({plots.length} SVGs)
          </h4>
          <span className="text-xs text-slate-400">
            Served live from /api/plots/ · click any chart to expand
          </span>
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {plots.map((plot) => (
            <div
              key={plot.filename}
              onClick={() => setZoomPlot(plot)}
              className="group cursor-pointer overflow-hidden rounded-xl border border-borderSubtle bg-panel transition-all hover:border-cyan-500/50"
            >
              <div className="flex items-center justify-between border-b border-borderSubtle px-3.5 py-2.5">
                <span className="text-xs font-medium text-slate-200 truncate">{plot.title}</span>
                <Expand className="h-3.5 w-3.5 text-slate-400 group-hover:text-cyan-300 shrink-0" />
              </div>
              <div className="bg-slate-100 p-2">
                <img
                  src={plot.url}
                  alt={plot.title}
                  className="h-52 w-full object-contain"
                  loading="lazy"
                />
              </div>
              <div className="px-3.5 py-1.5 font-mono text-[10px] text-slate-500">
                {plot.filename}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Plot Lightbox Modal */}
      {zoomPlot && (
        <div
          onClick={() => setZoomPlot(null)}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-6 backdrop-blur-sm"
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="max-w-5xl w-full rounded-2xl border border-borderSubtle bg-panel overflow-hidden shadow-2xl"
          >
            <div className="flex items-center justify-between border-b border-borderSubtle px-5 py-3.5">
              <div>
                <h4 className="text-sm font-semibold text-white">{zoomPlot.title}</h4>
                <div className="font-mono text-[11px] text-slate-400">{zoomPlot.url}</div>
              </div>
              <button
                type="button"
                onClick={() => setZoomPlot(null)}
                className="rounded-lg border border-borderSubtle p-1.5 text-slate-300 hover:bg-panelHover"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="bg-white p-4">
              <img
                src={zoomPlot.url}
                alt={zoomPlot.title}
                className="max-h-[75vh] w-full object-contain"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
