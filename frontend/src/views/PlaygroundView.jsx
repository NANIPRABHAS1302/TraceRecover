import React, { useState } from 'react'
import {
  Play,
  RotateCcw,
  SlidersHorizontal,
  Sparkles,
  Terminal,
  Zap,
} from 'lucide-react'
import { api } from '../api'
import { RunInspectionPanel } from '../components/Badges'

const PRESET_TONES = {
  emerald: 'border-emerald-500/35 hover:border-emerald-400/70 bg-emerald-500/5',
  cyan: 'border-cyan-500/35 hover:border-cyan-400/70 bg-cyan-500/5',
  violet: 'border-violet-500/35 hover:border-violet-400/70 bg-violet-500/5',
  amber: 'border-amber-500/35 hover:border-amber-400/70 bg-amber-500/5',
  rose: 'border-rose-500/35 hover:border-rose-400/70 bg-rose-500/5',
  slate: 'border-slate-600/60 hover:border-slate-400/70 bg-slate-500/5',
}

const BADGE_TONES = {
  emerald: 'bg-emerald-500/20 text-emerald-300',
  cyan: 'bg-cyan-500/20 text-cyan-300',
  violet: 'bg-violet-500/20 text-violet-300',
  amber: 'bg-amber-500/20 text-amber-300',
  rose: 'bg-rose-500/20 text-rose-300',
  slate: 'bg-slate-600/40 text-slate-200',
}

const DEFAULT_FORM = {
  question: 'What is the capital of France?',
  provider: 'local',
  failure_rate: 0,
  seed: 42,
  use_nli: true,
  enable_recovery: true,
  enable_grounding: true,
  enable_uncertainty: true,
  enable_localization: true,
  answer_threshold: 0.15,
  abstain_threshold: 0.55,
  scenario_mode: 'standard',
}

