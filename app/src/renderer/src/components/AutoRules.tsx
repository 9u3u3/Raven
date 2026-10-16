import { useEffect, useState } from 'react'
import { get, post } from '../lib/api'
import { size, tildify } from '../lib/format'
import type { Journal } from '../lib/types'
import { refreshGhost, say, useApp } from '../store'
import { EmptyState } from './EmptyState'
import { Chip, Ghost, SectionTitle, Switch, stagger } from './ui'

// Auto mode rules (section 9.8 step 3) as a plain form. Exact, reversible methods only.
type Rule = { id?: string; folder: string; cold_days: number; min_mb: number; method: string; daily_gb: number; enabled: boolean }
type LogEntry = { ts: number; folder: string; jobs: number; saved: number; waiting: number; errors: string[] }
type State = { rules: Rule[]; log: LogEntry[]; ac: boolean; idle: boolean; buffer_days: number; methods: string[] }

const METHOD_TEXT: Record<string, string> = { auto: 'best exact method', '7z': '.7z', 'tar.zst': '.tar.zst', zip: '.zip' }

export default function AutoRules({ folder }: { folder: string }) {
  const home = useApp((s) => s.places?.home ?? '')
  const [st, setSt] = useState<State | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const load = (): void => void get<State>('/compress/auto').then(setSt).catch(() => {})
  useEffect(load, [])

  const save = async (rules: Rule[]): Promise<void> => {
    try {
      const r = await post<{ rules: Rule[] }>('/compress/auto/rules', { rules })
      setSt((s) => (s ? { ...s, rules: r.rules } : s))
    } catch (e) {
      say((e as Error).message, 'error')
    }
  }
  const edit = (i: number, p: Partial<Rule>): void => {
    if (!st) return
    const rules = st.rules.map((r, j) => (j === i ? { ...r, ...p } : r))
    setSt({ ...st, rules })
    void save(rules)
  }
  const runNow = async (r: Rule): Promise<void> => {
    setBusy(r.id!)
    try {
      const res = await post<{ entry: LogEntry; journal: Journal }>('/compress/auto/run', { id: r.id, skip_wait: true })
      useApp.setState({ journal: res.journal })
      const e = res.entry
      say(e.jobs ? `Auto compression: ${e.jobs} job${e.jobs === 1 ? '' : 's'}, saved ${size(e.saved)}` : 'Auto compression found nothing to do', 'info', { undo: e.jobs > 0 })
      void refreshGhost()
      load()
    } catch (e) {
      say((e as Error).message, 'error')
    } finally {
      setBusy(null)
    }
  }

  if (!st) return <p className="py-4 text-muted">Loading…</p>
  const num = (v: string, d: number): number => (Number.isFinite(Number(v)) && v !== '' ? Number(v) : d)
  const lab = 'mb-1 block text-[12px] text-muted'
  return (
    <div>
      <div className="mb-6 flex flex-wrap items-start gap-x-8 gap-y-2">
        <p className="min-w-[280px] flex-1 text-[14px] leading-relaxed text-muted">
          Auto mode compresses cold files with exact, reversible methods only. It runs on AC power when the laptop is idle, and waits {st.buffer_days} days after a file set becomes a candidate. Each run is one undo.
        </p>
        <span className="flex gap-5 pt-1">
          <Chip tone={st.ac ? 'good' : 'warn'}>{st.ac ? 'on AC power' : 'on battery'}</Chip>
          <Chip tone={st.idle ? 'good' : 'plain'}>{st.idle ? 'idle' : 'busy'}</Chip>
        </span>
      </div>
      {st.rules.length === 0 && <EmptyState art="chart" title="No auto rules yet" />}
      <div>
        {st.rules.map((r, i) => (
          <div key={r.id} style={stagger(i)} className={`anim-row flex flex-wrap items-end gap-x-6 gap-y-3 border-b border-line py-4 transition-opacity ${r.enabled ? '' : 'opacity-50'}`}>
            <span className="self-center">
              <Switch on={r.enabled} label={r.enabled ? 'Turn off' : 'Turn on'} onChange={(v) => edit(i, { enabled: v })} />
            </span>
            <span className="min-w-[200px] flex-1 self-center">
              <span className={lab}>folder</span>
              <span className="mono block truncate text-[12.5px]" title={r.folder}>
                {tildify(r.folder, home)}
              </span>
            </span>
            <label>
              <span className={lab}>cold for days</span>
              <input defaultValue={r.cold_days} onBlur={(e) => edit(i, { cold_days: num(e.target.value, r.cold_days) })} className="field mono w-20" />
            </label>
            <label>
              <span className={lab}>min size MB</span>
              <input defaultValue={r.min_mb} onBlur={(e) => edit(i, { min_mb: num(e.target.value, r.min_mb) })} className="field mono w-24" />
            </label>
            <label>
              <span className={lab}>method</span>
              <select value={r.method} onChange={(e) => edit(i, { method: e.target.value })} className="field mono">
                {st.methods.map((m) => (
                  <option key={m} value={m}>
                    {METHOD_TEXT[m] ?? m}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span className={lab}>daily limit GB</span>
              <input defaultValue={r.daily_gb} onBlur={(e) => edit(i, { daily_gb: num(e.target.value, r.daily_gb) })} className="field mono w-24" />
            </label>
            <span className="flex items-center gap-2 self-center">
              <Ghost disabled={busy === r.id} onClick={() => void runNow(r)} title="Runs now and skips the wait">
                {busy === r.id ? 'Running…' : 'Run now'}
              </Ghost>
              <button className="link text-muted hover:text-danger" title="Remove this rule" aria-label="Remove rule" onClick={() => void save(st.rules.filter((_, j) => j !== i))}>
                remove
              </button>
            </span>
          </div>
        ))}
      </div>
      <div className="mt-4">
        <Ghost disabled={!folder || st.rules.some((r) => r.folder === folder)} onClick={() => void save([...st.rules, { folder, cold_days: 90, min_mb: 50, method: 'auto', daily_gb: 5, enabled: true }])}>
          Add a rule for {tildify(folder, home) || 'this folder'}
        </Ghost>
      </div>
      {st.log.length > 0 && (
        <div className="mt-4">
          <SectionTitle>Recent runs</SectionTitle>
          <div className="flex flex-col gap-1">
            {st.log.slice(0, 10).map((e) => (
              <div key={e.ts} className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line/60 px-1 py-2 text-[12px] transition-colors hover:bg-hover">
                <span className="mono text-muted">{new Date(e.ts).toLocaleString()}</span>
                <span className="mono">{tildify(e.folder, home)}</span>
                <Chip tone={e.jobs ? 'good' : 'plain'}>
                  {e.jobs} job{e.jobs === 1 ? '' : 's'}
                </Chip>
                <Chip>saved {size(e.saved) || '0 B'}</Chip>
                {e.waiting ? <Chip tone="warn">{e.waiting} waiting</Chip> : null}
                {e.errors.length ? <Chip tone="bad">{e.errors[0]}</Chip> : null}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
