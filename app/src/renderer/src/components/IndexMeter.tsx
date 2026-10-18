import { useEffect, useRef, useState } from 'react'
import type { IndexProgress } from '../lib/types'
import { setScreen } from '../store'

type Stage = { key: string; label: string; what: string; done: number; total: number | null; left: number; rate: number; off?: string | null }

// Typical speeds on this kind of laptop (items per second), for stages that have no measured speed yet.
// Images: SigLIP 2 on the CPU reads several photos a second. The stage waits for text and meaning, so its own
// speed shows only when it runs.
const DEFAULT_RATE: Record<string, number> = { text: 15, meaning: 2.4, images: 4, speech: 0.08 }
// The share of the total time that each stage takes, for one overall percent.
const WEIGHT: Record<string, number> = { text: 0.2, meaning: 0.55, images: 0.15, speech: 0.1 }

type Sample = { t: number; left: Record<string, number> }

// The highest believable speed of each stage (items per second). A cleanup (code files that become names only,
// old chunks that go) lowers the counts fast, and that is not work done.
const MAX_RATE: Record<string, number> = { text: 150, meaning: 40, images: 25, speech: 1, names: 20000 }

function stagesOf(i: IndexProgress): Omit<Stage, 'rate'>[] {
  const vt = i.visual_total ?? 0
  const vp = i.visual_pending ?? 0
  return [
    { key: 'text', label: 'Text', what: 'reads documents, OCR for scans', done: i.files - i.text_pending, total: i.files, left: i.text_pending },
    { key: 'meaning', label: 'Meaning', what: 'embeddings for search by meaning and Ask', done: i.chunks - i.embed_pending, total: i.chunks, left: i.embed_pending },
    { key: 'images', label: 'Images', what: 'photos, PDF pages and video frames', done: Math.max(0, vt - vp), total: vt, left: vp, off: i.visual_off },
    { key: 'speech', label: 'Speech', what: 'transcripts of audio and video', done: 0, total: null, left: i.speech_pending ?? 0, off: i.speech_off }
  ]
}

/** The items that a stage still works on. A stage that does not run (no model, off) adds no time. */
const todo = (s: Omit<Stage, 'rate'>): number => (s.off ? 0 : s.left)

export function minutes(s: number): string {
  if (!isFinite(s) || s <= 0) return ''
  if (s < 60) return 'less than 1 min'
  if (s < 3600) return `about ${Math.round(s / 60)} min`
  const h = s / 3600
  return h < 48 ? `about ${h < 10 ? h.toFixed(1) : Math.round(h)} h` : `about ${Math.round(h / 24)} days`
}

/** Speed of each stage from the samples of the last 90 s. A stage with no fall in that time keeps its default. */
function rates(samples: Sample[]): Record<string, number> {
  const out: Record<string, number> = {}
  if (samples.length < 2) return out
  const a = samples[0]
  const b = samples[samples.length - 1]
  const dt = (b.t - a.t) / 1000
  if (dt < 5) return out
  for (const k of Object.keys(b.left)) {
    const d = (a.left[k] ?? 0) - b.left[k]
    if (d > 0) out[k] = Math.min(d / dt, MAX_RATE[k] ?? Infinity)
  }
  return out
}

/**
 * Index progress in the status line: a bar, the overall percent and the time left. A click opens one row per
 * stage. The names stage has no total (Raven does not know the number of files before it lists them).
 */
