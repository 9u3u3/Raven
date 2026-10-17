import { Check, Copy } from 'lucide-react'
import { useEffect, useState } from 'react'
import { get, on, post } from '../lib/api'
import { size } from '../lib/format'
import { say, useApp } from '../store'
import { Ghost, Switch, Track, stagger } from './ui'

export type Fit = { level: 'good' | 'slow' | 'no'; reason: string }
export type Variant = { id: string; label: string; note: string; mb: number; missing_mb: number; present: boolean; fit: Fit; ram_use: number; recommended: boolean; ollama: boolean }
export type ToolRow = { name: string; ok: boolean; install: string }
export type Feature = {
  id: string
  label: string
  adds: string
  kind: 'builtin' | 'model' | 'tool'
  always: boolean
  needs: string[]
  on: boolean
  state: 'ready' | 'off' | 'missing' | 'downloading' | 'error' | 'needs_ollama'
  fit: Fit
  recommended: boolean
  mb: number
  size_mb?: number
  ram_use?: number
  ollama?: boolean
  job?: { done?: number; total?: number; error?: string } | null
  tools?: ToolRow[]
  variants?: Variant[]
  variant?: string
  recommended_variant?: string
}
export type OllamaStatus = {
  installed: boolean
  running: boolean
  version: string | null
  path: string | null
  managed: boolean
  system: string
  can_install: boolean
  download: { version: string; bytes: number } | null
  steps: string[]
  command: string
  job: { state: string; done: number; total: number; error: string | null; step: string } | null
}
export type Device = {
  os: { system: string; name: string; arch: string }
  cpu: { model: string; cores: number | null; threads: number }
  ram: { total: number; free: number }
  gpus: { name: string; vendor: string; vram: number | null; shared: boolean }[]
  gpu_memory: number
  disk: { path: string; free: number; total: number }
  battery: { percent: number; on_battery: boolean } | null
  bench: number | null
}
export type FeatureStatus = { device: Device; summary: string; features: Feature[]; ollama: OllamaStatus; pending: string[] }

/** The feature list, kept fresh by the download and install events. */
export function useFeatures(): [FeatureStatus | null, (fresh?: boolean) => Promise<void>, (s: FeatureStatus) => void] {
  const [st, setSt] = useState<FeatureStatus | null>(null)
  const load = async (fresh = false): Promise<void> => {
    try {
      setSt(await get<FeatureStatus>(`/features${fresh ? '?fresh=true' : ''}`))
    } catch {
      /* the sidecar restarts: the next event loads it */
    }
  }
  useEffect(() => {
    void load()
    let t: ReturnType<typeof setTimeout> | null = null
    // Progress events come often: load at most twice a second.
    const soon = (): void => {
      if (!t) t = setTimeout(() => ((t = null), void load()), 500)
    }
    const offs = [on('models_progress', soon), on('ollama_install', soon)]
    const iv = setInterval(() => void load(), 8000)
    return () => {
      offs.forEach((f) => f())
      clearInterval(iv)
      if (t) clearTimeout(t)
    }
  }, [])
  return [st, load, setSt]
}

const GB = 1073741824
const gb = (n: number): string => (n / GB < 10 ? (n / GB).toFixed(1) : String(Math.round(n / GB)))

/** "16 GB RAM · RTX 3050 6 GB · 16 threads · 86 GB free" */
export function DeviceLine({ d }: { d: Device }) {
  const gpu = d.gpus.find((g) => !g.shared && g.vram) ?? d.gpus.find((g) => g.vendor === 'apple') ?? d.gpus[0]
  const parts = [
    d.os.name,
    d.cpu.model,
    `${d.cpu.threads} threads`,
    `${gb(d.ram.total)} GB RAM`,
    gpu ? `${gpu.name}${gpu.vram && !gpu.shared ? ` ${gb(gpu.vram)} GB` : gpu.vendor === 'apple' ? ` (${gb(gpu.vram ?? 0)} GB shared)` : ''}` : 'no GPU found',
    `${gb(d.disk.free)} GB free`
  ]
  if (d.battery?.on_battery) parts.push(`on battery ${d.battery.percent}%`)
  return <div className="mono text-[11.5px] leading-relaxed text-muted">{parts.join(' · ')}</div>
}

