// Reclaim (PLAN2 P2-4): one total of the space Raven can free, one row per source, and "free X GB".
// Every change opens in the review view first. Rows that cannot be undone say so there.

import { useEffect, useState } from 'react'
import { get, post } from '../lib/api'
import { size } from '../lib/format'
import type { Journal } from '../lib/types'
import { activity, openReview, say, useApp, type ReviewStep } from '../store'
import { Chip, Ghost } from './ui'
import { pickFolder } from './FolderPicker'

type Item = { path: string; size: number; checked: boolean; note: string; blocked?: boolean; is_dir?: boolean }
type Row = {
  id: string
  source: string
  line: string
  items: Item[]
  safety: 'safe' | 'check' | 'lossy'
  action: string
  reversible: boolean
  bytes: number
  total: number
  admin?: boolean
  offload?: boolean
  estimate?: boolean
}
type Forecast = { used_pct: number; warn: boolean; days: number | null; per_day: number | null }
type Scan = { id: string; path: string; rows: Row[]; total: number; ms: number; disk: { total: number; used: number; free: number }; forecast: Forecast }
type RunResult = { done: number; errors: string[]; freed: number; in_trash: number; journal: Journal; label: string }

const GB = 1024 ** 3

function step(r: Row, pick?: Set<string>): ReviewStep & { rid: string } {
  return {
    rid: r.id,
    label: `${r.source} · ${r.line}`,
    permanent: !r.reversible,
    confirm: r.admin,
    files: r.items.map((i) => ({ path: i.path, size: i.size, checked: pick ? pick.has(i.path) : i.checked && !i.blocked, note: i.note, is_dir: i.is_dir }))
  }
}

async function runSteps(scan: Scan, steps: ReviewStep[], after: () => void): Promise<void> {
  const rows: Record<string, string[]> = {}
  for (const s of steps as (ReviewStep & { rid: string })[]) {
    const paths = s.files.filter((f) => f.checked).map((f) => f.path)
    if (paths.length) rows[s.rid] = paths
  }
  say('Freeing space…', 'busy')
  try {
    const r = await post<RunResult>('/space/reclaim/run', { id: scan.id, rows })
    useApp.setState({ journal: r.journal })
    const parts = [r.freed > 0 && `freed ${size(r.freed)}`, r.in_trash > 0 && `${size(r.in_trash)} in the trash`].filter(Boolean).join(', ')
    if (r.errors.length) say(`${parts || 'Done'}. ${r.errors[0]}`, 'error', { undo: r.in_trash > 0 })
    else say(`Done: ${parts || r.label}`, 'info', { undo: r.in_trash > 0 })
  } catch (e) {
    say((e as Error).message, 'error')
  }
  after()
}

function reviewRows(scan: Scan, rows: Row[], title: string, after: () => void, pick?: Record<string, string[]>): void {
  openReview({
    // Habits: a cancelled review counts as a rejected suggestion (it stays away for 30 days).
    onClose: (ran) => rows.forEach((r) => activity(ran ? 'suggestion_accepted' : 'suggestion_rejected', undefined, r.id)),
    title,
    reversible: rows.every((r) => r.reversible),
    cloudBytes: 0,
    frees: true,
    steps: rows.map((r) => step(r, pick ? new Set(pick[r.id] ?? []) : undefined)),
    run: (steps) => runSteps(scan, steps, after)
  })
}

function reviewOffload(r: Row, after: () => void): void {
  void (async () => {
    const picked = await pickFolder('Move the files to')
    if (!picked.length) return
    openReview({
      title: `Move to ${picked[0]}`,
      reversible: true,
      cloudBytes: 0,
      frees: true,
      steps: [{ label: 'Move', files: r.items.map((i) => ({ path: i.path, size: i.size, checked: true, note: i.note })) }],
      run: async (steps) => {
        const paths = steps[0].files.filter((f) => f.checked).map((f) => f.path)
        try {
          const res = await post<{ moved: number; errors: string[]; journal: Journal; label: string }>('/space/offload', { paths, target: picked[0] })
          useApp.setState({ journal: res.journal })
          say(res.errors.length ? `${res.label}. ${res.errors[0]}` : `Done: ${res.label}`, res.errors.length ? 'error' : 'info', { undo: true })
        } catch (e) {
          say((e as Error).message, 'error')
        }
        after()
      }
    })
  })()
}