export default function IndexMeter({ index, error }: { index: IndexProgress; error: string | null }) {
  const [open, setOpen] = useState(false)
  const samples = useRef<Sample[]>([])
  const box = useRef<HTMLDivElement>(null)

  const raw = stagesOf(index)
  const listing = index.stage === 'metadata'
  useEffect(() => {
    const now = Date.now()
    // While Raven lists the files, the stage counts change because files get relabeled, not because work is
    // done: those samples do not count. The names count is the only speed that means something then.
    const left: Record<string, number> = listing
      ? { names: -index.scanned }
      : Object.fromEntries(raw.map((s) => [s.key, s.left]))
    const keep = samples.current.filter((s) => now - s.t < 90_000 && ('names' in s.left) === listing)
    samples.current = [...keep, { t: now, left }]
  }, [index]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent | KeyboardEvent): void => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !box.current?.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', close)
    window.addEventListener('keydown', close)
    return () => (window.removeEventListener('mousedown', close), window.removeEventListener('keydown', close))
  }, [open])

  const measured = rates(samples.current)
  const stages: Stage[] = raw.map((s) => ({ ...s, rate: measured[s.key] ?? DEFAULT_RATE[s.key] }))
  const busy = listing || stages.some((s) => todo(s) > 0)
  // Listing: compare with the number of files of the last list. It is a guess when folders changed a lot.
  const listTotal = Math.max(index.files, index.scanned)
  const listLeft = Math.max(0, listTotal - index.scanned)
  const listEta = measured.names ? listLeft / measured.names : 0
  const listPct = listTotal ? Math.min(99, Math.floor((index.scanned / listTotal) * 100)) : 0
  // Files still waiting for text become chunks later. The sidecar measures chunks per read file; before it
  // has enough files, use 2 (most files in a home folder are short).
  const meaningLeft = stages[1].left + stages[0].left * (index.chunks_per_file ?? 2)
  const etaSec = stages[0].left / stages[0].rate + meaningLeft / stages[1].rate + todo(stages[2]) / stages[2].rate + todo(stages[3]) / stages[3].rate
  const frac = (s: Stage): number => (s.total ? Math.min(1, s.done / Math.max(1, s.total)) : s.left ? 0 : 1)
  // A stage that does not run leaves the percent, so the bar can reach 100.
  const live = stages.filter((s) => !s.off)
  const weight = live.reduce((a, s) => a + WEIGHT[s.key], 0) || 1
  const pct = listing ? 0 : Math.floor((live.reduce((a, s) => a + WEIGHT[s.key] * frac(s), 0) / weight) * 100)

  const label = !index.roots.length
    ? 'No folders are indexed'
    : listing
      ? `listing files ${listPct}%${listEta > 0 ? ` · ${minutes(listEta)} left` : ''}`
      : busy
        ? `indexing ${pct}%${etaSec > 0 ? ` · ${minutes(etaSec)} left` : ''}`
        : `Indexed ${index.files.toLocaleString()} files`

  return (
    <div ref={box} className="relative flex min-w-0 items-center">
      <button
        className={`link flex min-w-0 items-center gap-2 ${error ? 'text-danger' : 'text-muted hover:text-text'}`}
        title="Index progress: click for each stage"
        onClick={() => setOpen((o) => !o)}
      >
        {busy && (
          <span className="relative h-[3px] w-16 shrink-0 overflow-hidden rounded-full bg-line">
            <span className="absolute inset-y-0 left-0 bg-accent transition-[width] duration-300" style={{ width: `${listing ? listPct : pct}%` }} />
          </span>
        )}
        <span className="truncate">
          {label}
          {error && ' · meaning search off'}
        </span>
      </button>
      {open && (
        <div className="float-panel anim-pop fixed bottom-10 left-4 z-50 w-[600px] max-w-[calc(100vw-32px)] whitespace-normal p-4 text-[12px] text-text">
          <div className="mb-3 flex items-baseline justify-between">
            <span className="label">Index</span>
            <span className="mono text-[11px] text-muted">
              {listing
                ? `listing the files${listEta > 0 ? ` · ${minutes(listEta)} left` : ''} · then Raven can say the time`
                : busy
                  ? `${pct}% · ${minutes(etaSec) || 'almost done'} left`
                  : 'complete'}
            </span>
          </div>
          <div className="mono mb-2 grid grid-cols-[80px_minmax(0,1fr)_190px] gap-x-3 gap-y-1.5">
            <span>Names</span>
            <span className="text-muted">lists the files in the folders</span>
            <span className="text-right">
              {listing ? `${index.scanned.toLocaleString()} of about ${listTotal.toLocaleString()}` : `${index.files.toLocaleString()} files`}
            </span>
            {stages.map((s) => {
              const f = frac(s)
              const eta = s.left / s.rate
              return (
                <span key={s.key} className="contents">
                  <span className={todo(s) ? '' : 'text-muted'}>{s.label}</span>
                  <span className="min-w-0">
                    <span className="relative mb-0.5 block h-[3px] w-full overflow-hidden rounded-full bg-line">
                      <span className="absolute inset-y-0 left-0 bg-accent" style={{ width: `${Math.floor(f * 100)}%` }} />
                    </span>
                    <span className="block truncate text-[11px] text-muted" title={s.off ?? undefined}>
                      {s.off && s.left ? `not running: ${s.off}` : s.what}
                    </span>
                  </span>
                  <span className={`text-right ${s.off && s.left ? 'text-muted' : s.left ? '' : 'text-accent'}`}>
                    {listing
                      ? 'waits for the list'
                      : s.off && s.left
                        ? `${s.left.toLocaleString()} wait`
                        : s.left
                          ? `${s.left.toLocaleString()} left${eta > 0 ? ` · ${minutes(eta)}` : ''}`
                          : s.total || s.key === 'speech'
                            ? 'done'
                            : '—'}
                  </span>
                </span>
              )
            })}
          </div>
          {index.visual_current && (index.visual_pending ?? 0) > 0 && (
            <p className="mono truncate text-[11px] text-muted" title={index.visual_current}>
              now: {index.visual_current.split('/').pop()}
            </p>
          )}
          {error && <p className="mt-2 text-danger">{error}. Start Ollama with: ollama serve</p>}
          <p className="mt-3 text-[11px] text-muted">
            Times use the speed of the last minute. The local AI shares the GPU, so a question or a plan can slow the index for a moment.{' '}
            <button className="link text-accent" onClick={() => (setOpen(false), setScreen('settings'))}>
              Indexed folders
            </button>
          </p>
        </div>
      )}
    </div>
  )
}