const FIT_TEXT = { good: 'Runs well', slow: 'Runs slowly', no: 'Not recommended' }
const FIT_CLS = { good: 'text-ok', slow: 'text-warn', no: 'text-danger' }

export function FitDot({ fit, short = false }: { fit: Fit; short?: boolean }) {
  return (
    <span className={`inline-flex items-center gap-1.5 text-[12px] ${FIT_CLS[fit.level]}`} title={fit.reason || FIT_TEXT[fit.level]}>
      <span className="inline-block h-[6px] w-[6px] rounded-full bg-current" />
      {FIT_TEXT[fit.level]}
      {!short && fit.reason && <span className="text-muted">· {fit.reason}</span>}
    </span>
  )
}

function pct(j?: { done?: number; total?: number } | null): number {
  return j && j.total ? Math.min(100, Math.floor(((j.done ?? 0) / j.total) * 100)) : 0
}

export function CopyCmd({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="mt-1.5 flex items-center gap-2 bg-console-bg px-3 py-1.5">
      <code className="selectable mono flex-1 select-all overflow-x-auto whitespace-nowrap text-[11.5px] text-console-text">{text}</code>
      <button
        className="flex items-center gap-1 px-1.5 text-[11px] text-console-text/80 transition-colors hover:text-console-text"
        onClick={() =>
          void navigator.clipboard.writeText(text).then(() => {
            setCopied(true)
            setTimeout(() => setCopied(false), 1600)
          })
        }
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
        {copied ? 'Copied' : 'Copy'}
      </button>
    </div>
  )
}

/** The choice at first start: which features, and which size of each model. */
export type Choice = { on: Record<string, boolean>; variants: Record<string, string> }

/** The first choice: every feature that runs well, with the recommended size. */
export function defaultChoice(st: FeatureStatus): Choice {
  const c: Choice = { on: {}, variants: {} }
  for (const f of st.features) {
    if (f.kind !== 'model' || f.always) continue
    c.on[f.id] = f.state === 'ready' || f.recommended
  }
  for (const f of st.features) if (f.variants && f.recommended_variant) c.variants[f.id] = f.variant && f.variants.find((v) => v.id === f.variant)?.present ? f.variant : f.recommended_variant
  return c
}

/** The download size and the disk size of a choice. */
export function choiceSize(st: FeatureStatus, c: Choice): { download: number; ollama: boolean } {
  let mb = 0
  let ollama = false
  for (const f of st.features) {
    if (f.kind !== 'model' || !(f.always || c.on[f.id])) continue
    const v = f.variants?.find((x) => x.id === c.variants[f.id])
    if (v) {
      mb += v.missing_mb
      ollama ||= v.ollama
    } else {
      mb += f.mb
      ollama ||= !!f.ollama
    }
  }
  return { download: mb * 1048576, ollama }
}

function stateText(f: Feature): string {
  switch (f.state) {
    case 'ready':
      return f.kind === 'tool' ? 'installed' : 'ready'
    case 'off':
      return 'off'
    case 'downloading':
      return `downloading ${pct(f.job)}%`
    case 'error':
      return 'failed'
    case 'needs_ollama':
      return 'waits for Ollama'
    default:
      return f.kind === 'tool' ? 'not installed' : f.mb ? `${size(f.mb * 1048576)} to download` : 'not ready'
  }
}

/**
 * One row for each feature: a check box (first start) or a switch (Settings), the fit dot with its reason, a
 * size choice for the models with sizes, and the download progress. "pick" mode keeps the choice in the
 * parent. "live" mode applies each change at once (Settings).
 */