/** What each flag means, on hover. */
const TIPS: Record<string, string> = {
  safe: 'Safe: the app or tool makes these again when it needs them, or a copy stays.',
  check: 'Check: Raven cannot tell if you still need these. Look at the list before you free them.',
  lossy: 'Lossy: the smaller file is not the same data.'
}
const SAFETY: Record<string, [string, 'good' | 'warn' | 'bad']> = { safe: ['safe', 'good'], check: ['check', 'warn'], lossy: ['lossy', 'bad'] }

export default function Reclaim({ path }: { path: string }) {
  const [scan, setScan] = useState<Scan | null>(null)
  const [busy, setBusy] = useState(false)
  const [target, setTarget] = useState('10')
  const [err, setErr] = useState<string | null>(null)

  const load = async (): Promise<void> => {
    setBusy(true)
    setErr(null)
    try {
      setScan(await get<Scan>('/space/reclaim', path ? { path } : {}))
    } catch (e) {
      setErr((e as Error).message)
    }
    setBusy(false)
  }
  useEffect(() => {
    void load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path])

  const planFree = async (): Promise<void> => {
    if (!scan) return
    const want = Math.round(parseFloat(target) * GB)
    if (!(want > 0)) return
    try {
      const p = await post<{ bytes: number; enough: boolean; rows: Record<string, string[]> }>('/space/reclaim/plan', { target: want, id: scan.id })
      const rows = scan.rows.filter((r) => p.rows[r.id])
      if (!rows.length) return say('Nothing safe to free here.', 'error')
      if (!p.enough) say(`Raven finds ${size(p.bytes)}, less than ${target} GB.`)
      reviewRows(scan, rows, `Free ${size(p.bytes)}`, () => void load(), p.rows)
    } catch (e) {
      say((e as Error).message, 'error')
    }
  }

  const f = scan?.forecast
  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 py-2">
        <span className="mono text-[13px]">{scan ? size(scan.total) : '…'}</span>
        {f && (
          <span className={`mono text-[12px] ${f.warn ? 'text-danger' : 'text-muted'}`}>
            disk {f.used_pct}%{f.days != null ? ` · full in ~${f.days} d` : ''}
          </span>
        )}
        <span className="ml-auto flex items-center gap-2">
          <span className="text-[12px] text-muted">Free</span>
          <input value={target} onChange={(e) => setTarget(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void planFree()} className="field mono w-16 text-right" />
          <span className="text-[12px] text-muted">GB</span>
          <button className="btn-primary" disabled={!scan || busy} onClick={() => void planFree()}>
            Plan
          </button>
          <Ghost tone="plain" onClick={() => void load()}>
            {busy ? 'Scanning…' : 'Rescan'}
          </Ghost>
        </span>
      </div>
      {err && <div className="py-2 text-[12px] text-danger">{err}</div>}
      <div className="divide-y divide-line border-y border-line">
        {scan?.rows.map((r) => {
          const [word, tone] = SAFETY[r.safety] ?? ['', 'plain']
          return (
            <div key={r.id} className="grid grid-cols-[minmax(0,1fr)_auto_auto_auto] items-center gap-4 py-2">
              <div className="min-w-0">
                <div className="truncate">{r.source}</div>
                <div className="truncate text-[12px] text-muted">{r.line}</div>
              </div>
              <span className="flex gap-1.5">
                <span title={TIPS[r.safety] ?? ''}>
                  <Chip tone={tone}>{word}</Chip>
                </span>
                {!r.reversible && (
                  <span title="Raven deletes these. They do not go to the trash, and Undo cannot bring them back.">
                    <Chip tone="bad">permanent</Chip>
                  </span>
                )}
                {r.admin && (
                  <span title="The system asks for your password before Raven cleans these.">
                    <Chip tone="plain">admin</Chip>
                  </span>
                )}
              </span>
              <span className="mono w-20 text-right text-[12px]">
                {r.estimate ? '~' : ''}
                {size(r.total)}
              </span>
              <span className="flex gap-1">
                {r.offload && (
                  <Ghost tone="plain" onClick={() => reviewOffload(r, () => void load())}>
                    Move…
                  </Ghost>
                )}
                <Ghost onClick={() => scan && reviewRows(scan, [r], r.source, () => void load())}>Review</Ghost>
              </span>
            </div>
          )
        })}
        {scan && !scan.rows.length && <div className="py-3 text-[12px] text-muted">Nothing to free here.</div>}
      </div>
    </div>
  )
}
