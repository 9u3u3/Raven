import { ArrowUp, Check, ChevronRight, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { create } from 'zustand'
import AutoRules from '../components/AutoRules'
import SizeChart from '../components/SizeChart'
import { ApiError, get, on, post } from '../lib/api'
import { basename, date, size, tildify } from '../lib/format'
import type { CompressJob, DupeGroup, Place, SpaceNode, SpaceStatus, Suggestion } from '../lib/types'
import { EmptyState } from '../components/EmptyState'
import { Chip, Ghost, Seg, SectionTitle, Track, TreeGuides, stagger, useCountUp, useGrown } from '../components/ui'
import Reclaim from '../components/Reclaim'
import { compressRestore, compressRun, goto, nameFiles, openOrganize, openReview, reveal, say, setScreen, trashFromReview, useApp } from '../store'

const CATS = ['Video', 'Photos', 'Documents', 'Archives', 'Installers', 'Code dependencies', 'Caches', 'Other']
const AGES = ['Opened in the last month', '1 to 6 months', '6 to 12 months', 'Over 12 months']
type Tab = 'reclaim' | 'suggestions' | 'duplicates' | 'largest' | 'compressed' | 'rules'

type LargestItem = { path: string; name: string; size: number; mtime: number; atime: number; cat: number }

// Space state lives outside the component, so it stays when you switch screens.
const useSpace = create<{
  path: string
  mode: 'treemap' | 'sunburst'
  tab: Tab
  status: SpaceStatus | null
  tree: SpaceNode | null
  suggestions: Suggestion[]
  largest: LargestItem[]
  dupes: DupeGroup[]
  hover: string | null
  error: string | null
  jobs: CompressJob[]
  methods: Record<string, string>
}>(() => ({
  path: '',
  mode: 'treemap',
  tab: 'reclaim',
  status: null,
  tree: null,
  suggestions: [],
  largest: [],
  dupes: [],
  hover: null,
  error: null,
  jobs: [],
  methods: {}
}))
const setS = useSpace.setState

async function loadStatus(): Promise<SpaceStatus | null> {
  try {
    const st = await get<SpaceStatus>('/space/status')
    setS({ status: st })
    return st
  } catch {
    return null
  }
}

async function loadPath(path: string): Promise<void> {
  setS({ path })
  try {
    const [tree, sug, lg, du, jb] = await Promise.all([
      get<SpaceNode>('/space/tree', { path, depth: 2 }),
      get<{ rows: Suggestion[] }>('/space/suggestions', { path }),
      get<{ items: LargestItem[] }>('/space/largest', { path, limit: 60 }),
      get<{ groups: DupeGroup[] }>('/space/duplicates', { path }),
      get<{ jobs: CompressJob[] }>('/compress/jobs')
    ])
    setS({ tree, suggestions: sug.rows, largest: lg.items, dupes: du.groups, jobs: jb.jobs, error: null })
  } catch (e) {
    setS({ tree: null, error: e instanceof ApiError && e.status === 404 ? null : (e as Error).message })
  }
}

export function refreshSpace(): void {
  const p = useSpace.getState().path
  if (p) void loadPath(p)
  void loadStatus()
}

/** Open Space on a folder. Raven scans it when no scan covers it yet. */
export async function showInSpace(path: string): Promise<void> {
  setS({ path })
  goto('space')
  const st = await loadStatus()
  if (st?.roots.some((r) => path === r.path || path.startsWith(r.path + '/'))) await loadPath(path)
  else await scan(path)
}

async function scan(path: string): Promise<void> {
  try {
    await post('/space/scan', { path })
    setS({ path })
    void loadStatus()
  } catch (e) {
    say((e as Error).message, 'error')
  }
}


const CELLS = 48

/** One drive: the free space in large type, and a meter of square cells. Used cells are filled, the cells that
 * the plan frees are green, and the cells light up one by one when the screen opens. */
function DriveLine({ d, planFree, ghost, i, named }: { d: Place; planFree: number; ghost: number; i: number; named: boolean }) {
  const total = d.total ?? 0
  const free = d.free ?? 0
  const used = total - free
  const usedPct = total ? used / total : 0
  const usedCells = Math.round(usedPct * CELLS)
  const planCells = planFree && total ? Math.min(usedCells, Math.max(1, Math.round((planFree / total) * CELLS))) : 0
  const hot = usedPct > 0.9
  return (
    <button
      onClick={() => void scan(d.path)}
      title={`Scan ${d.path}`}
      style={stagger(i + 2)}
      className="anim-row group min-w-[280px] flex-1 border-l border-line pl-6 text-left"
    >
      <div className="flex items-baseline gap-3">
        <span className="serif text-[26px] leading-none tabular-nums">{size(free)}</span>
        <span className="text-muted">free of {size(total)}</span>
        <span className={`mono ml-auto text-[12px] ${hot ? 'text-danger' : 'text-text'}`}>{Math.round(usedPct * 100)}%</span>
      </div>
      <div className="mt-3 grid h-[16px] gap-[2px]" style={{ gridTemplateColumns: `repeat(${CELLS}, minmax(0, 1fr))` }}>
        {Array.from({ length: CELLS }, (_, k) => {
          const plan = k >= usedCells - planCells && k < usedCells && planCells > 0
          const bg = k < usedCells ? (plan ? 'var(--ok)' : hot ? 'var(--danger)' : 'var(--accent)') : 'var(--line)'
          return <span key={k} style={{ background: bg, animation: `rv-cell 380ms cubic-bezier(0.2,0.8,0.2,1) ${i * 120 + k * 14}ms both` }} />
        })}
      </div>
      <div className="mono mt-2 flex flex-wrap gap-x-4 text-[11px] text-muted">
        {named && <span>{d.label}</span>}
        {planFree > 0 && <span className="text-ok">+{size(planFree)} after the plan</span>}
        {ghost > 0 && <span title={`If you restore all archives, you need ${size(ghost)} more`}>{size(ghost)} ghost</span>}
        <span className="link-g text-accent opacity-0 transition-opacity duration-200 group-hover:opacity-100">scan this drive →</span>
      </div>
    </button>
  )
}

/** The scan state, small: a dot, the file count on hover, and a rescan button. */
function ScanState({ status, scanned, home, error }: { status: SpaceStatus | null; scanned?: SpaceStatus['roots'][number]; home: string; error: string | null }) {
  if (error) return <span className="text-[12px] text-danger">{error}</span>
  if (status?.scanning)
    return (
      <span className="flex items-center gap-2 text-[12px] text-muted">
        <span className="relative h-[2px] w-16 overflow-hidden bg-line">
          <span className="absolute inset-y-0 w-1/3 bg-accent" style={{ animation: 'rv-scan 1s ease-in-out infinite' }} />
        </span>
        <span className="mono">{status.files.toLocaleString()} files</span>
      </span>
    )
  if (!scanned) return <Ghost onClick={() => void scan(home)}>Scan the home folder</Ghost>
  return (
    <span className="flex items-center gap-2 text-[12px] text-muted" title={`${tildify(scanned.path, home)} · ${scanned.files.toLocaleString()} files in ${scanned.seconds.toFixed(0)} s`}>
      <span className="h-[6px] w-[6px] bg-ok" />
      {date(scanned.finished)}
      <button className="icon-btn !h-7 !w-7" title="Rescan" aria-label="Rescan" onClick={() => void scan(scanned.path)}>
        <RefreshCw size={13} strokeWidth={1.6} />
      </button>
    </span>
  )
}

function Breadcrumb({ tree, home }: { tree: SpaceNode; home: string }) {
  const root = tree.scan_root ?? tree.path
  const parts: { label: string; path: string }[] = [{ label: tildify(root, home), path: root }]
  if (tree.path !== root) {
    let cur = root
    for (const p of tree.path.slice(root.length).split('/').filter(Boolean)) {
      cur = `${cur.replace(/\/$/, '')}/${p}`
      parts.push({ label: p, path: cur })
    }
  }
  return (
    <div className="mono flex min-w-0 items-baseline gap-0.5 truncate text-[12.5px]">
      <span className="sec-no">02</span>
      {parts.map((p, i) => (
        <span key={p.path} className="flex items-baseline">
          {i > 0 && <span className="serif px-1.5 text-[17px]  leading-none text-muted/60">/</span>}
          <button className={`link py-0.5 ${i === parts.length - 1 ? 'text-text' : 'text-muted hover:text-text'}`} onClick={() => void loadPath(p.path)}>
            {p.label}
          </button>
        </span>
      ))}
      <span className="serif ml-4 text-[14px] leading-none text-accent">{size(tree.size)}</span>
    </div>
  )
}

/** Where the space went, as a tree: the biggest folders on branches, and the biggest things inside them. */
function Outline({ tree, hover }: { tree: SpaceNode; hover: string | null }) {
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [kids, setKids] = useState<Record<string, SpaceNode[]>>({})
  const [more, setMore] = useState<Set<string>>(new Set())
  // A new folder or a new scan starts the outline again.
  useEffect(() => {
    setOpen(new Set())
    setKids({})
    setMore(new Set())
  }, [tree.path, tree.scanned])

  const sorted = (xs: SpaceNode[]): SpaceNode[] => [...xs].sort((a, b) => b.size - a.size)
  const childrenOf = (n: SpaceNode): SpaceNode[] => sorted(kids[n.path] ?? n.children ?? [])
  const toggle = (n: SpaceNode): void => {
    setOpen((o) => {
      const x = new Set(o)
      if (x.has(n.path)) x.delete(n.path)
      else x.add(n.path)
      return x
    })
    if (!n.children?.length && !kids[n.path])
      get<SpaceNode>('/space/tree', { path: n.path, depth: 1 })
        .then((t) => setKids((k) => ({ ...k, [n.path]: t.children ?? [] })))
        .catch(() => setKids((k) => ({ ...k, [n.path]: [] })))
  }
  const toggleMore = (key: string): void =>
    setMore((m) => {
      const x = new Set(m)
      if (x.has(key)) x.delete(key)
      else x.add(key)
      return x
    })

  const LIMIT_TOP = 8
  const LIMIT_SUB = 6
  let k = 0
  const viewMore = (key: string, total: number, limit: number, trail: boolean[]) => {
    const all = more.has(key)
    return (
      <div key={`more:${key}`} className="flex h-[30px] items-stretch">
        <TreeGuides trail={trail} last step={16} draw={false} />
        <button className="link pl-6 text-[12px] text-accent" onClick={() => toggleMore(key)}>
          {all ? 'View less' : `View more · ${total - limit} more`}
        </button>
      </div>
    )
  }
  const row = (n: SpaceNode, trail: boolean[], last: boolean, depth: number, denom: number): React.ReactNode => {
    const hot = hover && (n.path === hover || n.path.startsWith(hover + '/'))
    const go = n.is_dir && !n.rest
    const isOpen = open.has(n.path)
    const list = isOpen ? childrenOf(n) : []
    const shown = more.has(n.path) ? list : list.slice(0, LIMIT_SUB)
    const hasMore = list.length > LIMIT_SUB
    const loading = isOpen && !n.children?.length && kids[n.path] === undefined
    return (
      <div key={`${n.path}:${n.rest ? 'rest' : ''}`}>
        <div
          style={stagger(k++)}
          onMouseEnter={() => setS({ hover: n.path })}
          onMouseLeave={() => setS({ hover: null })}
          className={`anim-row group flex h-[34px] items-stretch transition-colors ${hot ? 'bg-select' : ''}`}
        >
          <TreeGuides trail={trail} last={last} step={16} draw={false} />
          <button
            aria-label={isOpen ? 'Collapse' : 'Expand'}
            onClick={() => go && toggle(n)}
            className={`grid w-4 shrink-0 place-content-center text-muted transition-colors hover:text-text ${go ? '' : 'invisible'}`}
          >
            <ChevronRight size={11} strokeWidth={2} className={`transition-transform duration-200 ${isOpen ? 'rotate-90' : ''}`} />
          </button>
          <button onClick={() => go && void loadPath(n.path)} title={go ? 'Open this folder in the chart' : n.name} className={`flex min-w-0 flex-1 items-center pl-1 text-left ${go ? 'cursor-pointer' : 'cursor-default'}`}>
            <span className={`truncate transition-all duration-200 ${depth ? 'text-[13px] text-text/80' : 'text-[14px] font-medium'} ${n.rest ? 'text-muted' : go ? 'group-hover:translate-x-0.5 group-hover:text-accent' : ''}`}>{n.rest ? 'Small files' : n.name}</span>
          </button>
          <span className="flex w-[84px] shrink-0 items-center">
            <Track pct={(n.size / Math.max(denom, 1)) * 100} color={depth ? 'var(--k-doc)' : 'var(--accent)'} h={3} />
          </span>
          <span className="mono w-[68px] shrink-0 self-center text-right text-[12px] text-text/75">{size(n.size)}</span>
        </div>
        {isOpen && (
          <div className="anim-fade">
            {loading && <div className="flex h-[26px] items-center pl-12 text-[12px] text-muted">Reading…</div>}
            {shown.map((c, i) => row(c, [...trail, !last], i === shown.length - 1 && !hasMore, depth + 1, n.size))}
            {hasMore && viewMore(n.path, list.length, LIMIT_SUB, [...trail, !last])}
          </div>
        )}
      </div>
    )
  }

  const top = sorted(tree.children ?? [])
  const shownTop = more.has('top') ? top : top.slice(0, LIMIT_TOP)
  const biggest = top[0]?.size ?? 1
  return (
    <div className="absolute inset-0 flex min-w-0 flex-col">
      <div className="label mb-3"><span className="sec-no">03</span>Where it went</div>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain pr-2">
        <div className="mb-0.5 flex items-center gap-2 py-1">
          <span className="inline-block h-[8px] w-[8px] rotate-45 border border-accent bg-accent" />
          <span className="text-[15px] font-semibold">{tree.name || basename(tree.path)}</span>
          <span className="mono ml-auto text-[12px] text-text/75">{size(tree.size)}</span>
        </div>
        <div className="ml-[3px]">
          {top.length === 0 && <p className="py-2 text-muted">Nothing is inside this folder.</p>}
          {shownTop.map((n, i) => row(n, [], i === shownTop.length - 1 && top.length <= LIMIT_TOP, 0, biggest))}
          {top.length > LIMIT_TOP && viewMore('top', top.length, LIMIT_TOP, [])}
        </div>
      </div>
    </div>
  )
}

/** One stacked bar that grows in, and a small legend under it. Hover a part to see its size. */
function Breakdown({ no, title, hint, labels, values, colors }: { no?: string; title: string; hint?: string; labels: string[]; values: number[]; colors: string[] }) {
  const on = useGrown()
  const [hot, setHot] = useState<number | null>(null)
  const total = values.reduce((a, b) => a + b, 0)
  const parts = labels.map((l, i) => ({ l, i, v: values[i] ?? 0 })).filter((x) => x.v > 0)
  return (
    <div>
      <div className="mb-3 flex items-baseline justify-between">
        <span className="label">{no && <span className="sec-no">{no}</span>}{title}</span>
        <span className="text-[12px] text-muted">{hot !== null ? `${labels[hot]} · ${size(values[hot])}` : hint}</span>
      </div>
      <div className="flex h-[14px] w-full gap-[2px] overflow-hidden">
        {parts.map((x, k) => (
          <span
            key={x.l}
            onMouseEnter={() => setHot(x.i)}
            onMouseLeave={() => setHot(null)}
            className={`h-full transition-[flex-grow,opacity] duration-[1000ms] ease-[cubic-bezier(0.3,0.7,0.2,1)] ${hot !== null && hot !== x.i ? 'opacity-35' : ''}`}
            style={{ flexGrow: on ? x.v / Math.max(total, 1) : 0, flexBasis: 0, minWidth: 4, background: colors[x.i], transitionDelay: `${k * 60}ms` }}
          />
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5">
        {parts.map((x) => (
          <span
            key={x.l}
            onMouseEnter={() => setHot(x.i)}
            onMouseLeave={() => setHot(null)}
            className={`flex items-center gap-1.5 text-[12px] transition-opacity ${hot !== null && hot !== x.i ? 'opacity-40' : ''}`}
          >
            <span className="h-[8px] w-[8px]" style={{ background: colors[x.i] }} />
            {x.l}
            <span className="mono text-[11px] text-muted">{total ? Math.round((x.v / total) * 100) : 0}%</span>
          </span>
        ))}
      </div>
    </div>
  )
}

function reviewSuggestion(s: Suggestion): void {
  openReview({
    title: s.sentence,
    reversible: true,
    cloudBytes: 0,
    frees: true,
    steps: [{ label: 'Move to trash', files: s.items.map((i) => ({ ...i })) }],
    run: async (steps) => {
      await trashFromReview(steps, 'Clean up')
      refreshSpace()
    }
  })
}

/** Similar photos are different files: nothing is checked, the user picks what goes (section 9.7 step 4). */
function reviewNear(groups: DupeGroup[]): void {
  const files = groups.flatMap((g) =>
    g.files.filter((f) => !f.keep).map((f) => ({ path: f.path, size: f.size, checked: false, note: `${f.pixels ?? ''}, looks like ${g.files.find((x) => x.keep)?.path.split('/').pop()} (${g.files.find((x) => x.keep)?.pixels ?? ''})` }))
  )
  openReview({
    title: `${groups.length} sets of similar photos. Check the photos to move to the trash. The one with the highest resolution of each set stays.`,
    reversible: true,
    cloudBytes: 0,
    frees: true,
    steps: [{ label: 'Move similar photos to trash', files }],
    run: async (steps) => {
      await trashFromReview(steps, 'Remove similar photos')
    }
  })
}

function reviewDupes(groups: DupeGroup[]): void {
  const files = groups.flatMap((g) =>
    g.files.filter((f) => !f.keep).map((f) => ({ path: f.path, size: f.size, checked: true, note: `Same as ${g.files.find((x) => x.keep)?.path.split('/').pop()}` }))
  )
  openReview({
    title: `One copy of ${groups.length} files stays. The rest go to the trash.`,
    reversible: true,
    cloudBytes: 0,
    frees: true,
    steps: [{ label: 'Move duplicate copies to trash', files }],
    run: async (steps) => {
      await trashFromReview(steps, 'Remove duplicates')
      refreshSpace()
    }
  })
}

const METHOD_LABEL: Record<string, string> = {
  '7z': '.7z',
  'tar.zst': '.tar.zst',
  zip: '.zip',
  zst: 'zstd',
  jxl: 'JPEG XL',
  avif: 'AVIF (lossy)',
  'jxl-q': 'JPEG XL (lossy)',
  hevc: 'H.265 (lossy)'
}
const LOSSY = new Set(['avif', 'jxl-q', 'hevc'])
const METHOD_SHORT: Record<string, string> = { jxl: 'JXL', avif: 'AVIF', 'jxl-q': 'JXL lossy' }

/** The row sentence for the method that the user picked. */
function compressSentence(s: Suggestion, method: string): string {
  if (LOSSY.has(method) && s.method === 'jxl') {
    return s.sentence.replace(/Convert to lossless JPEG XL\..*$/, `Convert to ${METHOD_LABEL[method]}. This loses some quality.`)
  }
  return s.sentence.replace(/Archive to \.7z\./, `Archive to ${METHOD_LABEL[method]}.`)
}

function reviewCompress(s: Suggestion, method: string): void {
  const archive = method === '7z' || method === 'tar.zst' || method === 'zip'
  const lossy = LOSSY.has(method)
  openReview({
    title: lossy
      ? `${s.items.length} file${s.items.length === 1 ? '' : 's'} to ${METHOD_LABEL[method]}. The new files are smaller but not the same data. Raven cannot make the exact original again.`
      : compressSentence(s, method),
    note: lossy ? 'Originals go to the trash. Undo restores them.' : undefined,
    reversible: !lossy,
    cloudBytes: 0,
    frees: true,
    steps: [
      {
        label: archive ? `Archive to ${METHOD_LABEL[method]}` : `Compress with ${METHOD_LABEL[method]}`,
        detail: lossy
          ? 'Raven checks each new file first. Then the original goes to the trash.'
          : 'Raven checks every hash first. Then the originals go to the trash.',
        files: s.items.map((i) => ({ ...i }))
      }
    ],
    run: async (steps) => {
      const files = steps[0].files
      const checked = files.filter((f) => f.checked).map((f) => f.path)
      if (!checked.length || !s.root) return
      // All files checked: archive the whole folder, so empty folders and folder times come back too.
      const paths = archive && checked.length === files.length && s.unit ? s.unit : checked
      await compressRun(s.root, paths, method, s.id, lossy)
      refreshSpace()
    }
  })
}

const ACTION_WORD: Record<string, string> = { compress: 'shrink', organize: 'organize', privacy: 'privacy', trash: 'clean up', name: 'name' }

function Suggestions({ rows }: { rows: Suggestion[] }) {
  const methods = useSpace((s) => s.methods)
  if (!rows.length) return <EmptyState art="chart" title="Nothing to clean up here" />
  return (
    <ol>
      {rows.map((r, k) => {
        const method = methods[r.id] ?? r.method ?? ''
        const reversible = r.action === 'compress' ? !LOSSY.has(method) : r.reversible
        return (
          <li key={r.id} style={stagger(k)} className="anim-row group relative grid grid-cols-[52px_minmax(0,1fr)_150px_120px] items-center gap-5 border-b border-line py-4">
            <span aria-hidden className="absolute -bottom-px left-0 h-px w-full origin-left scale-x-0 bg-accent transition-transform duration-500 group-hover:scale-x-100" />
            <span className="serif text-[16px]  leading-none text-muted transition-colors duration-300 group-hover:text-accent">{String(k + 1).padStart(2, '0')}</span>
            <div className="min-w-0">
              <div className="text-[14.5px] leading-snug">{r.action === 'compress' ? compressSentence(r, method) : r.sentence}</div>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-5 gap-y-1">
                <Chip tone={r.action === 'organize' || r.action === 'privacy' || r.action === 'name' ? 'plain' : reversible ? 'good' : 'bad'}>
                  {ACTION_WORD[r.action]} · {r.action === 'organize' || r.action === 'name' ? 'tidy' : r.action === 'privacy' ? 'personal data' : reversible ? 'reversible' : 'lossy'}
                </Chip>
                {r.action === 'compress' && (r.methods?.length ?? 0) > 1 && (
                  <Seg size="sm" options={r.methods!.map((m) => [m, METHOD_SHORT[m] ?? METHOD_LABEL[m]] as [string, string])} value={method} onChange={(m) => setS((st) => ({ methods: { ...st.methods, [r.id]: m } }))} />
                )}
              </div>
            </div>
            <div className="text-right">
              {r.bytes ? (
                <>
                  <div className="serif text-[18px] leading-none">{size(r.bytes)}</div>
                  <div className="mt-1 text-[11px] text-muted">{r.action === 'compress' ? 'saves about' : 'can be freed'}</div>
                </>
              ) : null}
            </div>
            <div className="text-right">
              <Ghost
                onClick={() =>
                  r.action === 'compress' ? reviewCompress(r, method) : r.action === 'organize' ? openOrganize(r.root!) : r.action === 'name' ? void nameFiles(r.root!) : r.action === 'privacy' ? setScreen('privacy') : reviewSuggestion(r)
                }
              >
                Review
              </Ghost>
            </div>
          </li>
        )
      })}
    </ol>
  )
}

const STATUS_TEXT: Record<CompressJob['status'], string> = {
  planned: 'planned', running: 'running', verifying: 'verifying', done: 'compressed', failed: 'failed',
  restored: 'restored', undone: 'undone', missing: 'archive missing'
}

function Compressed({ jobs }: { jobs: CompressJob[] }) {
  const home = useApp((s) => s.places?.home ?? '')
  if (!jobs.length) return <EmptyState art="chart" title="No compression jobs yet" />
  return (
    <ol>
      {jobs.map((j, k) => {
        const kept = j.size_before && j.size_after ? j.size_after / j.size_before : 1
        const bad = j.status === 'failed' || j.status === 'missing'
        return (
          <li key={j.id} style={stagger(k)} className="anim-row grid grid-cols-[96px_minmax(0,1fr)_210px_130px_90px] items-center gap-5 border-b border-line py-3.5">
            <span className="mono text-[11.5px] text-muted">{date(j.created)}</span>
            <div className="min-w-0">
              <div className="mono truncate text-[12.5px]" title={j.error ?? j.outputs.join('\n')} style={{ direction: 'rtl', textAlign: 'left' }}>
                <bdi>{j.outputs.length > 1 ? `${tildify(j.root, home)}/ · ${j.outputs.length} files` : tildify(j.outputs[0] ?? j.root, home)}</bdi>
              </div>
              <div className="mt-1 flex gap-4 text-[12px] text-muted">
                <span className="mono">{j.method}</span>
                <span>
                  {j.files} file{j.files === 1 ? '' : 's'}
                </span>
              </div>
            </div>
            <div>
              <div className="mono mb-1.5 flex justify-between text-[11px] text-muted">
                <span>{size(j.size_before)}</span>
                <span className="text-text">{j.size_after ? size(j.size_after) : ''}</span>
              </div>
              <Track pct={kept * 100} color="var(--accent)" />
              <div className="mono mt-1 text-right text-[10.5px] text-muted">{j.status === 'done' ? `ghost +${size(j.gain)}` : ''}</div>
            </div>
            <Chip tone={bad ? 'bad' : j.status === 'done' ? 'good' : 'plain'}>
              {STATUS_TEXT[j.status]}
              {j.reversible ? '' : ' · lossy'}
            </Chip>
            <div className="text-right">
              {j.status === 'done' && j.reversible && <Ghost onClick={() => void compressRestore(j).then(refreshSpace)}>Restore</Ghost>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}

function Duplicates({ groups, path, status }: { groups: DupeGroup[]; path: string; status: SpaceStatus | null }) {
  const d = status?.dupes
  const home = useApp((s) => s.places?.home ?? '')
  const exact = groups.filter((g) => g.kind !== 'near')
  const near = groups.filter((g) => g.kind === 'near')
  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center gap-x-7 gap-y-3">
        {d?.running ? (
          <div className="min-w-[300px] flex-1">
            <div className="mono mb-2 text-[11.5px] text-muted">
              finding duplicates · {d.stage} {d.total ? `${d.done.toLocaleString()} / ${d.total.toLocaleString()}` : d.done.toLocaleString()}
            </div>
            <Track pct={d.total ? (d.done / d.total) * 100 : 35} color="var(--accent)" />
          </div>
        ) : (
          <>
            {exact.length > 0 && (
              <button className="btn-primary" onClick={() => reviewDupes(exact)}>
                Review all copies · {size(exact.reduce((a, g) => a + g.wasted, 0))}
              </button>
            )}
            {near.length > 0 && <Ghost onClick={() => reviewNear(near)}>Review similar photos · {near.length} sets</Ghost>}
            <Ghost tone="plain" onClick={() => void post('/space/duplicates', { path }).then(loadStatus)}>
              <RefreshCw size={13} strokeWidth={1.6} /> {d?.finished ? 'Find again' : 'Find duplicates'} in {tildify(path, home)}
            </Ghost>
          </>
        )}
      </div>
      {!groups.length && d?.finished && !d.running && <EmptyState art="search" title="No duplicates here" />}
      {!groups.length && !d?.finished && !d?.running && <EmptyState art="search" title="Look for duplicates" />}
      <div className="flex flex-col gap-7">
        {groups.map((g, k) => (
          <div key={g.hash} style={stagger(k)} className="anim-row">
            <div className="mb-1 flex items-baseline gap-4">
              <span className="serif text-[16px] leading-none">{g.kind === 'near' ? `${g.count} similar photos` : `${g.count} copies`}</span>
              <span className="mono text-[11px] text-muted">{g.kind === 'near' ? `up to ${size(g.wasted)} to free` : `${size(g.size)} each · wastes ${size(g.wasted)}`}</span>
            </div>
            {g.files.map((f, j) => (
              <div key={f.path} onDoubleClick={() => reveal(f.path)} className="group flex h-[28px] items-stretch hover:bg-hover">
                <TreeGuides trail={[]} last={j === g.files.length - 1} />
                <span className={`mono flex-1 truncate self-center pl-1.5 text-[12px] ${f.keep ? 'text-text' : 'text-muted'}`} title={f.path}>
                  {tildify(f.path, home)}
                </span>
                <span className="mono w-[96px] shrink-0 self-center text-[11px] text-muted">{date(f.mtime)}</span>
                <span className="w-[210px] shrink-0 self-center text-[12px]">
                  {f.keep ? (
                    <span className="inline-flex items-center gap-1.5 text-ok">
                      <Check size={12} strokeWidth={2.4} /> keep · {f.reason}
                    </span>
                  ) : (
                    <span className="text-muted">{g.kind === 'near' ? `similar · ${f.pixels ?? ''}` : 'copy'}</span>
                  )}
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

function Largest({ items, hover }: { items: LargestItem[]; hover: string | null }) {
  const home = useApp((s) => s.places?.home ?? '')
  const max = Math.max(1, ...items.map((f) => f.size))
  if (!items.length) return <EmptyState art="chart" title="No files to rank yet" />
  return (
    <div>
      {items.map((f, k) => (
        <div
          key={f.path}
          onDoubleClick={() => reveal(f.path)}
          onMouseEnter={() => setS({ hover: f.path })}
          onMouseLeave={() => setS({ hover: null })}
          style={stagger(k)}
          title="Double-click to show in Files"
          className={`anim-row grid grid-cols-[14px_minmax(0,1.2fr)_minmax(0,1fr)_minmax(120px,1fr)_90px_110px] items-center gap-4 border-b border-line/70 px-1 py-2 transition-colors duration-100 ${hover && (f.path === hover || f.path.startsWith(hover + '/')) ? 'bg-select' : 'hover:bg-hover'}`}
        >
          <span className="inline-block h-[8px] w-[8px]" style={{ background: `var(--cat-${f.cat})` }} title={CATS[f.cat]} />
          <span className="truncate">{f.name}</span>
          <span className="mono truncate text-[11.5px] text-muted">{tildify(f.path.slice(0, f.path.lastIndexOf('/')), home)}</span>
          <Track pct={(f.size / max) * 100} color="var(--accent)" h={2} delay={Math.min(k, 12) * 30} />
          <span className="mono text-right text-[12px]">{size(f.size)}</span>
          <span className="mono text-right text-[11.5px] text-muted">{date(Math.max(f.atime, f.mtime))}</span>
        </div>
      ))}
    </div>
  )
}

export default function SpaceScreen() {
  const places = useApp((s) => s.places)
  const ready = useApp((s) => s.ready)
  const { path, mode, tab, status, tree, suggestions, largest, dupes, hover, error, jobs } = useSpace()
  const planFree = useApp((s) => s.planFree)
  const planPath = useApp((s) => s.planPath)
  const ghost = useApp((s) => s.ghost)
  // The plan frees space only on the drive that holds its files: the longest mount path that matches.
  const planDrive = useMemo(() => {
    const all = places ? [...places.drives, ...places.removable] : []
    return all
      .filter((d) => planPath === d.path || planPath.startsWith(d.path.replace(/\/$/, '') + '/'))
      .sort((a, b) => b.path.length - a.path.length)[0]?.path
  }, [places, planPath])
  // Ghost space per drive: each archive counts on the drive that holds it.
  const ghostByDrive = useMemo(() => {
    const out: Record<string, number> = {}
    const all = places ? [...places.drives, ...places.removable] : []
    for (const j of jobs.filter((x) => x.status === 'done')) {
      const p = j.outputs[0] ?? ''
      const d = all.filter((x) => p.startsWith(x.path.replace(/\/$/, '') + '/')).sort((a, b) => b.path.length - a.path.length)[0]
      if (d) out[d.path] = (out[d.path] ?? 0) + j.gain
    }
    return out
  }, [places, jobs, ghost])
  const home = places?.home ?? ''

  useEffect(() => {
    if (!ready) return
    void (async () => {
      const st = await loadStatus()
      const p = useSpace.getState().path || st?.roots[0]?.path || home
      if (st && !st.scanning && !st.roots.some((r) => p === r.path || p.startsWith(r.path + '/'))) await scan(p)
      else void loadPath(p)
    })()
    let t: ReturnType<typeof setTimeout> | null = null
    const offs = [
      on('space_progress', (m: { path: string; files: number; bytes: number }) =>
        setS((s) => ({ status: s.status ? { ...s.status, scanning: true, path: m.path, files: m.files, bytes: m.bytes } : s.status }))
      ),
      on('compress_changed', () => refreshSpace()),
      on('journal', () => {
        // Undo and redo can change compression records.
        void get<{ jobs: CompressJob[] }>('/compress/jobs').then((r) => setS({ jobs: r.jobs }))
      }),
      on('space_done', (m: { path: string }) => {
        void loadStatus()
        void loadPath(useSpace.getState().path || m.path)
      }),
      on('dupes_progress', () => {
        if (t) clearTimeout(t)
        t = setTimeout(() => {
          void loadStatus().then((st) => {
            if (st && !st.dupes.running) refreshSpace()
          })
        }, 300)
      })
    ]
    return () => offs.forEach((f) => f())
  }, [home, ready])

  const drill = useCallback((p: string) => void loadPath(p), [])
  const open = useCallback((p: string) => reveal(p), [])
  const onHover = useCallback((p: string | null) => setS({ hover: p }), [])
  const catColors = useMemo(() => Array.from({ length: 8 }, (_, i) => `var(--cat-${i})`), [])
  const scanned = status?.roots.find((r) => path === r.path || path.startsWith(r.path + '/'))

  const tabs: [Tab, string][] = [
    ['reclaim', 'Reclaim'],
    ['suggestions', `Suggestions${suggestions.length ? ` · ${suggestions.length}` : ''}`],
    ['duplicates', `Duplicates${dupes.length ? ` · ${dupes.length}` : ''}`],
    ['largest', 'Largest'],
    ['compressed', `Compressed${jobs.filter((j) => j.status === 'done').length ? ` · ${jobs.filter((j) => j.status === 'done').length}` : ''}`],
    ['rules', 'Rules']
  ]

  const drives = places ? places.drives.concat(places.removable) : []
  const planned = suggestions.reduce((a, r) => a + (r.bytes ?? 0), 0) + dupes.reduce((a, g) => a + g.wasted, 0)
  const headline = useCountUp(planned > 0 ? planned : tree?.size ?? 0)
  const folderName = tildify(path || home, home)
  const hs = size(Math.round(headline)) || '0 B'
  const [num, unit] = [hs.replace(/[^0-9.,]/g, ''), hs.replace(/[0-9.,\s]/g, '')]

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="px-5 pb-10 pt-5">
        {/* The headline: how much can be freed, and where the drives stand. */}
        <div className="panel ticks flex flex-wrap items-center gap-x-10 gap-y-5 px-7 py-6">
          <div className="relative min-w-[260px]">
            <div className="label anim-row mb-2" style={stagger(0)}>
              <span className="sec-no">01</span>
              {planned > 0 ? 'You can free up to' : 'This folder holds'}
            </div>
            <div className="anim-reveal flex items-baseline gap-2" style={stagger(1)}>
              <span className="serif text-[52px] leading-[0.85] tracking-tight">{num}</span>
              <span className="serif text-[22px] text-muted">{unit}</span>
            </div>
            <div className="anim-row mt-3 flex items-center gap-4" style={stagger(2)}>
              {planned > 0 ? (
                <Ghost onClick={() => setS({ tab: 'suggestions' })}>
                  {suggestions.length} suggestion{suggestions.length === 1 ? '' : 's'}
                  {dupes.length ? ` · ${dupes.length} duplicate set${dupes.length === 1 ? '' : 's'}` : ''}
                </Ghost>
              ) : (
                <span className="text-muted">in {folderName}</span>
              )}
            </div>
          </div>
          <div className="relative flex min-w-[320px] flex-1 flex-wrap gap-x-8 gap-y-5">
            {drives.map((d, k) => (
              <DriveLine key={d.path} d={d} i={k} named={drives.length > 1} planFree={d.path === planDrive ? planFree : 0} ghost={ghostByDrive[d.path] ?? 0} />
            ))}
          </div>
        </div>

        {tree ? (
          <div className="anim-row mt-5 grid grid-cols-[minmax(0,1fr)_360px] gap-5" style={stagger(6)}>
            <div className="panel ticks min-w-0 p-5">
              <div className="mb-4 flex items-center gap-4">
                <Breadcrumb tree={tree} home={home} />
                {tree.path !== (tree.scan_root ?? tree.path) && (
                  <Ghost tone="plain" onClick={() => void loadPath(tree.path.slice(0, tree.path.lastIndexOf('/')) || '/')}>
                    <ArrowUp size={12} /> Up
                  </Ghost>
                )}
                <span className="ml-auto" />
                <ScanState status={status} scanned={scanned} home={home} error={error} />
                <Seg size="sm" options={[['treemap', 'Blocks'], ['sunburst', 'Rings']]} value={mode} onChange={(m) => setS({ mode: m })} />
              </div>
              <div className="h-[50vh] min-h-[380px] overflow-hidden">
                <SizeChart tree={tree} mode={mode} onDrill={drill} onOpen={open} onHover={onHover} />
              </div>
            </div>
            <div className="panel relative min-h-[420px] p-5">
              <div className="absolute inset-5"><Outline tree={tree} hover={hover} /></div>
            </div>
          </div>
        ) : (
          <div className="mt-8">
            <ScanState status={status} scanned={scanned} home={home} error={error} />
            {!status?.scanning && (
              <EmptyState art="chart" title="No scan covers this folder yet">
                <Ghost onClick={() => void scan(path || home)}>Scan this folder</Ghost>
              </EmptyState>
            )}
          </div>
        )}

        {tree && (
          <div className="anim-row mt-5 grid grid-cols-2 gap-5" style={stagger(8)}>
            <div className="panel px-6 py-5"><Breakdown no="04" title="By category" labels={CATS} values={tree.cats ?? []} colors={catColors} /></div>
            <div className="panel px-6 py-5"><Breakdown no="05" title="By last use" hint="fresh to stale" labels={AGES} values={tree.ages ?? []} colors={['var(--accent)', 'var(--k-audio)', 'var(--k-folder)', 'var(--k-archive)']} /></div>
          </div>
        )}

        <div className="mb-6 mt-10">
          <Seg options={tabs} value={tab} onChange={(t) => setS({ tab: t })} />
          <div className="anim-rule h-px bg-line" />
        </div>
        <div key={tab} className="anim-fade">
          {tab === 'reclaim' && <Reclaim path={path || home} />}
          {tab === 'suggestions' && <Suggestions rows={suggestions} />}
          {tab === 'duplicates' && <Duplicates groups={dupes} path={path || home} status={status} />}
          {tab === 'largest' && <Largest items={largest} hover={hover} />}
          {tab === 'compressed' && <Compressed jobs={jobs} />}
          {tab === 'rules' && <AutoRules folder={path} />}
        </div>
      </div>
    </div>
  )
}