export default function FeaturePicker({
  st,
  mode,
  choice,
  onChoice,
  onStatus
}: {
  st: FeatureStatus
  mode: 'pick' | 'live'
  choice?: Choice
  onChoice?: (c: Choice) => void
  onStatus?: (s: FeatureStatus) => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const models = st.features.filter((f) => f.kind !== 'tool')
  const tools = st.features.filter((f) => f.kind === 'tool')

  const apply = async (body: Partial<Choice>, id: string): Promise<void> => {
    setBusy(id)
    try {
      const r = await post<FeatureStatus & { reembed: boolean; waiting: string[] }>('/features/apply', { on: {}, variants: {}, ...body })
      onStatus?.(r)
      if (r.reembed) say('Raven reads the meaning of your files again in the background.')
      else if (r.waiting.length) say('The download starts when Ollama runs.')
    } catch (e) {
      say((e as Error).message, 'error')
    } finally {
      setBusy(null)
    }
  }
  const remove = (f: Feature, variant?: string): void => {
    const v = f.variants?.find((x) => x.id === (variant ?? f.variant))
    useApp.setState({
      confirm: {
        title: `Remove ${v ? v.label : f.label}?`,
        body: `Raven deletes the model files (${size((v?.mb ?? f.size_mb ?? 0) * 1048576)})${v?.ollama || f.ollama ? ' from Ollama' : ''}${variant ? '' : ' and turns the feature off'}. You can download them again here.`,
        action: 'Remove',
        danger: true,
        run: () => void removeNow(f, variant)
      }
    })
  }
  const removeNow = async (f: Feature, variant?: string): Promise<void> => {
    setBusy(f.id)
    try {
      const r = await post<FeatureStatus & { removed: string[] }>('/features/remove', { feature: f.id, variant })
      onStatus?.(r)
      say(r.removed.length ? `Removed ${f.label}` : `${f.label} is off. Another feature still uses its model.`)
    } catch (e) {
      say((e as Error).message, 'error')
    } finally {
      setBusy(null)
    }
  }

  const isOn = (f: Feature): boolean => (f.always || f.kind === 'builtin' ? true : mode === 'pick' ? !!choice?.on[f.id] : f.on)
  const variantOf = (f: Feature): string | undefined => (mode === 'pick' ? choice?.variants[f.id] : f.variant)

  const row = (f: Feature, k: number) => {
    const onNow = isOn(f)
    const vId = variantOf(f)
    const v = f.variants?.find((x) => x.id === vId)
    const fit = v?.fit ?? f.fit
    const fixed = f.always || f.kind === 'builtin'
    const toggle = (val: boolean): void => {
      if (mode === 'pick' && choice && onChoice) onChoice({ ...choice, on: { ...choice.on, [f.id]: val } })
      else void apply({ on: { [f.id]: val } }, f.id)
    }
    const pickVariant = (id: string): void => {
      if (mode === 'pick' && choice && onChoice) onChoice({ ...choice, variants: { ...choice.variants, [f.id]: id } })
      else void apply({ variants: { [f.id]: id } }, f.id)
    }
    const downloading = f.state === 'downloading'
    return (
      <div key={f.id} style={stagger(k)} className="anim-row grid grid-cols-[22px_minmax(0,1fr)_auto] items-start gap-3 border-b border-line/70 py-2.5">
        <span className="pt-0.5">
          {mode === 'pick' ? (
            <input type="checkbox" aria-label={f.label} disabled={fixed} checked={onNow} onChange={(e) => toggle(e.target.checked)} />
          ) : fixed ? (
            <Check size={14} className="mt-0.5 text-ok" />
          ) : (
            <Switch on={onNow} label={f.label} onChange={toggle} />
          )}
        </span>
        <span className="min-w-0">
          <span className={`block font-medium ${mode === 'live' && !fixed ? 'pl-3' : ''}`}>{f.label}</span>
          <span className={`block text-[12.5px] text-muted ${mode === 'live' && !fixed ? 'pl-3' : ''}`}>{f.adds}</span>
          <span className={`mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 ${mode === 'live' && !fixed ? 'pl-3' : ''}`}>
            {f.kind !== 'builtin' && <FitDot fit={fit} />}
            {f.variants && (
              <select aria-label={`${f.label} size`} value={vId} disabled={busy === f.id} onChange={(e) => pickVariant(e.target.value)} className="field mono !py-0.5 text-[11.5px]">
                {f.variants.map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.label} · {x.note} · {size(x.mb * 1048576)}
                    {x.recommended ? ' · suggested' : ''}
                    {x.fit.level === 'no' ? ' · not recommended' : x.fit.level === 'slow' ? ' · slow' : ''}
                  </option>
                ))}
              </select>
            )}
            {(f.tools ?? []).filter((t) => !t.ok).length > 0 && <span className="text-[12px] text-muted">needs {(f.tools ?? []).filter((t) => !t.ok).map((t) => t.name).join(', ')}</span>}
          </span>
          {mode === 'pick' && onNow && fit.level === 'no' && <span className="mt-1 block text-[12px] text-danger">This computer may not run it well. You can still try it.</span>}
          {downloading && (
            <span className="mt-1.5 block">
              <Track pct={pct(f.job)} color="var(--accent)" />
            </span>
          )}
          {f.state === 'error' && <span className="mt-1 block text-[12px] text-danger">{f.job?.error ?? 'The download failed.'}</span>}
        </span>
        <span className="flex flex-col items-end gap-1 pt-0.5 text-right">
          <span className={`mono text-[11.5px] ${f.state === 'ready' ? 'text-ok' : f.state === 'error' ? 'text-danger' : 'text-muted'}`}>
            {mode === 'pick' && f.state !== 'ready' && f.state !== 'downloading' ? (v ? (v.missing_mb ? size(v.missing_mb * 1048576) : 'ready') : f.mb ? size(f.mb * 1048576) : fixed ? 'built in' : 'no download') : stateText(f)}
          </span>
          {mode === 'live' && f.state === 'error' && (
            <Ghost onClick={() => void apply({ on: { [f.id]: true } }, f.id)} disabled={busy === f.id}>
              Retry
            </Ghost>
          )}
          {mode === 'live' && f.kind === 'model' && !f.always && f.state === 'ready' && (f.size_mb ?? 0) > 0 && (
            <Ghost tone="danger" onClick={() => remove(f)} disabled={busy === f.id} title="Delete the model files and turn the feature off">
              Remove
            </Ghost>
          )}
          {mode === 'live' && f.variants && f.variants.some((x) => x.present && x.id !== f.variant && x.mb > 0) && (
            <Ghost tone="plain" onClick={() => {
                const other = f.variants!.find((x) => x.present && x.id !== f.variant && x.mb > 0)
                if (other) remove(f, other.id)
              }} disabled={busy === f.id} title="Delete a size that Raven does not use">
              Remove an unused size
            </Ghost>
          )}
        </span>
      </div>
    )
  }

  return (
    <div>
      <div className="border-t border-line">{models.map((f, k) => row(f, k))}</div>
      {tools.length > 0 && (
        <>
          <div className="label mb-1 mt-5">System programs · {tools.filter((t) => t.state === 'ready').length} of {tools.length} installed</div>
          <div className="border-t border-line">
            {tools.map((f) => {
              const missing = (f.tools ?? []).filter((t) => !t.ok)
              return (
                <div key={f.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3 border-b border-line/70 py-2">
                  <span className="min-w-0">
                    <span className="block">{f.label}</span>
                    <span className="block text-[12.5px] text-muted">{f.adds}</span>
                    {missing.filter((t) => t.install).map((t) => (
                      <CopyCmd key={t.name} text={t.install} />
                    ))}
                  </span>
                  <span className={`mono pt-0.5 text-[11.5px] ${missing.length ? 'text-muted' : 'text-ok'}`}>{missing.length ? `needs ${missing.map((t) => t.name).join(', ')}` : 'installed'}</span>
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
