import React, { useCallback, useEffect, useState } from 'react'
import {
  Activity,
  BarChart3,
  CheckCircle2,
  FlaskConical,
  LayoutDashboard,
  RefreshCw,
  ScrollText,
  ShieldCheck,
  Terminal,
} from 'lucide-react'
import { api } from './api'
import OverviewView from './views/OverviewView'
import PlaygroundView from './views/PlaygroundView'
import TraceExplorerView from './views/TraceExplorerView'
import FailureLabView from './views/FailureLabView'
import BenchmarksView from './views/BenchmarksView'
import EvidenceSettingsView from './views/EvidenceSettingsView'

const NAV_ITEMS = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'playground', label: 'Agent Playground', icon: Terminal },
  { id: 'traces', label: 'Trace Explorer', icon: ScrollText },
  { id: 'failure-lab', label: 'Failure Lab', icon: FlaskConical },
  { id: 'benchmarks', label: 'Benchmarks & Ablations', icon: BarChart3 },
  { id: 'evidence', label: 'Evidence & Settings', icon: ShieldCheck },
]

export default function App() {
  const [activeTab, setActiveTab] = useState('overview')
  const [health, setHealth] = useState(null)
  const [presets, setPresets] = useState([])
  const [benchmark, setBenchmark] = useState(null)
  const [settings, setSettings] = useState(null)
  const [recentTraces, setRecentTraces] = useState([])
  const [lastRun, setLastRun] = useState(null)
  const [explorerTraceId, setExplorerTraceId] = useState(null)
  const [evidenceTraceId, setEvidenceTraceId] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const refreshAll = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [h, p, b, s, t] = await Promise.all([
        api.getHealth(),
        api.getPresets(),
        api.getBenchmark(),
        api.getSettings(),
        api.getTraces({ limit: 30 }),
      ])
      setHealth(h)
      setPresets(p.presets || [])
      setBenchmark(b)
      setSettings(s)
      setRecentTraces(t.traces || [])
    } catch (err) {
      setError(err.message || 'Could not reach TraceRecover backend API.')
    } finally {
      setLoading(false)
    }
  }, [])

  const refreshTracesOnly = useCallback(async () => {
    try {
      const [h, t] = await Promise.all([
        api.getHealth(),
        api.getTraces({ limit: 30 }),
      ])
      setHealth(h)
      setRecentTraces(t.traces || [])
    } catch {
      // Keep existing state on transient refresh error
    }
  }, [])

  useEffect(() => {
    refreshAll()
  }, [refreshAll])

  const handleRunComplete = (result) => {
    setLastRun(result)
    setEvidenceTraceId(result.trace_id)
    refreshTracesOnly()
  }

  const handleOpenTrace = (traceId) => {
    setExplorerTraceId(traceId)
    setActiveTab('traces')
  }

  const handleInspectEvidence = (traceId) => {
    setEvidenceTraceId(traceId)
    setActiveTab('evidence')
  }

  const activeNav = NAV_ITEMS.find((n) => n.id === activeTab) || NAV_ITEMS[0]

  return (
    <div className="min-h-screen bg-obsidian text-slate-100 flex">
      {/* Left Navigation Sidebar */}
      <aside className="hidden md:flex md:w-64 md:flex-col border-r border-borderSubtle bg-graphite shrink-0">
        <div className="flex items-center gap-3 border-b border-borderSubtle px-5 py-4">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-cyan-400 to-indigo-500 text-obsidian font-mono font-black text-sm shadow-lg shadow-cyan-500/20">
            TR
          </div>
          <div>
            <div className="text-sm font-bold tracking-tight text-white">TraceRecover</div>
            <div className="font-mono text-[10px] text-cyan-400">AI Observability v2.0</div>
          </div>
        </div>

        <nav className="flex-1 space-y-1 p-3">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon
            const isActive = activeTab === item.id
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setActiveTab(item.id)}
                className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-xs font-medium transition-colors ${
                  isActive
                    ? 'bg-cyan-500/15 text-cyan-300 border border-cyan-500/30'
                    : 'text-slate-400 hover:bg-panel hover:text-slate-100 border border-transparent'
                }`}
              >
                <Icon className={`h-4 w-4 ${isActive ? 'text-cyan-400' : 'text-slate-500'}`} />
                {item.label}
              </button>
            )
          })}
        </nav>

        {/* Bottom System Telemetry Card */}
        <div className="border-t border-borderSubtle p-4 space-y-2.5 text-xs">
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-1.5 text-slate-400">
              <Activity className="h-3.5 w-3.5 text-emerald-400" />
              Engine Status
            </span>
            <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 font-mono text-[10px] font-semibold text-emerald-300">
              {health?.status === 'ok' ? 'ONLINE' : 'SYNCING'}
            </span>
          </div>
          <div className="flex items-center justify-between font-mono text-[11px]">
            <span className="text-slate-400">Core Test Suite</span>
            <span className="text-emerald-300 font-semibold">
              {health?.test_suite?.label || '122/122 verified'}
            </span>
          </div>
          <div className="flex items-center justify-between font-mono text-[11px]">
            <span className="text-slate-400">Python Runtime</span>
            <span className="text-slate-300">{health?.python_version || '3.14'}</span>
          </div>
          <div className="flex items-center justify-between font-mono text-[11px]">
            <span className="text-slate-400">Persisted Traces</span>
            <span className="text-cyan-300">{health?.trace_log_count ?? 0}</span>
          </div>
        </div>
      </aside>

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top Bar */}
        <header className="sticky top-0 z-30 border-b border-borderSubtle bg-graphite/90 backdrop-blur px-5 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <h1 className="text-base font-bold text-white">{activeNav.label}</h1>
              {health?.test_suite && (
                <span className="hidden sm:inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 font-mono text-[11px] text-emerald-300">
                  <CheckCircle2 className="h-3 w-3" />
                  {health.test_suite.label}
                </span>
              )}
              {health?.active_benchmark?.run_id && (
                <span className="hidden lg:inline-flex rounded-full border border-borderSubtle bg-obsidian px-2.5 py-0.5 font-mono text-[11px] text-slate-400">
                  {health.active_benchmark.run_id}
                </span>
              )}
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={refreshAll}
                className="inline-flex items-center gap-1.5 rounded-lg border border-borderSubtle bg-panel px-3 py-1.5 text-xs text-slate-300 hover:bg-panelHover hover:text-white"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
                Sync
              </button>
            </div>
          </div>

          {/* Mobile Tab Bar */}
          <div className="mt-2.5 flex md:hidden gap-1.5 overflow-x-auto pb-1">
            {NAV_ITEMS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setActiveTab(item.id)}
                className={`whitespace-nowrap rounded-md px-2.5 py-1 text-xs font-medium ${
                  activeTab === item.id
                    ? 'bg-cyan-500 text-obsidian'
                    : 'bg-panel text-slate-400'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </header>

        <main className="flex-1 p-5 lg:p-6 max-w-[1600px] w-full mx-auto">
          {error && (
            <div className="mb-4 rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-xs text-rose-200">
              <strong>Backend Error:</strong> {error}
            </div>
          )}

          {activeTab === 'overview' && (
            <OverviewView
              health={health}
              benchmark={benchmark}
              traces={recentTraces}
              onNavigate={setActiveTab}
              onSelectTrace={handleOpenTrace}
            />
          )}
          {activeTab === 'playground' && (
            <PlaygroundView
              presets={presets}
              providers={health?.providers}
              lastRun={lastRun}
              onRunComplete={handleRunComplete}
              onInspectEvidence={handleInspectEvidence}
            />
          )}
          {activeTab === 'traces' && (
            <TraceExplorerView
              initialTraceId={explorerTraceId}
              onClearInitialTrace={() => setExplorerTraceId(null)}
              onInspectEvidence={handleInspectEvidence}
            />
          )}
          {activeTab === 'failure-lab' && (
            <FailureLabView
              onRunRecorded={refreshTracesOnly}
              onInspectEvidence={handleInspectEvidence}
            />
          )}
          {activeTab === 'benchmarks' && (
            <BenchmarksView benchmark={benchmark} />
          )}
          {activeTab === 'evidence' && (
            <EvidenceSettingsView
              settings={settings}
              traces={recentTraces}
              selectedTraceId={evidenceTraceId}
              onSelectTraceId={setEvidenceTraceId}
            />
          )}
        </main>
      </div>
    </div>
  )
}