export default function PlaygroundView({
  presets,
  providers,
  lastRun,
  onRunComplete,
  onInspectEvidence,
}) {
  const [form, setForm] = useState(DEFAULT_FORM)
  const [activePresetId, setActivePresetId] = useState('normal_answer')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState(null)

  const executeRun = async (params) => {
    setRunning(true)
    setError(null)
    try {
      const result = await api.runAgent(params)
      onRunComplete(result)
    } catch (err) {
      setError(err.message || 'Execution failed.')
    } finally {
      setRunning(false)
    }
  }

  const handleSelectPreset = (preset) => {
    const nextParams = { ...DEFAULT_FORM, ...preset.params }
    setActivePresetId(preset.id)
    setForm(nextParams)
    executeRun(nextParams)
  }

  const handleSubmit = (e) => {
    e.preventDefault()
    executeRun(form)
  }

  const updateField = (key, value) => {
    setActivePresetId(null)
    setForm((prev) => {
      const next = { ...prev, [key]: value }
      if (key === 'enable_recovery' && value) {
        next.enable_localization = true
      }
      return next
    })
  }

  return (
    <div className="space-y-6">
      {/* 6 One-Click Preset Scenarios */}
      <div className="rounded-xl border border-borderSubtle bg-panel p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-cyan-400" />
            <h3 className="text-sm font-semibold text-white">
              One-Click Live Demo Presets
            </h3>
          </div>
          <span className="text-xs text-slate-400">
            Click any card to run through the real Python ReactAgent engine
          </span>
        </div>

        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-3">
          {(presets || []).map((preset) => {
            const isActive = activePresetId === preset.id
            return (
              <button
                key={preset.id}
                type="button"
                disabled={running}
                onClick={() => handleSelectPreset(preset)}
                className={`group text-left rounded-xl border p-3.5 transition-all ${
                  PRESET_TONES[preset.tone] || PRESET_TONES.slate
                } ${isActive ? 'ring-1 ring-cyan-400' : ''} disabled:opacity-60`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-white group-hover:text-cyan-200">
                    {preset.title}
                  </span>
                  <span
                    className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-bold uppercase ${
                      BADGE_TONES[preset.tone] || BADGE_TONES.slate
                    }`}
                  >
                    {preset.badge}
                  </span>
                </div>
                <div className="mt-0.5 text-[11px] font-medium text-slate-300">
                  {preset.subtitle}
                </div>
                <p className="mt-1.5 text-[11px] leading-relaxed text-slate-400 line-clamp-2">
                  {preset.description}
                </p>
              </button>
            )
          })}
        </div>
      </div>

      {/* Interactive Query & Parameter Form */}
      <form
        onSubmit={handleSubmit}
        className="rounded-xl border border-borderSubtle bg-panel p-5 space-y-4"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Terminal className="h-4 w-4 text-cyan-400" />
            <h3 className="text-sm font-semibold text-white">Live Agent Runner</h3>
          </div>
          <button
            type="button"
            onClick={() => {
              setForm(DEFAULT_FORM)
              setActivePresetId('normal_answer')
            }}
            className="inline-flex items-center gap-1.5 rounded-md border border-borderSubtle px-2.5 py-1 text-xs text-slate-400 hover:bg-panelHover hover:text-slate-200"
          >
            <RotateCcw className="h-3 w-3" />
            Reset Defaults
          </button>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
          <div className="lg:col-span-8 space-y-1.5">
            <label className="text-xs font-medium text-slate-300">
              Agent Question / Prompt
            </label>
            <textarea
              rows={2}
              value={form.question}
              onChange={(e) => updateField('question', e.target.value)}
              placeholder="e.g., What is 19 * 23 + 4? or What is the status and owner of ticket T-401?"
              className="w-full rounded-lg border border-borderSubtle bg-obsidian px-3 py-2 font-mono text-xs text-slate-100 placeholder-slate-500 focus:border-cyan-400 focus:outline-none"
            />
          </div>

          <div className="lg:col-span-4 grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-slate-300">LLM Provider</label>
              <select
                value={form.provider}
                onChange={(e) => updateField('provider', e.target.value)}
                className="w-full rounded-lg border border-borderSubtle bg-obsidian px-2.5 py-2 font-mono text-xs text-slate-100 focus:border-cyan-400 focus:outline-none"
              >
                {(providers || [{ id: 'local', api_key_configured: true }]).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.id} {p.api_key_configured ? '✓' : '(no key)'}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-slate-300">RNG Seed</label>
              <input
                type="number"
                value={form.seed}
                onChange={(e) => updateField('seed', Number(e.target.value))}
                className="w-full rounded-lg border border-borderSubtle bg-obsidian px-2.5 py-2 font-mono text-xs text-slate-100 focus:border-cyan-400 focus:outline-none"
              />
            </div>
            <div className="col-span-2 space-y-1">
              <div className="flex justify-between text-xs">
                <label className="font-medium text-slate-300">Fault Injection Rate</label>
                <span className="font-mono font-bold text-cyan-300 tabular-nums">
                  {form.failure_rate}%
                </span>
              </div>
              <input
                type="range"
                min={0}
                max={100}
                step={5}
                value={form.failure_rate}
                onChange={(e) => updateField('failure_rate', Number(e.target.value))}
                className="w-full accent-cyan-400"
              />
            </div>
          </div>
        </div>

        {/* Ablation Toggles & Thresholds */}
        <div className="flex flex-wrap items-center justify-between gap-4 border-t border-borderSubtle/80 pt-3">
          <div className="flex flex-wrap items-center gap-3">
            <span className="flex items-center gap-1.5 text-xs font-medium text-slate-400">
              <SlidersHorizontal className="h-3.5 w-3.5 text-cyan-400" />
              Modules:
            </span>
            {[
              ['enable_recovery', 'Self-Healing Recovery'],
              ['enable_grounding', 'Evidence Grounding'],
              ['use_nli', 'DeBERTa NLI'],
              ['enable_uncertainty', 'Uncertainty Gate'],
            ].map(([key, label]) => (
              <label
                key={key}
                className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-borderSubtle bg-graphite px-2.5 py-1.5 text-xs text-slate-200 hover:border-slate-500"
              >
                <input
                  type="checkbox"
                  checked={Boolean(form[key])}
                  onChange={(e) => updateField(key, e.target.checked)}
                  className="rounded border-slate-600 bg-obsidian text-cyan-400 focus:ring-0"
                />
                {label}
              </label>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1.5 text-xs">
              <span className="text-slate-400">ANS ≤</span>
              <input
                type="number"
                step="0.05"
                min="0"
                max="0.95"
                value={form.answer_threshold}
                onChange={(e) => updateField('answer_threshold', Number(e.target.value))}
                className="w-16 rounded border border-borderSubtle bg-obsidian px-2 py-1 font-mono text-xs text-slate-100"
              />
            </div>
            <div className="flex items-center gap-1.5 text-xs">
              <span className="text-slate-400">ABS ≥</span>
              <input
                type="number"
                step="0.05"
                min="0.05"
                max="1"
                value={form.abstain_threshold}
                onChange={(e) => updateField('abstain_threshold', Number(e.target.value))}
                className="w-16 rounded border border-borderSubtle bg-obsidian px-2 py-1 font-mono text-xs text-slate-100"
              />
            </div>
            <button
              type="submit"
              disabled={running}
              className="inline-flex items-center gap-2 rounded-lg bg-cyan-500 px-4 py-2 text-xs font-bold text-obsidian hover:bg-cyan-400 disabled:opacity-60 transition-colors"
            >
              {running ? (
                <>
                  <Zap className="h-3.5 w-3.5 animate-spin" />
                  Executing ReactAgent...
                </>
              ) : (
                <>
                  <Play className="h-3.5 w-3.5" />
                  Run TraceRecover
                </>
              )}
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
            {error}
          </div>
        )}
      </form>

      {/* Live Output Inspection */}
      {lastRun ? (
        <RunInspectionPanel
          data={lastRun}
          onInspectEvidence={onInspectEvidence}
        />
      ) : (
        <div className="rounded-xl border border-dashed border-borderSubtle bg-panel/60 p-10 text-center">
          <Terminal className="mx-auto h-8 w-8 text-slate-600" />
          <p className="mt-2 text-sm font-medium text-slate-300">
            Select a preset above or enter a query to execute TraceRecover live.
          </p>
          <p className="mt-1 text-xs text-slate-500">
            Every execution writes real JSONL trace &amp; grounding reports to logs/ and returns full step provenance.
          </p>
        </div>
      )}
    </div>
  )
}
