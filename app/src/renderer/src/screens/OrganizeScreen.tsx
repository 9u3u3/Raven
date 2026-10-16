import { AlertCircle, Check, Lightbulb, PenLine } from 'lucide-react'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { get, post } from '../lib/api'
import { tildify } from '../lib/format'
import type { Journal } from '../lib/types'
import { navigate, reload, say, useApp } from '../store'
import { FileIcon } from '../components/FileIcon'
import WatchRules from '../components/WatchRules'
import FileGraph from '../components/FileGraph'
import { Chip, Ghost, Seg, TreeGuides, stagger } from '../components/ui'
import { pickFolder } from '../components/FolderPicker'

// Organize (section 12.4, 9.5): two trees side by side, now and after. Changed items show in the
// accent color with the reason below. The user unchecks, renames in place, or drags an item to
// another folder in the right tree. Apply runs the plan as one undo.

type Move = { src: string; dst: string; reason: string; category: string | null; rename: boolean; move: boolean }
type Plan = {
  id: string
  root: string
  tier: string
  planner: string
  ms: number
  moves: Move[]
  skipped: { path: string; reason: string }[]
  before: { name: string; is_dir: boolean; path: string }[]
}
type Item = { src: string; folder: string; name: string; reason: string; checked: boolean; category: string | null }
type Stats = { share: Record<string, number>; done: number; total: number }

const base = (p: string): string => p.slice(p.lastIndexOf('/') + 1)
const ext = (n: string): string => (n.includes('.') ? n.slice(n.lastIndexOf('.') + 1).toLowerCase() : '')

export default function OrganizeScreen() {
  const cwd = useApp((s) => s.path)
  const home = useApp((s) => s.places?.home ?? '')
  const [root, setRoot] = useState(cwd)
  const [mode, setMode] = useState<'both' | 'move' | 'rename'>('both')
  const [plan, setPlan] = useState<Plan | null>(null)
  const [items, setItems] = useState<Item[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState<string | null>(null)
  const view = useApp((s) => s.organizeView)
  const setView = (v: 'plan' | 'rules' | 'map'): void => useApp.setState({ organizeView: v })

  useEffect(() => {
    void get<Stats>('/classify/stats').then(setStats).catch(() => {})
  }, [plan])

  // A Space insight or the command bar can open this screen with a folder: plan it at once.
  const asked = useApp((s) => s.organizeRoot)
  useEffect(() => {
    if (!asked) return
    useApp.setState({ organizeRoot: null })
    setRoot(asked)
    void suggestFor(asked)
  }, [asked]) // eslint-disable-line react-hooks/exhaustive-deps

  const suggest = (think = false, m = mode): Promise<void> => suggestFor(root, think, m)
  async function suggestFor(folder: string, think = false, m = mode): Promise<void> {
    setBusy(think ? 'Asking a model for the tree…' : 'Grouping files…')
    setError(null)
    try {
      const p = await post<Plan>('/organize/suggest', { path: folder, mode: m, think_harder: think })
      setPlan(p)
      setItems(
        p.moves.map((mv) => {
          const rel = mv.dst.slice(p.root.length + 1)
          const i = rel.lastIndexOf('/')
          return { src: mv.src, folder: i >= 0 ? rel.slice(0, i) : '', name: rel.slice(i + 1), reason: mv.reason, checked: true, category: mv.category }
        })
      )
    } catch (e) {
      setError((e as Error).message)
      setPlan(null)
    } finally {
      setBusy(null)
    }
  }

  const apply = async (): Promise<void> => {
    if (!plan) return
    const keep = items.filter((i) => i.checked)
    setBusy('Applying…')
    try {
      const r = await post<{ moved: number; errors: string[]; label: string; journal: Journal; learned: Record<string, string> }>('/organize/apply', {
        id: plan.id,
        items: keep.map((i) => ({ src: i.src, dst: `${plan.root}/${i.folder ? `${i.folder}/` : ''}${i.name}` }))
      })
      useApp.setState({ journal: r.journal })
      const learned = Object.entries(r.learned ?? {}).map(([c, f]) => `${c.toLowerCase()} → ${f}`)
      const note = learned.length ? ` Raven will use your folders next time: ${learned.join(', ')}.` : ''
      say(r.errors.length ? `${r.label}: ${r.moved} moved. ${r.errors[0]}` : `${r.label}: ${r.moved} items moved.${note}`, r.errors.length ? 'error' : 'info', {
        undo: true
      })
      setPlan(null)
      setItems([])
      await reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  // The "after" tree: folders that stay, then the folders of the plan, with the items in them.
  const after = useMemo(() => {
    if (!plan) return []
    const moved = new Set(items.filter((i) => i.checked).map((i) => i.src))
    const groups = new Map<string, { item?: Item; stay?: string }[]>()
    const add = (folder: string, e: { item?: Item; stay?: string }): void => {
      if (!groups.has(folder)) groups.set(folder, [])
      groups.get(folder)!.push(e)
    }
    for (const b of plan.before) if (b.is_dir) groups.set(b.name, [])
    for (const i of items) add(i.checked ? i.folder : '', { item: i })
    for (const b of plan.before) if (!b.is_dir && !moved.has(b.path) && !items.some((i) => i.src === b.path)) add('', { stay: b.name })
    return [...groups.entries()].sort((a, b) => (a[0] === '' ? 1 : b[0] === '' ? -1 : a[0].localeCompare(b[0])))
  }, [plan, items])

  const changed = new Set(items.filter((i) => i.checked).map((i) => i.src))
  const setItem = (src: string, p: Partial<Item>): void => setItems((xs) => xs.map((x) => (x.src === src ? { ...x, ...p } : x)))
  const checkedCount = items.filter((i) => i.checked).length

  // The curves between the two trees: one for each item that moves. Both trees sit in one scrolling page, so a
  // row's place on the left and on the right can be measured together and joined.
  const gutter = useRef<HTMLDivElement>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const [curves, setCurves] = useState<{ id: string; y1: number; y2: number }[]>([])
  const [gw, setGw] = useState({ w: 120, h: 0 })
  const [hot, setHot] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(() => setTick((t) => t + 1))
    ro.observe(el)
    return () => ro.disconnect()
  }, [plan, view])
  useLayoutEffect(() => {
    const g = gutter.current
    if (!g || !plan) return setCurves([])
    const gr = g.getBoundingClientRect()
    const out: { id: string; y1: number; y2: number }[] = []
    for (const it of items) {
      if (!it.checked) continue
      const l = document.querySelector(`[data-l="${CSS.escape(it.src)}"]`)
      const r = document.querySelector(`[data-r="${CSS.escape(it.src)}"]`)
      if (!l || !r) continue
      const a = l.getBoundingClientRect()
      const b = r.getBoundingClientRect()
      out.push({ id: it.src, y1: a.top + a.height / 2 - gr.top, y2: b.top + b.height / 2 - gr.top })
    }
    setCurves(out)
    setGw({ w: gr.width, h: gr.height })
  }, [plan, items, after, tick, view])

  const VIEW_TEXT = {
    plan: '',
    rules: '',
    map: ''
  }
  const header = (
    <div className="px-4 pb-3 pt-3">
      <div className="flex items-center">
        <Seg options={[['plan', 'Plan a folder'], ['rules', 'Watch rules'], ['map', 'Graph']]} value={view} onChange={setView} />
      </div>
      <div className="anim-rule h-px bg-line" />
      {view === 'plan' && (
        <>
          <Stepper step={busy === 'Applying…' ? 2 : plan ? 1 : 0} />
          <div className="anim-row mt-3 flex flex-wrap items-end gap-x-8 gap-y-3" style={stagger(2)}>
            <label className="min-w-[320px] max-w-[540px] flex-1">
              <span className="label mb-1 block">Folder</span>
              <input value={root} spellCheck={false} onChange={(e) => setRoot(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void suggest()} className="field mono w-full" placeholder="Folder to organize" />
            </label>
            <Ghost tone="plain" onClick={() => setRoot(cwd)} title="The folder open in Files">
              Use the folder in Files
            </Ghost>
            <Seg
              size="sm"
              options={[['both', 'Move and rename'], ['move', 'Move only'], ['rename', 'Rename only']]}
              value={mode}
              onChange={(m) => {
                setMode(m)
                if (plan) void suggest(false, m)
              }}
            />
            <span className="ml-auto" />
            {plan && (
              <Ghost tone="plain" onClick={() => void suggest(true)} disabled={!!busy} title="Ask a stronger model for a better tree">
                <Lightbulb size={13} strokeWidth={1.6} /> Think harder
              </Ghost>
            )}
            {plan ? (
              <button className="btn-primary !px-7 !py-2 disabled:opacity-40" disabled={!checkedCount || !!busy} onClick={() => void apply()}>
                Apply {checkedCount} change{checkedCount === 1 ? '' : 's'}
              </button>
            ) : (
              <button className="btn-primary !px-7 !py-2 disabled:opacity-40" disabled={!!busy || !root} onClick={() => void suggest()}>
                Suggest a plan
              </button>
            )}
          </div>
          {busy && (
            <div className="anim-fade mt-4 flex items-center gap-3">
              <span className="relative h-px w-32 overflow-hidden bg-line">
                <span className="absolute inset-y-0 w-1/3 bg-accent" style={{ animation: 'rv-scan 1s ease-in-out infinite' }} />
              </span>
              <span className="mono text-[12px] text-muted">{busy}</span>
            </div>
          )}
          {error && (
            <div className="anim-pop mt-4 flex items-start gap-2.5 border-l-2 border-danger py-1 pl-3 text-danger">
              <AlertCircle size={16} className="mt-0.5 shrink-0" />
              {error}
            </div>
          )}
          {plan && !busy && (
            <div className="anim-fade mt-4 flex flex-wrap items-center gap-x-6 gap-y-2">
              <Chip tone={plan.tier === 'T3' ? 'warn' : 'good'}>{plan.tier === 'rules' ? 'rules' : plan.tier === 'T3' ? 'cloud · redacted' : 'local'}</Chip>
              <Chip>{plan.tier === 'rules' ? `${plan.ms} ms` : `${plan.planner} · ${(plan.ms / 1000).toFixed(1)} s`}</Chip>
              <Chip>{items.length} changes</Chip>
              <Chip tone="good">nothing is deleted</Chip>
              <Chip tone="good">one undo</Chip>
              {stats && stats.total > 0 && <Sources stats={stats} />}
            </div>
          )}
        </>
      )}
    </div>
  )

  if (view !== 'plan')
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        {header}
        <div key={view} className="anim-fade flex min-h-0 flex-1 flex-col border-t border-line">
          {view === 'rules' ? <WatchRules /> : <MapView />}
        </div>
      </div>
    )

  const TRY = (useApp.getState().places?.folders ?? []).filter((f) => ['Downloads', 'Desktop', 'Documents', 'Pictures'].includes(f.label)).map((f) => f.path)
  const tryPaths = [...new Set([cwd, ...TRY])].filter(Boolean).slice(0, 4)
  const rootName = base(plan?.root ?? root) || plan?.root || root
  const topGroups = after.filter(([f]) => f !== '')
  const rootItems = after.find(([f]) => f === '')?.[1] ?? []
  const leftKids = plan?.before ?? []

  return (
    <div className="flex min-h-0 flex-1 flex-col border-t border-line">
      {header}

      {!plan && !busy && !error && (
        <div className="min-h-0 flex-1 overflow-y-auto border-t border-line">
          <div className="anim-fade px-4 py-3">
            <div>
              <div className="anim-row flex flex-wrap items-baseline gap-x-1 gap-y-1" style={stagger(0)}>
                <span className="label mr-3">Try it on</span>
                {tryPaths.map((p, i) => (
                  <span key={p} className="flex items-baseline">
                    <button
                      onClick={() => {
                        setRoot(p)
                        void suggestFor(p)
                      }}
                      className="link py-0.5 transition-colors hover:text-accent"
                    >
                      {base(p) || p}
                    </button>
                    {i < tryPaths.length - 1 && <span className="px-2 text-muted/60">/</span>}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {plan && (
        <div ref={wrap} className="min-h-0 flex-1 overflow-y-auto border-t border-line px-4 py-5">
          <div className="grid grid-cols-[minmax(0,1fr)_130px_minmax(0,1.25fr)]">
            {/* NOW */}
            <div>
              <div className="mb-3 flex items-baseline gap-3">
                <span className="label">Now</span>
                <span className="anim-rule h-px flex-1 bg-line" />
                <span className="mono text-[11px] text-muted">{plan.before.length} items</span>
              </div>
              <div className="mb-1 flex items-center gap-2">
                <span className="inline-block h-[9px] w-[9px] rotate-45 border border-muted" />
                <span className="serif text-[14px] leading-none">{rootName}</span>
              </div>
              <div className="ml-[4px]">
                {leftKids.map((b, k) => {
                  const moves = changed.has(b.path)
                  return (
                    <div
                      key={b.path}
                      data-l={b.path}
                      style={stagger(k)}
                      onMouseEnter={() => setHot(b.path)}
                      onMouseLeave={() => setHot(null)}
                      className={`anim-row flex h-[30px] items-stretch ${hot === b.path ? 'bg-select' : ''}`}
                    >
                      <TreeGuides trail={[]} last={k === leftKids.length - 1} />
                      <span className="flex min-w-0 items-center gap-2 pl-1.5">
                        <FileIcon ext={ext(b.name)} isDir={b.is_dir} size={14} />
                        <span className={`truncate ${moves ? 'text-text' : 'text-muted'}`}>{b.name}</span>
                        {moves && <span className="h-[5px] w-[5px] shrink-0 rounded-full bg-accent" title="This item moves" />}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* The curves */}
            <div ref={gutter} className="relative">
              <svg width={gw.w} height={gw.h} className="absolute inset-0 overflow-visible" aria-hidden>
                {curves.map((c, k) => {
                  const w = gw.w
                  const on = hot === c.id
                  return (
                    <g key={c.id}>
                      <path
                        d={`M 0 ${c.y1} C ${w * 0.55} ${c.y1}, ${w * 0.45} ${c.y2}, ${w} ${c.y2}`}
                        fill="none"
                        pathLength={1}
                        className="draw stroke-accent"
                        strokeWidth={on ? 2 : 1.1}
                        strokeOpacity={hot && !on ? 0.12 : on ? 1 : 0.5}
                        style={{ animationDelay: `${Math.min(k, 30) * 28 + 200}ms`, transition: 'stroke-width 160ms, stroke-opacity 160ms' }}
                      />
                      <circle cx={0} cy={c.y1} r={on ? 3.5 : 2.2} className="fill-accent" opacity={hot && !on ? 0.2 : 1} />
                      <circle cx={w} cy={c.y2} r={on ? 3.5 : 2.2} className="fill-accent" opacity={hot && !on ? 0.2 : 1} />
                    </g>
                  )
                })}
              </svg>
            </div>

            {/* AFTER */}
            <div>
              <div className="mb-3 flex items-baseline gap-3">
                <span className="label text-accent">After</span>
                <span className="anim-rule h-px flex-1 bg-line" />
                
              </div>
              <div className="mb-1 flex items-center gap-2">
                <span className="inline-block h-[9px] w-[9px] rotate-45 border border-accent bg-accent" />
                <span className="serif text-[14px] leading-none">{rootName}</span>
              </div>
              <div className="ml-[4px]">
                {topGroups.map(([folder, entries], gi) => {
                  const isNew = !plan.before.some((b) => b.is_dir && b.name === folder)
                  const lastGroup = gi === topGroups.length - 1 && rootItems.length === 0
                  return (
                    <div
                      key={folder}
                      onDragOver={(e) => {
                        e.preventDefault()
                        setDragOver(folder)
                      }}
                      onDragLeave={() => setDragOver(null)}
                      onDrop={(e) => {
                        e.preventDefault()
                        setDragOver(null)
                        const src = e.dataTransfer.getData('text/raven-organize')
                        if (src) setItem(src, { folder, checked: true })
                      }}
                      className={`transition-colors duration-200 ${dragOver === folder ? 'bg-accent-soft outline-dashed outline-1 outline-accent' : ''}`}
                    >
                      <div style={stagger(gi)} className="anim-row flex h-[34px] items-stretch">
                        <TreeGuides trail={[]} last={lastGroup && entries.length === 0} />
                        <span className="flex min-w-0 items-center gap-2 pl-1.5">
                          <FileIcon ext="" isDir size={16} />
                          <span className="serif text-[19px] leading-none">{folder}</span>
                          {isNew && <Chip tone="good">new</Chip>}
                          <span className="mono text-[11px] text-muted">{entries.filter((x) => x.item?.checked).length || ''}</span>
                        </span>
                      </div>
                      {entries.map((x, j) => (
                        renderItem(x, [!lastGroup], j === entries.length - 1)
                      ))}
                    </div>
                  )
                })}
                {rootItems.map((x, j) => (
                  renderItem(x, [], j === rootItems.length - 1)
                ))}
              </div>
              {plan.skipped.length > 0 && (
                <div className="mt-4">
                  <div className="mb-2 flex items-baseline gap-3">
                    <span className="label">Stays as it is</span>
                    <span className="h-px flex-1 bg-line" />
                  </div>
                  {plan.skipped.map((sk) => (
                    <div key={sk.path} className="py-[2px] text-[12.5px] text-muted">
                      <span className="text-text">{base(sk.path)}</span>: {sk.reason}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
      {plan && (
        <div className="flex items-center gap-4 border-t border-line px-4 py-2 text-[12px] text-muted">
          <span className="ml-auto" />
          <Ghost tone="plain" onClick={() => void navigate(plan.root)}>
            Open the folder in Files
          </Ghost>
        </div>
      )}
    </div>
  )

  // One file in the "after" tree.
  function renderItem(x: { item?: Item; stay?: string }, trail: boolean[], last: boolean) {
    if (!x.item)
      return (
        <div key={`stay-${x.stay}`} className="flex h-[30px] items-stretch">
          <TreeGuides trail={trail} last={last} draw={false} />
          <span className="flex min-w-0 items-center gap-2 pl-1.5 text-muted">
            <FileIcon ext={ext(x.stay ?? '')} isDir={false} size={14} />
            <span className="truncate">{x.stay}</span>
            <span className="mono text-[10.5px]">stays</span>
          </span>
        </div>
      )
    const it = x.item
    return (
      <div
        key={it.src}
        data-r={it.src}
        draggable
        onDragStart={(e) => e.dataTransfer.setData('text/raven-organize', it.src)}
        onMouseEnter={() => setHot(it.src)}
        onMouseLeave={() => setHot(null)}
        className={`group flex min-h-[44px] items-stretch transition-colors ${hot === it.src ? 'bg-select' : ''}`}
      >
        <TreeGuides trail={trail} last={last} />
        <span className="flex min-w-0 flex-1 items-start gap-2.5 py-1.5 pl-1.5">
          <input type="checkbox" checked={it.checked} onChange={(e) => setItem(it.src, { checked: e.target.checked })} className="mt-[3px] shrink-0" />
          <FileIcon ext={ext(it.name)} isDir={false} size={14} />
          <span className="min-w-0 flex-1">
            {editing === it.src ? (
              <input
                autoFocus
                defaultValue={it.name}
                spellCheck={false}
                onBlur={(e) => {
                  setItem(it.src, { name: e.target.value.trim() || it.name })
                  setEditing(null)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                  if (e.key === 'Escape') setEditing(null)
                }}
                className="field w-full !py-0"
              />
            ) : (
              <span className={`flex items-baseline gap-2 truncate ${it.checked ? 'text-text' : 'text-muted'}`} onDoubleClick={() => setEditing(it.src)} title="Double-click to rename">
                <span className="truncate">{it.name}</span>
                {it.name !== base(it.src) && <span className="truncate text-[12px] text-muted line-through">{base(it.src)}</span>}
                <PenLine size={11} className="shrink-0 text-muted opacity-0 transition-opacity group-hover:opacity-100" />
              </span>
            )}
            <span className="block truncate text-[12px] text-muted">{it.reason}</span>
          </span>
          {it.category && <span className="serif shrink-0 pr-2 text-[15px]  leading-none text-accent">{it.category}</span>}
        </span>
      </div>
    )
  }
}

/** Three steps: pick, review, apply. A line fills between them as you go. */
function Stepper({ step }: { step: number }) {
  const steps = ['Pick a folder', 'Review the plan', 'Apply']
  return (
    <div className="anim-row mt-3 flex items-center" style={stagger(1)}>
      {steps.map((label, i) => (
        <div key={label} className="flex items-center">
          <div className="flex items-baseline gap-2.5">
            <span className={`serif text-[14px]  leading-none transition-colors duration-500 ${i <= step ? 'text-accent' : 'text-muted/60'}`}>{i < step ? '✓' : i + 1}</span>
            <span className={`transition-colors duration-500 ${i === step ? 'text-text' : 'text-muted'}`}>{label}</span>
          </div>
          {i < steps.length - 1 && (
            <span className="mx-5 h-px w-20 bg-line">
              <span className="block h-px origin-left bg-accent transition-transform duration-700 ease-[cubic-bezier(0.3,0.7,0.2,1)]" style={{ transform: `scaleX(${i < step ? 1 : 0})` }} />
            </span>
          )}
        </div>
      ))}
    </div>
  )
}

const SOURCE: Record<string, [string, string]> = {
  rule: ['Rules', 'var(--k-code)'],
  learned: ['Learned from you', 'var(--k-audio)'],
  embedding: ['Meaning', 'var(--k-video)'],
  llm: ['Local model', 'var(--k-folder)'],
  user: ['Your choices', 'var(--k-image)']
}

/** How the categories were decided, as one thin bar. */
function Sources({ stats }: { stats: Stats }) {
  const parts = Object.entries(stats.share).filter(([k, v]) => SOURCE[k] && v > 0)
  if (!parts.length) return null
  return (
    <span className="ml-auto flex items-center gap-4" title="How Raven decided each file's category">
      <span className="flex h-[3px] w-28 overflow-hidden bg-line">
        {parts.map(([k, v]) => (
          <span key={k} style={{ width: `${v}%`, background: SOURCE[k][1] }} />
        ))}
      </span>
      <span className="flex items-center gap-3 text-[11px] text-muted">
        {parts.map(([k, v]) => (
          <span key={k} className="flex items-center gap-1">
            <span className="inline-block h-[6px] w-[6px]" style={{ background: SOURCE[k][1] }} />
            {SOURCE[k][0]} {v}%
          </span>
        ))}
        {stats.done < stats.total && <span>· {stats.total - stats.done} waiting</span>}
      </span>
    </span>
  )
}

/** The last folder that was mapped. It stays while the app runs, so a tab switch keeps the graph. */
let lastMapped = ''

/**
 * The graph (PLAN2 P2-7) of one folder that the user names. Raven reads only that folder: nothing is
 * built for the whole disk. "Links" shows the tree and the links in the text. "By meaning" shows files
 * with close meaning (indexed files only) and files with the same content.
 */
function MapView() {
  const here = useApp((s) => s.path)
  const home = useApp((s) => s.places?.home ?? '')
  const [layout, setLayout] = useState<'links' | 'meaning'>('links')
  const [typed, setTyped] = useState(lastMapped ? tildify(lastMapped, home) : '')
  const [path, setPath] = useState(lastMapped)
  const map = (p: string): void => {
    const full = p.trim().replace(/^~(?=\/|$)/, home).replace(/\/+$/, '') || '/'
    if (!p.trim()) return
    lastMapped = full
    setPath(full)
    setTyped(tildify(full, home))
  }
  const browse = async (): Promise<void> => {
    const picked = (await pickFolder('A folder to map'))[0]
    if (picked) map(picked)
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-4 pt-3">
        <form
          className="flex min-w-[320px] flex-1 items-center gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            map(typed)
          }}
        >
          <input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => e.stopPropagation()}
            spellCheck={false}
            placeholder="A folder to map, like ~/Documents/Sem5"
            className="field mono w-full max-w-[560px]"
          />
          <button type="submit" className="btn-primary" disabled={!typed.trim()}>
            Map it
          </button>
          <Ghost tone="plain" onClick={() => void browse()}>
            Browse
          </Ghost>
        </form>
        {path && <Seg size="sm" options={[['links', 'Links'], ['meaning', 'By meaning']]} value={layout} onChange={setLayout} />}
      </div>
      <div className="mt-2 flex min-h-0 flex-1 flex-col">
        {path ? (
          <FileGraph
            key={layout}
            path={path}
            onPath={map}
            start={layout === 'links' ? ['folder', 'ref', 'mention', 'same'] : ['similar', 'same']}
          />
        ) : (
          <div className="anim-fade flex flex-1 flex-col items-center justify-center gap-3 text-center">
            <div className="serif text-[20px]">Map one folder</div>
            <p className="max-w-[420px] text-muted">Raven reads only the folder that you name, and links its files by their content.</p>
            {here && (
              <button className="link mono text-[12px] text-accent" onClick={() => map(here)}>
                Map {tildify(here, home)}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
