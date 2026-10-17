// App state. One store keeps the Files screen, the command bar and the status line in step.

import { create } from 'zustand'
import { ApiError, get, post, send, stream } from './lib/api'
import { basename, dirname } from './lib/format'
import type { AskSection, CompressJob, CompressSummary, Conflict, ConflictChoice, ConsentRequest, CopyJob, Entry, IndexProgress, Journal, Listing, Places, PrivacyStatus, RouterResult, SearchResponse } from './lib/types'

export type Font = 'inter' | 'commit' | 'geist' | 'jetbrains' | 'plex' | 'system'
export const FONTS: [Font, string][] = [['inter', 'Inter'], ['commit', 'Commit Mono'], ['geist', 'Geist Mono'], ['jetbrains', 'JetBrains Mono'], ['plex', 'IBM Plex Mono'], ['system', 'System']]
export type Screen = 'files' | 'space' | 'organize' | 'privacy' | 'settings' | 'results' | 'answer' | 'ask'
export type SortKey = 'name' | 'size' | 'mtime' | 'kind'
export type Status = { text: string; kind: 'info' | 'error' | 'busy'; undo?: boolean; action?: { label: string; run: () => void } }
export type ReviewFile = { path: string; size: number; checked: boolean; note?: string; is_dir?: boolean }
export type ReviewStep = { label: string; files: ReviewFile[]; detail?: string; lossy?: boolean; permanent?: boolean; confirm?: boolean; n?: number }
/** One review view for every change (section 12.5). `run` gets the steps with the user's checks. */
export type Review = {
  title: string
  steps: ReviewStep[]
  reversible: boolean
  cloudBytes: number
  frees: boolean
  run: (steps: ReviewStep[]) => Promise<void>
  /** A muted line under the title, such as where the plan came from. */
  note?: string
  /** Called when the review closes: `ran` is false for a cancel (habits: a rejected suggestion). */
  onClose?: (ran: boolean) => void
}
export type Confirm = { title: string; body: string; action: string; danger?: boolean; run: () => void }

type State = {
  ready: boolean
  /** The sidecar did not start: the start screen shows this. */
  bootError: string | null
  version: string
  screen: Screen
  places: Places | null
  path: string
  listing: Listing | null
  loading: boolean
  error: string | null
  history: string[]
  histIdx: number
  sort: { key: SortKey; dir: 1 | -1 }
  view: 'list' | 'grid'
  showHidden: boolean
  selected: Set<string>
  anchor: string | null
  cursor: string | null
  inspector: boolean
  leftOpen: boolean
  /** The width of the left column in pixels; the user drags its edge. */
  leftWidth: number
  /** The width of the Inspector in pixels. */
  inspectorWidth: number
  clipboard: { mode: 'copy' | 'cut'; paths: string[] } | null
  renaming: string | null
  status: Status | null
  journal: Journal
  index: IndexProgress | null
  free: { free: number; total: number } | null
  cmdOpen: boolean
  cmdQuery: string
  results: SearchResponse | null
  latency: Record<string, number>
  overlay: boolean
  confirm: Confirm | null
  conflicts: { mode: 'copy' | 'move'; dst: string; items: Conflict[]; resolve: (c: Record<string, ConflictChoice> | null) => void } | null
  jobs: Record<string, CopyJob>
  /** Dual pane (F3): two Files tabs side by side. The active one is live, the other shows its snapshot. */
  dual: { left: number; right: number } | null
  /** The view inside Organize: a plan for a folder, the watch rules, or the file map (Galaxy). */
  organizeView: 'plan' | 'rules' | 'map'
  /** The first-run screen shows (section 12.7). */
  firstRun: boolean
  /** The encrypted index waits for its passphrase (secure.py). */
  locked: boolean
  /** Power user mode (section 9.13): administrator retries, system folders. */
  powerUser: boolean
  /** Paths in the batch rename dialog, or null when it is closed. */
  batchRename: string[] | null
  favorites: string[]
  recent: string[]
  dragPaths: string[]
  theme: 'system' | 'light' | 'dark'
  scale: number
  font: Font
  policies: Policy[]
  /** Full view (PLAN2 P2-5): the root folder in the left column, hidden files, system folders marked. */
  fullView: boolean
  editPath: boolean
  gridCols: number
  review: Review | null
  planFree: number
  planPath: string
  ghost: CompressSummary | null
  privacy: PrivacyStatus | null
  answer: Answer | null
  asks: AskSection[]
  pdfPage: { path: string; page: number } | null
  /** Open this audio or video file at this second (a video moment or a transcript citation). */
  mediaAt: { path: string; at: number } | null
  organizeRoot: string | null
  connectOpen: boolean
  connectUrl: string
  tabs: Tab[]
  activeTab: number
}

export type Answer = {
  question: string
  path: string
  think: boolean
  status: 'running' | 'consent' | 'done' | 'error'
  result?: RouterResult
  consent?: ConsentRequest
  error?: string
  started: number
}

const ls = {
  get<T>(k: string, d: T): T {
    try {
      const v = localStorage.getItem(`raven.${k}`)
      return v === null ? d : (JSON.parse(v) as T)
    } catch {
      return d
    }
  },
  set(k: string, v: unknown): void {
    try {
      localStorage.setItem(`raven.${k}`, JSON.stringify(v))
    } catch {
      /* storage blocked */
    }
  }
}

export const useApp = create<State>(() => ({
  ready: false,
  bootError: null,
  version: '',
  screen: 'files',
  places: null,
  path: '',
  listing: null,
  loading: false,
  error: null,
  history: [],
  histIdx: -1,
  sort: ls.get('sort', { key: 'name', dir: 1 }),
  view: ls.get('view', 'list'),
  showHidden: ls.get('showHidden', false),
  selected: new Set(),
  anchor: null,
  cursor: null,
  inspector: ls.get('inspector', false),
  leftOpen: ls.get('leftOpen', true),
  leftWidth: ls.get('leftWidth', 240),
  inspectorWidth: ls.get('inspectorWidth', 340),
  clipboard: null,
  renaming: null,
  status: null,
  journal: { undo: null, redo: null },
  index: null,
  free: null,
  cmdOpen: false,
  cmdQuery: '',
  results: null,
  latency: {},
  overlay: false,
  confirm: null,
  conflicts: null,
  jobs: {},
  batchRename: null,
  powerUser: false,
  locked: false,
  firstRun: false,
  dual: null,
  organizeView: 'plan',
  favorites: ls.get('favorites', [] as string[]),
  recent: ls.get('recent', [] as string[]),
  dragPaths: [],
  theme: ls.get('theme', 'system'),
  scale: ls.get('scale', 1),
  font: ls.get('font', 'inter'),
  policies: [],
  fullView: ls.get('fullView', false),
  editPath: false,
  gridCols: 1,
  review: null,
  planFree: 0,
  planPath: '',
  ghost: null,
  privacy: null,
  answer: null,
  asks: [],
  pdfPage: null,
  mediaAt: null,
  organizeRoot: null,
  connectOpen: false,
  connectUrl: '',
  tabs: [{ id: 1, snap: null, used: Date.now() }],
  activeTab: 1
}))

const set = useApp.setState
const st = useApp.getState

// ---------------------------------------------------------------- helpers

export function sortEntries(entries: Entry[], key: SortKey, dir: 1 | -1, showHidden: boolean): Entry[] {
  const coll = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })
  const list = showHidden ? entries.slice() : entries.filter((e) => !e.is_hidden)
  list.sort((a, b) => {
    // Folders first, in every sort order.
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
    let c = 0
    if (key === 'name') c = coll.compare(a.name, b.name)
    else if (key === 'size') c = (a.is_dir ? (a.dir_size ?? a.child_count ?? 0) : (a.size ?? 0)) - (b.is_dir ? (b.dir_size ?? b.child_count ?? 0) : (b.size ?? 0))
    else if (key === 'mtime') c = a.mtime - b.mtime
    else if (key === 'kind') c = coll.compare(a.kind, b.kind) || coll.compare(a.name, b.name)
    if (c === 0) c = coll.compare(a.name, b.name)
    return c * dir
  })
  return list
}

export function visibleEntries(): Entry[] {
  const s = st()
  if (!s.listing) return []
  return sortEntries(s.listing.entries, s.sort.key, s.sort.dir, s.showHidden)
}

let statusTimer: ReturnType<typeof setTimeout> | null = null

export function say(text: string, kind: Status['kind'] = 'info', extra: Partial<Status> = {}): void {
  if (statusTimer) clearTimeout(statusTimer)
  set({ status: { text, kind, ...extra } })
  if (kind !== 'busy') statusTimer = setTimeout(() => set({ status: null }), kind === 'error' ? 12000 : 15000)
}

function fail(e: unknown): void {
  const msg = e instanceof ApiError || e instanceof Error ? e.message : String(e)
  say(msg, 'error')
}

export function setLatency(key: string, ms: number): void {
  set((s) => ({ latency: { ...s.latency, [key]: Math.round(ms * 10) / 10 } }))
}

// ---------------------------------------------------------------- navigation

let listAbort: AbortController | null = null

export async function navigate(path: string, opts: { push?: boolean; select?: string; newTab?: boolean } = {}): Promise<void> {
  // A folder opens in a Files tab. The other tab keeps its work (section: tabs).
  if (opts.newTab) newTab('files')
  else if (st().screen !== 'files') goto('files')
  const push = opts.push ?? true
  listAbort?.abort()
  const ac = new AbortController()
  listAbort = ac
  set({ loading: true, error: null, screen: 'files' })
  const t0 = performance.now()
  try {
    const listing = await get<Listing>('/fs/list', { path, hidden: true }, ac.signal)
    setLatency('folder', performance.now() - t0)
    const s = st()
    let history = s.history
    let histIdx = s.histIdx
    if (push && listing.path !== s.history[s.histIdx]) {
      history = [...s.history.slice(0, s.histIdx + 1), listing.path]
      histIdx = history.length - 1
    }
    const keep = opts.select ? new Set([opts.select]) : new Set<string>()
    const recent = [listing.path, ...s.recent.filter((p) => p !== listing.path)].slice(0, 8)
    ls.set('recent', recent)
    set({
      listing,
      path: listing.path,
      history,
      histIdx,
      loading: false,
      selected: keep,
      anchor: opts.select ?? null,
      cursor: opts.select ?? null,
      renaming: null,
      recent
    })
    send({ type: 'watch_dir', path: listing.path })
    void refreshFree(listing.path)
    // Frecency (section 6.1): a visit by a click or a key, not a reload of the same folder.
    if (push && !listing.archive) void post('/ui/visit', { path: listing.path }).catch(() => {})
  } catch (e) {
    if ((e as Error).name === 'AbortError') return
    set({ loading: false })
    fail(e)
  }
}

/** Reload the current folder and keep the selection where the items still exist. */
export async function reload(): Promise<void> {
  const s = st()
  if (!s.path) return
  try {
    const listing = await get<Listing>('/fs/list', { path: s.path, hidden: true })
    const names = new Set(listing.entries.map((e) => e.path))
    const selected = new Set([...st().selected].filter((p) => names.has(p)))
    set({ listing, selected, cursor: s.cursor && names.has(s.cursor) ? s.cursor : null })
  } catch (e) {
    // The folder itself is gone: go to the nearest parent that exists.
    if (e instanceof ApiError && e.status === 404) await navigate(dirname(s.path), { push: false })
  }
}

async function refreshFree(path: string): Promise<void> {
  try {
    const f = await get<{ free: number; total: number }>('/fs/free', { path })
    set({ free: f })
  } catch {
    /* not important */
  }
}

export function back(): void {
  const s = st()
  if (s.histIdx > 0) {
    set({ histIdx: s.histIdx - 1 })
    void navigate(s.history[s.histIdx - 1], { push: false, select: s.path })
  }
}

export function forward(): void {
  const s = st()
  if (s.histIdx < s.history.length - 1) {
    set({ histIdx: s.histIdx + 1 })
    void navigate(s.history[s.histIdx + 1], { push: false })
  }
}

export function up(): void {
  const s = st()
  if (s.listing?.parent) void navigate(s.listing.parent, { select: s.path })
}

/** Open the folder that holds `path` and select the item. */
export function reveal(path: string, inspector = true, page?: number | null, at?: number | null): void {
  if (inspector) setPref('inspector', true)
  goto('files')
  set({ pdfPage: page ? { path, page } : null, mediaAt: at != null ? { path, at } : null })
  void navigate(dirname(path), { select: path })
}

/** Archives that Raven opens like a folder (fs/archive.py). */
export const ARCHIVE = /\.(zip|7z|rar|tar|tar\.gz|tgz|tar\.bz2|tbz2|tar\.xz|txz|tar\.zst|tzst|iso|jar|cbz)$/i

export async function openEntry(e: { path: string; is_dir: boolean }): Promise<void> {
  if (e.is_dir || ARCHIVE.test(e.path)) return navigate(e.path)
  await openWithApp(e.path)
}

/** Open with the default app. A member of an archive is extracted to the cache first. */
export async function openWithApp(path: string): Promise<void> {
  let real = path
  if (!st().listing?.archive) activity('open', path)  // /fs/open_path notes it for archive members
  if (st().listing?.archive) {
    try {
      real = (await get<{ path: string }>('/fs/open_path', { path })).path
    } catch (err) {
      return fail(err)
    }
  }
  const err = await window.raven?.openPath(real)
  if (err) say(err, 'error')
}

export async function createArchive(paths: string[], format: 'zip' | '7z' | 'tar.zst' | 'tar.gz'): Promise<void> {
  await change(
    `Creating a ${format} archive…`,
    () => post<{ path: string; journal: Journal }>('/fs/archive', { paths, format }),
    (r) => `Created ${basename(r.path)}`
  )
}

export async function extractArchive(path: string): Promise<void> {
  const r = await change(
    `Extracting ${basename(path)}…`,
    () => post<{ path: string; journal: Journal }>('/fs/extract', { path }),
    (r) => `Extracted to ${basename(r.path)}`
  )
  if (r && dirname(r.path) === st().path) selectOnly(r.path)
}

export function setScreen(screen: Screen): void {
  goto(screen)
}

export function setPref<K extends 'view' | 'showHidden' | 'inspector' | 'leftOpen' | 'leftWidth' | 'inspectorWidth' | 'sort' | 'theme' | 'scale' | 'font' | 'fullView' | 'favorites'>(
  key: K,
  value: State[K]
): void {
  ls.set(key, value)
  set({ [key]: value } as Partial<State>)
}

// ---------------------------------------------------------------- selection

export function selectOnly(path: string | null): void {
  set({ selected: new Set(path ? [path] : []), anchor: path, cursor: path })
}

export function toggleSelect(path: string): void {
  const sel = new Set(st().selected)
  if (sel.has(path)) sel.delete(path)
  else sel.add(path)
  set({ selected: sel, anchor: path, cursor: path })
}

export function rangeSelect(path: string, additive = false): void {
  const list = visibleEntries()
  const s = st()
  const a = list.findIndex((e) => e.path === (s.anchor ?? path))
  const b = list.findIndex((e) => e.path === path)
  if (a < 0 || b < 0) return selectOnly(path)
  const [lo, hi] = a < b ? [a, b] : [b, a]
  const sel = additive ? new Set(s.selected) : new Set<string>()
  for (let i = lo; i <= hi; i++) sel.add(list[i].path)
  set({ selected: sel, cursor: path })
}

export function selectAll(): void {
  set({ selected: new Set(visibleEntries().map((e) => e.path)) })
}

export function invertSelection(): void {
  const s = st()
  set({ selected: new Set(visibleEntries().filter((e) => !s.selected.has(e.path)).map((e) => e.path)) })
}

export function moveCursor(delta: number, extend = false, cols = 1): void {
  const list = visibleEntries()
  if (!list.length) return
  const s = st()
  let i = list.findIndex((e) => e.path === s.cursor)
  if (i < 0) i = delta > 0 ? -1 : list.length
  const step = Math.abs(delta) === 1 ? delta * cols : delta
  const j = Math.max(0, Math.min(list.length - 1, i + step))
  const p = list[j].path
  if (extend) rangeSelect(p)
  else selectOnly(p)
}

export function cursorTo(pos: 'first' | 'last'): void {
  const list = visibleEntries()
  if (list.length) selectOnly(list[pos === 'first' ? 0 : list.length - 1].path)
}

export function selectedPaths(): string[] {
  const s = st()
  const order = visibleEntries().map((e) => e.path)
  return order.filter((p) => s.selected.has(p))
}

// ---------------------------------------------------------------- changes

/** One operation that Raven can retry as administrator (fs/elevate.py). */
export type ElevatedStep = { op: 'rename' | 'move' | 'copy' | 'mkdir' | 'newfile' | 'delete'; src?: string; dst?: string }

function denied(e: unknown): boolean {
  return e instanceof ApiError && e.status === 403 && /permission denied|operation not permitted/i.test(e.message)
}

/** "Permission denied": in Power user mode the status line offers "Retry as administrator" (section 9.13). */
function failOrElevate(e: unknown, steps?: ElevatedStep[]): void {
  if (!steps?.length || !denied(e) || !st().powerUser) return fail(e)
  const msg = e instanceof Error ? e.message : String(e)
  // The trash of another user or of the system is not ours to fill: as administrator, a delete is permanent.
  const permanent = steps.some((s) => s.op === 'delete')
  const run = permanent
    ? () =>
        set({
          confirm: {
            title: 'Delete permanently as administrator',
            body: `${plural(steps.length, 'item')} will be deleted permanently. The trash cannot take items that belong to another user.`,
            action: 'Delete',
            danger: true,
            run: () => void runElevated(steps)
          }
        })
    : () => void runElevated(steps)
  say(msg, 'error', { action: { label: 'Retry as administrator', run } })
}

export async function runElevated(steps: ElevatedStep[]): Promise<void> {
  say('Waiting for the administrator password…', 'busy')
  try {
    let journal: Journal | null = null
    for (const s of steps) journal = (await post<{ journal: Journal }>('/fs/elevated', s)).journal
    if (journal) set({ journal })
    say(`Done as administrator: ${steps.length} step${steps.length === 1 ? '' : 's'}`, 'info', { undo: true })
  } catch (e) {
    fail(e)
  }
  await reload()
}

async function change<T extends { journal?: Journal }>(
  busy: string,
  fn: () => Promise<T>,
  done: (r: T) => string,
  elevated?: ElevatedStep[]
): Promise<T | null> {
  say(busy, 'busy')
  try {
    const r = await fn()
    if (r.journal) set({ journal: r.journal })
    say(done(r), 'info', { undo: !!r.journal?.undo })
    await reload()
    return r
  } catch (e) {
    failOrElevate(e, elevated)
    await reload()
    return null
  }
}

const plural = (n: number, w: string): string => `${n} ${w}${n === 1 ? '' : 's'}`

export function copySelection(mode: 'copy' | 'cut'): void {
  const paths = selectedPaths()
  if (!paths.length) return
  set({ clipboard: { mode, paths } })
  say(`${mode === 'copy' ? 'Copied' : 'Cut'} ${plural(paths.length, 'item')}. Paste with Ctrl+V.`)
}

export async function paste(dst?: string): Promise<void> {
  const s = st()
  const target = dst ?? s.path
  if (!s.clipboard || !target) return
  if (!dst && readOnly()) return
  const { mode, paths } = s.clipboard
  const job = await transfer(paths, target, mode === 'copy' ? 'copy' : 'move')
  if (job && mode === 'cut') set({ clipboard: null })
}

/**
 * Copy or move through the copy queue (section 6.1). Names that exist in the target open the conflict
 * dialog first: replace, skip or keep both, for each item or for all of them.
 */
export async function transfer(paths: string[], dst: string, mode: 'copy' | 'move'): Promise<CopyJob | null> {
  if (!paths.length) return null
  try {
    const { conflicts } = await post<{ conflicts: Conflict[] }>('/fs/conflicts', { paths, dst })
    let choices: Record<string, ConflictChoice> = {}
    if (conflicts.length) {
      const picked = await new Promise<Record<string, ConflictChoice> | null>((resolve) => set({ conflicts: { mode, dst, items: conflicts, resolve } }))
      set({ conflicts: null })
      if (!picked) return null
      choices = picked
    }
    const job = await post<CopyJob>('/fs/jobs', { kind: mode, paths, dst, choices })
    set({ jobs: { ...st().jobs, [job.id]: job } })
    return job
  } catch (e) {
    fail(e)
    return null
  }
}

/** A copy_job event: keep the newest state. A finished job selects its results and offers Undo. */
export function onCopyJob(job: CopyJob): void {
  const prev = st().jobs[job.id]
  set({ jobs: { ...st().jobs, [job.id]: job } })
  if (prev?.status === job.status || !['done', 'cancelled', 'error'].includes(job.status)) return
  const verb = job.kind === 'copy' ? 'Copied' : 'Moved'
  if (job.status === 'done') {
    const skipped = job.skipped ? `, skipped ${job.skipped}` : ''
    say(`${verb} ${plural(job.results.length, 'item')} to ${basename(job.dst)}${skipped}`, 'info', { undo: job.results.length > 0 })
  } else if (job.status === 'cancelled') {
    say(`Cancelled. ${verb} ${plural(job.results.length, 'item')} before the stop.`, 'info', { undo: job.results.length > 0 })
  } else {
    // Permission denied in Power user mode: the items that are left can go as administrator.
    const left = job.srcs.filter((s) => !job.results.some((r) => basename(r) === basename(s)))
    if (st().powerUser && /permission denied/i.test(job.error ?? '') && left.length) {
      const steps: ElevatedStep[] = left.map((s) => ({ op: job.kind === 'copy' ? 'copy' : 'move', src: s, dst: `${job.dst}/${basename(s)}` }))
      say(job.error!, 'error', { action: { label: 'Retry as administrator', run: () => void runElevated(steps) } })
    } else say(job.error || 'The copy failed', 'error', { undo: job.results.length > 0 })
  }
  void get<Journal>('/fs/journal').then((journal) => set({ journal }))
  void reload().then(() => {
    if (job.results.length && job.dst === st().path) set({ selected: new Set(job.results), cursor: job.results[0], anchor: job.results[0] })
  })
}

export function controlJob(id: string, action: 'pause' | 'resume' | 'cancel'): void {
  void post<CopyJob>(`/fs/jobs/${id}/${action}`).then((j) => set({ jobs: { ...st().jobs, [j.id]: j } })).catch(fail)
}

/** True when the open folder is a trash folder: there, Delete means delete permanently. */
export function inTrash(dir: string | undefined = st().listing?.path): boolean {
  return !!dir && /\/(Trash|\.Trash-\d+)\/files$/.test(dir)
}

export async function restoreFromTrash(paths: string[]): Promise<void> {
  await change(
    `Restoring ${plural(paths.length, 'item')}…`,
    () => post<{ paths: string[]; journal: Journal }>('/fs/restore', { paths }),
    (r) => `Restored ${plural(r.paths.length, 'item')} to ${r.paths.length === 1 ? basename(dirname(r.paths[0])) : 'their folders'}`
  )
}

/** Inside an archive Raven only reads. Say so instead of an error from the sidecar. */
function readOnly(): boolean {
  if (st().listing?.writable !== false) return false
  say(st().listing?.archive ? 'Raven does not change files inside an archive. Extract it first.' : 'This folder is read-only.', 'error')
  return true
}

export async function trashSelection(): Promise<void> {
  if (readOnly()) return
  // Items in the trash cannot go to the trash again: delete them, after the confirm.
  if (inTrash()) return deleteSelection()
  const paths = selectedPaths()
  if (!paths.length) return
  const list = visibleEntries()
  const idx = list.findIndex((e) => e.path === paths[0])
  let noTrash = false
  await change(
    `Moving ${plural(paths.length, 'item')} to the trash…`,
    () =>
      post<{ count: number; journal: Journal }>('/fs/trash', { paths }).catch((e) => {
        // A share with no trash (PLAN2 P2-9.4): offer a permanent delete instead.
        // Items that are in the trash already get the same offer.
        noTrash = e instanceof ApiError && (e.data?.code === 'no_trash' || e.data?.code === 'in_trash')
        throw e
      }),
    (r) => `Moved ${plural(r.count, 'item')} to the trash`,
    paths.map((p) => ({ op: 'delete' as const, src: p }))
  )
  if (noTrash) deleteSelection()
  // Keep the cursor near where the items were.
  const after = visibleEntries()
  if (after.length) selectOnly(after[Math.min(Math.max(idx, 0), after.length - 1)].path)
}

export function deleteSelection(): void {
  if (readOnly()) return
  const paths = selectedPaths()
  if (!paths.length) return
  set({
    confirm: {
      title: 'Delete permanently',
      body:
        paths.length === 1
          ? `Raven deletes "${basename(paths[0])}" permanently. You cannot undo this.`
          : `Raven deletes ${paths.length} items permanently. You cannot undo this.`,
      action: 'Delete',
      danger: true,
      run: () =>
        void change(
          `Deleting ${plural(paths.length, 'item')}…`,
          () => post<{ count: number; journal: Journal }>('/fs/delete', { paths }),
          (r) => `Deleted ${plural(r.count, 'item')} permanently`
        )
    }
  })
}

export async function renameTo(path: string, name: string): Promise<void> {
  set({ renaming: null })
  if (!name || name === basename(path)) return
  const r = await change(
    'Renaming…',
    () => post<{ path: string; journal: Journal }>('/fs/rename', { path, name }),
    () => `Renamed to ${name}`,
    [{ op: 'rename', src: path, dst: `${dirname(path)}/${name}` }]
  )
  if (r) selectOnly(r.path)
}

export async function createItem(kind: 'mkdir' | 'newfile'): Promise<void> {
  const parent = st().path
  if (!parent || readOnly()) return
  const r = await change(
    kind === 'mkdir' ? 'Creating folder…' : 'Creating file…',
    () => post<{ path: string; journal: Journal }>(`/fs/${kind}`, { parent }),
    (r) => `Created ${basename(r.path)}`,
    [{ op: kind, dst: `${parent}/${kind === 'mkdir' ? 'New folder' : 'New file.txt'}` }]
  )
  if (r) {
    selectOnly(r.path)
    set({ renaming: r.path })
  }
}

export async function undo(): Promise<void> {
  await change(
    'Undoing…',
    () => post<{ undone: string; journal: Journal }>('/fs/undo'),
    (r) => `Undid: ${r.undone}`
  )
}

export async function redo(): Promise<void> {
  await change(
    'Redoing…',
    () => post<{ redone: string; journal: Journal }>('/fs/redo'),
    (r) => `Redid: ${r.redone}`
  )
}

export async function copyPathToClipboard(paths: string[]): Promise<void> {
  try {
    await navigator.clipboard.writeText(paths.join('\n'))
    say(paths.length === 1 ? `Copied path ${paths[0]}` : `Copied ${paths.length} paths`)
  } catch (e) {
    fail(e)
  }
}

/** Copy as relative path (section 6.1): from the project root of each item, else ~/... */
export async function copyRelativePaths(paths: string[]): Promise<void> {
  try {
    const r = await post<{ paths: string[] }>('/fs/relative', { paths })
    await navigator.clipboard.writeText(r.paths.join('\n'))
    say(r.paths.length === 1 ? `Copied ${r.paths[0]}` : `Copied ${r.paths.length} relative paths`)
  } catch (e) {
    fail(e)
  }
}

export function toggleFavorite(path: string): void {
  const favs = st().favorites
  const next = favs.includes(path) ? favs.filter((p) => p !== path) : [...favs, path]
  setPref('favorites', next)
  say(favs.includes(path) ? `Removed ${basename(path)} from favorites` : `Added ${basename(path)} to favorites`)
}

// ---------------------------------------------------------------- review

export function openReview(r: Review): void {
  set({ review: r })
}

export function closeReview(ran = false): void {
  const r = st().review
  set({ review: null, planFree: 0 })
  r?.onClose?.(ran)
}

/** Move one item into a folder (one undo): the Inspector's "Usually goes to" (PLAN2 P2-8.3). */
export async function moveTo(path: string, folder: string): Promise<void> {
  await change(
    'Moving…',
    () => post<{ journal: Journal }>('/fs/move', { paths: [path], dst: folder }),
    () => `Moved ${basename(path)} to ${basename(folder)}`
  )
}

/** Habits (PLAN2 P2-8): what you open, preview and click. Local only, and off with one switch. */
export function activity(kind: 'open' | 'preview' | 'search' | 'result_click' | 'suggestion_accepted' | 'suggestion_rejected', path?: string, query?: string): void {
  void post('/ui/activity', { kind, path: path ?? null, query: query ?? null }).catch(() => {})
}

/** Move the checked files of every step to the trash as one undo group. */
export async function trashFromReview(steps: ReviewStep[], label: string): Promise<string[]> {
  const files = steps.flatMap((s) => s.files.filter((f) => f.checked))
  if (!files.length) return []
  const paths = files.map((f) => f.path)
  const r = await change(
    `Moving ${plural(paths.length, 'item')} to the trash…`,
    () => post<{ count: number; journal: Journal }>('/fs/trash', { paths, label, source: 'cleanup' }),
    (r) => `${label}: moved ${plural(r.count, 'item')} to the trash`
  )
  if (!r) return []
  await post('/space/forget', { items: files.map((f) => [f.path, f.size]) }).catch(() => {})
  return paths
}

// ---------------------------------------------------------------- compression

export async function refreshGhost(): Promise<void> {
  try {
    set({ ghost: await get<CompressSummary>('/compress/summary') })
  } catch {
    /* not important */
  }
}

export async function compressRun(root: string, paths: string[], method: string, label: string, acceptLoss = false): Promise<CompressJob | null> {
  const r = await change(
    `Compressing ${plural(paths.length, 'item')} to ${method}. Raven verifies every file before it trashes the originals…`,
    () => post<{ job: CompressJob; journal: Journal }>('/compress/run', { root, paths, method, label, accept_loss: acceptLoss }),
    (r) => `Compressed ${plural(r.job.files, 'file')} to ${method}: ${sizeText(r.job.size_before)} → ${sizeText(r.job.size_after)}`
  )
  void refreshGhost()
  return r?.job ?? null
}

export async function compressRestore(job: CompressJob): Promise<void> {
  await change(
    `Restoring ${plural(job.files, 'file')} from ${job.method}…`,
    () => post<{ job: CompressJob & { renamed: string[] }; journal: Journal }>(`/compress/restore/${job.id}`),
    (r) =>
      `Restored ${plural(r.job.files, 'file')}. Hashes match.` + (r.job.renamed.length ? ` ${r.job.renamed.length} kept a new name because the old name is in use.` : '')
  )
  void refreshGhost()
}

function sizeText(n: number): string {
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024
    i++
  }
  return `${i ? n.toFixed(1) : n} ${u[i]}`
}

// ---------------------------------------------------------------- privacy and the router

/** The name of a cloud provider id ("groq" gives "Groq"), from the privacy status. */
export function providerName(id: string | null | undefined): string {
  if (!id) return ''
  return st().privacy?.provider_names?.[id] ?? id
}

export async function refreshPrivacy(): Promise<void> {
  try {
    set({ privacy: await get<PrivacyStatus>('/privacy/status') })
  } catch {
    /* not important */
  }
}

export async function setAiMode(mode: 'local' | 'hybrid' | 'ask'): Promise<void> {
  await post('/settings', { key: 'ai_mode', value: mode })
  await refreshPrivacy()
  say(mode === 'local' ? 'Local only: nothing leaves this computer' : mode === 'hybrid' ? 'Hybrid: heavy tasks may use the cloud after redaction' : 'Ask each time: Raven shows each payload before it sends it')
}

/** Summarize a folder. Small folders run locally. Think harder may use the cloud (section 4.1). */
export async function summarize(path: string, think = false, consent = false): Promise<void> {
  const question = `What is in ${basename(path)}?`
  // A summary has its own tab. Think harder and consent stay in that tab.
  if (!(st().screen === 'answer' && st().answer?.path === path)) newTab('answer')
  const tab = st().activeTab
  set({ cmdOpen: false, answer: { question, path, think, status: 'running', started: Date.now() } })
  try {
    const r = await post<RouterResult | ConsentRequest>('/router/run', { task: 'folder_summary', path, think_harder: think, consent })
    if (tabValue(tab, 'answer')?.path !== path) return
    if ('needs_consent' in r) patchTab(tab, { answer: { question, path, think, status: 'consent', consent: r, started: Date.now() } })
    else patchTab(tab, { answer: { question, path, think, status: 'done', result: r, started: Date.now() } })
  } catch (e) {
    patchTab(tab, { answer: { question, path, think, status: 'error', error: (e as Error).message, started: Date.now() } })
  }
  void refreshPrivacy()
}

// ---------------------------------------------------------------- conversion

export type ConvertTarget = { ext: string; label: string; engine: string; tool: string | null }

export async function convertTargets(paths: string[]): Promise<ConvertTarget[]> {
  try {
    return (await post<{ targets: ConvertTarget[] }>('/convert/targets', { paths })).targets
  } catch {
    return []
  }
}

type ConvertResult = {
  created: string[]
  errors: { path: string; error: string }[]
  print_jobs: { html: string; out: string; src: string }[]
  group: string
  label: string
  journal: Journal
}

/** Convert files. The sidecar does most engines. MD and HTML to PDF print in Electron. */
export async function convertFiles(paths: string[], target: string, combine = false): Promise<void> {
  if (!paths.length) return
  say(combine ? `Combining ${plural(paths.length, 'image')} into one PDF…` : `Converting ${plural(paths.length, 'file')} to ${target.toUpperCase()}…`, 'busy')
  try {
    const r = await post<ConvertResult>('/convert/run', { paths, target, combine })
    const created = [...r.created]
    const errors = [...r.errors]
    const printed: string[] = []
    for (const job of r.print_jobs) {
      const err = window.raven ? await window.raven.printPdf(job.html, job.out) : 'Print to PDF needs the Raven app'
      if (err) errors.push({ path: job.src, error: err })
      else printed.push(job.out)
    }
    let journal = r.journal
    if (printed.length) {
      journal = (await post<{ journal: Journal }>('/convert/record', { group: r.group, paths: printed, label: r.label })).journal
      created.push(...printed)
    }
    set({ journal })
    await reload()
    if (created.length) set({ selected: new Set(created), cursor: created[0], anchor: created[0] })
    if (errors.length && !created.length) say(errors[0].error, 'error')
    else
      say(
        `${combine ? 'Made' : 'Converted to'} ${created.length === 1 ? basename(created[0]) : plural(created.length, 'file')}` +
          (errors.length ? `. ${errors.length} failed: ${errors[0].error}` : ''),
        errors.length ? 'error' : 'info',
        { undo: true }
      )
  } catch (e) {
    fail(e)
  }
}

// ---------------------------------------------------------------- Ask (section 9.2 RAG, 12.3)

let askSeq = 0
const askAborts = new Map<number, AbortController>()

function patchAsk(id: number, p: Partial<AskSection>): void {
  // The stream may still run after the user switched tabs: patch the tab that holds the section.
  const s = st()
  if (s.asks.some((a) => a.id === id)) return set({ asks: s.asks.map((a) => (a.id === id ? { ...a, ...p } : a)) })
  const t = s.tabs.find((x) => x.snap?.asks.some((a) => a.id === id))
  if (t) patchTab(t.id, { asks: t.snap!.asks.map((a) => (a.id === id ? { ...a, ...p } : a)) })
}

function findAsk(id: number): AskSection | undefined {
  const s = st()
  return s.asks.find((a) => a.id === id) ?? s.tabs.flatMap((t) => t.snap?.asks ?? []).find((a) => a.id === id)
}

/** Ask a question about the files. `followUp` adds a section below the last one. */
export async function ask(question: string, opts: { think?: boolean; consent?: boolean; followUp?: boolean; replace?: number } = {}): Promise<void> {
  question = question.trim()
  if (!question) return
  // A new question is a new document in its own tab. Follow-ups and Think harder stay in the tab.
  if (!opts.followUp && opts.replace == null) newTab('ask')
  // One stream per tab: a follow-up stops the stream of its own tab only.
  const tab = st().activeTab
  askAborts.get(tab)?.abort()
  const ac = new AbortController()
  askAborts.set(tab, ac)
  const id = ++askSeq
  const prev = st().asks
  // Think harder (or a consent answer) replaces its own section in place. A follow-up goes at the end.
  const at = opts.replace != null ? prev.findIndex((a) => a.id === opts.replace) : -1
  const before = at >= 0 ? prev.slice(0, at) : opts.followUp ? prev : []
  const history = before.filter((a) => a.status === 'done' && !a.notFound).slice(-2).map((a) => ({ q: a.question, a: a.text }))
  const sec: AskSection = { id, question, think: !!opts.think, status: 'retrieving', sources: [], text: '', started: Date.now() }
  set({ screen: 'ask', cmdOpen: false, asks: at >= 0 ? [...before, sec, ...prev.slice(at + 1)] : [...before, sec] })
  try {
    await stream(
      '/ask',
      { question, think_harder: !!opts.think, consent: !!opts.consent, history },
      (e) => {
        if (e.type === 'sources')
          patchAsk(id, { sources: e.sources, tier: e.tier, provider: e.provider, model: e.model, reason: e.reason, redacted: e.redacted, status: 'streaming' })
        else if (e.type === 'token') {
          const cur = findAsk(id)
          if (cur) patchAsk(id, { text: cur.text + e.t, firstMs: cur.firstMs ?? Date.now() - cur.started })
        } else if (e.type === 'consent') patchAsk(id, { status: 'consent', consent: e })
        else if (e.type === 'done')
          patchAsk(id, {
            status: 'done',
            tier: e.tier ?? findAsk(id)?.tier,
            provider: e.provider,
            model: e.model,
            redacted: e.redacted,
            ms: e.ms,
            notFound: !!e.not_found,
            fallback: e.fallback
          })
        else if (e.type === 'error') patchAsk(id, { status: 'error', error: e.message })
      },
      ac.signal
    )
  } catch (e) {
    if ((e as Error).name !== 'AbortError') patchAsk(id, { status: 'error', error: (e as Error).message })
  }
  void refreshPrivacy()
}

// ---------------------------------------------------------------- agent (section 9.10)

type AgentFile = { path: string; dst: string | null; size: number; is_dir: boolean; note: string }
type AgentPlan = {
  id: string
  command: string
  tier: string
  planner: string
  ms: number
  cloud_bytes: number
  warnings: string[]
  steps: { n: number; op: string; label: string; detail: string; confirm: boolean; files: AgentFile[] }[]
}
type AgentRun =
  | { status: 'done'; label: string; created: string[]; errors: string[]; journal: Journal }
  | { status: 'print'; run_id: string; jobs: { html: string; out: string; src: string }[] }

type NamePlan = { id: string; items: { src: string; dst: string; name: string; source: string }[]; ms: number }

/** PLAN2 P2-3.2: real names for files with generic names, in a folder, one file, or the whole index. */
export async function nameFiles(path?: string): Promise<void> {
  say('Reading the files for names…', 'busy')
  let plan: NamePlan
  try {
    plan = await post<NamePlan>('/name/suggest', { path: path ?? null })
  } catch (e) {
    say((e as Error).message, 'error')
    return
  }
  if (!plan.items.length) {
    say('No file here needs a new name.')
    return
  }
  const byPath = new Map(plan.items.map((i) => [i.src, i]))
  say(`Review ${plural(plan.items.length, 'new name')}. Enter runs it.`)
  openReview({
    title: 'Name unnamed files',
    note: `${(plan.ms / 1000).toFixed(1)} s`,
    reversible: true,
    cloudBytes: 0,
    frees: false,
    steps: [
      {
        label: `Rename ${plural(plan.items.length, 'file')}`,
        files: plan.items.map((i) => ({ path: i.src, size: 0, checked: true, note: `→ ${i.name} · ${i.source}` }))
      }
    ],
    run: async (steps) => {
      const items = steps[0].files.filter((f) => f.checked).map((f) => ({ src: f.path, name: byPath.get(f.path)!.name }))
      try {
        const r = await post<{ renamed: number; errors: string[]; label: string; journal: Journal }>('/name/apply', { id: plan.id, items })
        set({ journal: r.journal })
        await reload()
        if (r.errors.length) say(`${r.label}. ${r.errors[0]}`, 'error', { undo: true })
        else say(`Done: ${r.label}`, 'info', { undo: true })
      } catch (e) {
        say((e as Error).message, 'error')
      }
    }
  })
}

// ---------------------------------------------------------------- granular control (PLAN2 P2-5)

export type Policy = { path: string; no_index: number; no_cloud: number; protect: number; no_compress: number; full_index: number; private: number }
export const POLICY_NAMES: [keyof Omit<Policy, 'path'>, string][] = [
  // PLAN4 7.6: no AI and no model reads the folder. It is a plain folder.
  ['private', 'Private: no AI'],
  ['no_index', 'Not indexed'],
  ['no_cloud', 'Never to the cloud'],
  ['protect', 'Raven never changes it'],
  ['no_compress', 'Never compressed'],
  // PLAN3 P0-7: a code project is one row in the index. This rule lists every file name of the projects inside.
  ['full_index', 'List every file of code projects']
]

export async function loadPolicies(): Promise<void> {
  try {
    set({ policies: (await get<{ policies: Policy[] }>('/fs/policies')).policies })
  } catch {
    /* an old sidecar */
  }
}

export async function togglePolicy(path: string, key: keyof Omit<Policy, 'path'>): Promise<void> {
  const cur = st().policies.find((p) => p.path === path)
  if (key === 'private' && !cur?.private) {
    setPrivate(path, true)
    return
  }
  await post('/fs/policies', { path, [key]: !cur?.[key] })
  await loadPolicies()
  say(`${POLICY_NAMES.find(([k]) => k === key)?.[1]}: ${cur?.[key] ? 'off' : 'on'} for ${basename(path)}`)
}

/** A private folder (PLAN4 7.6): Raven's AI and models never read it. Turning it on deletes what Raven knows of it. */
export function setPrivate(path: string, on: boolean): void {
  const run = async (): Promise<void> => {
    try {
      await post('/fs/policies', { path, private: on })
      await loadPolicies()
      say(on ? `${basename(path)} is private. Raven's AI does not read it.` : `${basename(path)} is not private now`)
    } catch (e) {
      say(`Could not change the folder: ${(e as Error).message}`)
    }
  }
  if (!on) {
    void run()
    return
  }
  set({
    confirm: {
      title: 'Make the folder private',
      body: `No AI and no model reads "${basename(path)}" or the files in it: no index, no search by content, no Ask, no agent, no photo or speech model. Raven deletes the index data, text, image data and thumbnails it has of the folder. The files stay as they are, and you open, copy and move them as usual.`,
      action: 'Make private',
      run: () => void run()
    }
  })
}

export async function makeLink(target: string, kind: 'symbolic' | 'hard'): Promise<void> {
  await change(
    'Making a link…',
    () => post<{ path: string; journal: Journal }>('/fs/link', { target, kind }),
    (r) => `Made ${basename(r.path)}`
  )
}

export function shredSelection(): void {
  const paths = selectedPaths()
  if (!paths.length) return
  set({
    confirm: {
      title: 'Shred',
      body: `Raven overwrites ${plural(paths.length, 'item')} three times, then deletes. You cannot undo this. On an SSD the drive may keep old copies.`,
      action: 'Shred',
      danger: true,
      run: () =>
        void change(
          'Shredding…',
          () => post<{ deleted: number; note: string | null; journal?: Journal }>('/fs/shred', { paths }),
          (r) => `Shredded ${plural(r.deleted, 'file')}${r.note ? `. ${r.note}` : ''}`
        )
    }
  })
}

/** Plan a file command and open it in the review view. Nothing changes before Run. */
export async function agentCommand(command: string, think = false): Promise<void> {
  command = command.trim()
  if (!command) return
  const s = st()
  set({ cmdOpen: false })
  say(think ? 'Planning with the cloud model…' : 'Planning…', 'busy')
  let plan: AgentPlan
  try {
    plan = await post<AgentPlan>('/agent/plan', { command, cwd: s.path, selection: selectedPaths(), think_harder: think })
  } catch (e) {
    say((e as Error).message, 'error')
    return
  }
  if (s.screen !== 'files') goto('files')
  const where =
    plan.tier === 'fast path'
      ? `fast path · ${plan.ms} ms`
      : plan.tier === 'T3'
        ? `cloud · ${plan.planner} · redacted · ${(plan.ms / 1000).toFixed(1)} s`
        : `local · ${plan.planner} · ${(plan.ms / 1000).toFixed(1)} s`
  say(`Review the plan: ${plural(plan.steps.length, 'step')}. Enter runs it. Esc cancels.`)
  openReview({
    title: command,
    note: [where, ...plan.warnings].join(' · '),
    reversible: true,
    cloudBytes: plan.cloud_bytes,
    frees: false,
    steps: plan.steps.map((st) => ({
      n: st.n,
      label: st.label,
      detail: st.detail,
      confirm: st.confirm,
      files: st.files.map((f) => ({ path: f.path, size: f.size, checked: true, note: f.note, is_dir: f.is_dir }))
    })),
    run: async (steps) => {
      const keep: Record<number, string[]> = {}
      for (const st of steps) if (st.n != null) keep[st.n] = st.files.filter((f) => f.checked).map((f) => f.path)
      say('Running the plan…', 'busy')
      try {
        let r = await post<AgentRun>('/agent/run', { id: plan.id, keep })
        while (r.status === 'print') {
          const printed: string[] = []
          for (const job of r.jobs) {
            const err = window.raven ? await window.raven.printPdf(job.html, job.out) : 'Print to PDF needs the Raven app'
            if (!err) printed.push(job.out)
          }
          r = await post<AgentRun>('/agent/resume', { run_id: r.run_id, printed })
        }
        set({ journal: r.journal })
        await reload()
        if (r.errors.length) say(`${r.label}. ${r.errors[0]}`, 'error', { undo: true })
        else say(`Done: ${r.label}`, 'info', { undo: true })
      } catch (e) {
        fail(e)
        await reload()
      }
    }
  })
}

/** Open Organize with a plan for `root` (from a Space insight or the command bar). */
export function openOrganize(root: string): void {
  goto('organize')
  set({ organizeRoot: root, cmdOpen: false })
}

// ---------------------------------------------------------------- tabs

/** Set values in one tab: the store when the tab is active, else its snapshot. */
export function patchTab(id: number, p: Partial<TabSnap>): void {
  const s = st()
  if (id === s.activeTab) return set(p)
  set({ tabs: s.tabs.map((t) => (t.id === id && t.snap ? { ...t, snap: { ...t.snap, ...p } } : t)) })
}

function tabValue<K extends TabKey>(id: number, k: K): TabSnap[K] | undefined {
  const s = st()
  if (id === s.activeTab) return s[k]
  return s.tabs.find((t) => t.id === id)?.snap?.[k]
}

/** One tab is one workspace: its screen, folder, history, selection, results, Ask document and review.
 *  The active tab's values live in the store. The other tabs keep a snapshot. */
const TAB_KEYS = [
  'screen', 'path', 'listing', 'loading', 'error', 'history', 'histIdx', 'selected', 'anchor', 'cursor', 'renaming',
  'results', 'cmdQuery', 'asks', 'answer', 'review', 'pdfPage', 'mediaAt', 'planFree', 'planPath', 'editPath', 'organizeRoot'
] as const
type TabKey = (typeof TAB_KEYS)[number]
export type TabSnap = Pick<State, TabKey>
export type Tab = { id: number; snap: TabSnap | null; used: number }
/** Screens that exist once. A click on them goes back to their tab. */
const SINGLE: Screen[] = ['space', 'organize', 'privacy', 'settings']
const MAX_TABS = 16

let tabSeq = 1
const closed: TabSnap[] = []

function pick(): TabSnap {
  const s = st()
  const o = {} as Record<string, unknown>
  for (const k of TAB_KEYS) o[k] = s[k]
  return o as TabSnap
}

function blank(screen: Screen, path: string): TabSnap {
  return {
    screen, path, listing: null, loading: false, error: null, history: [], histIdx: -1, selected: new Set(), anchor: null,
    cursor: null, renaming: null, results: null, cmdQuery: '', asks: [], answer: null, review: null, pdfPage: null, mediaAt: null,
    planFree: 0, planPath: '', editPath: false, organizeRoot: null
  }
}

function snapOf(t: Tab): TabSnap {
  return t.snap ?? pick()
}

/** Open a new tab and make it active. `init` sets its first values. */
export function newTab(screen: Screen = 'files', init: Partial<TabSnap> = {}): number {
  const s = st()
  const id = ++tabSeq
  const tabs = s.tabs.map((t) => (t.id === s.activeTab ? { ...t, snap: pick() } : t))
  // Very many tabs: drop the oldest one that is not active.
  if (tabs.length >= MAX_TABS) {
    const old = [...tabs].sort((a, b) => a.used - b.used)[0]
    closed.push(snapOf(old))
    tabs.splice(tabs.indexOf(old), 1)
  }
  const at = tabs.findIndex((t) => t.id === s.activeTab)
  tabs.splice(at + 1, 0, { id, snap: null, used: Date.now() })
  set({ ...blank(screen, s.path), ...init, tabs, activeTab: id, cmdOpen: false })
  saveTabs()
  return id
}

/** A new Files tab at `path` (default: the folder of the active tab, else home). */
export function openFilesTab(path?: string): void {
  const s = st()
  const target = path ?? (s.screen === 'files' && s.path ? s.path : (s.places?.home ?? s.path))
  newTab('files')
  if (target) void navigate(target)
}

export function switchTab(id: number): void {
  const s = st()
  if (id === s.activeTab) return
  const target = s.tabs.find((t) => t.id === id)
  if (!target?.snap) return
  listAbort?.abort()
  const snap = target.snap
  const tabs = s.tabs.map((t) => (t.id === s.activeTab ? { ...t, snap: pick() } : t.id === id ? { ...t, snap: null, used: Date.now() } : t))
  set({ ...snap, tabs, activeTab: id, cmdOpen: false })
  if (snap.screen === 'files' && snap.path) {
    // The folder may have changed while the tab was in the back. Watch it again and list it again.
    if (snap.listing) {
      send({ type: 'watch_dir', path: snap.path })
      void reload()
    } else void navigate(snap.path, { push: snap.history.length === 0 })
  }
  saveTabs()
}

export function closeTab(id: number): void {
  const s = st()
  if (s.tabs.length === 1) {
    // The last tab: start again with Files at home.
    closed.push(pick())
    set({ ...blank('files', s.places?.home ?? s.path) })
    if (s.places) void navigate(s.places.home)
    return
  }
  const i = s.tabs.findIndex((t) => t.id === id)
  if (i < 0) return
  if (id === s.activeTab) {
    const next = s.tabs[i + 1] ?? s.tabs[i - 1]
    switchTab(next.id)
  }
  const after = st()
  const t = after.tabs.find((x) => x.id === id)
  if (t) closed.push(snapOf(t))
  if (closed.length > 20) closed.shift()
  set({ tabs: after.tabs.filter((x) => x.id !== id), dual: after.dual && (after.dual.left === id || after.dual.right === id) ? null : after.dual })
  saveTabs()
}

/** F3: show a second Files tab next to this one, or go back to one pane. */
export function toggleDual(): void {
  const s = st()
  if (s.dual) {
    // The second pane was a tab that dual pane made: it goes with it.
    const d = s.dual
    set({ dual: null })
    const partner = s.activeTab === d.left ? d.right : d.left
    if (s.tabs.some((t) => t.id === partner)) closeTab(partner)
    return
  }
  if (s.screen !== 'files') return
  // The other pane starts in the same folder, in a new tab behind this one.
  const id = ++tabSeq
  const tabs = [...s.tabs]
  const at = tabs.findIndex((t) => t.id === s.activeTab)
  tabs.splice(at + 1, 0, { id, snap: { ...blank('files', s.path), listing: s.listing, history: [s.path], histIdx: 0 }, used: Date.now() })
  set({ tabs, dual: { left: s.activeTab, right: id } })
  saveTabs()
}

/** Tab in dual pane: the other pane becomes live. */
export function otherPane(): void {
  const s = st()
  if (!s.dual) return
  const other = s.activeTab === s.dual.left ? s.dual.right : s.activeTab === s.dual.right ? s.dual.left : null
  if (other != null) switchTab(other)
}

export function reopenTab(): void {
  const snap = closed.pop()
  if (!snap) return
  newTab(snap.screen, snap)
  if (snap.screen === 'files' && snap.path) void navigate(snap.path, { push: false })
}

export function cycleTab(dir: 1 | -1): void {
  const s = st()
  const i = s.tabs.findIndex((t) => t.id === s.activeTab)
  const next = s.tabs[(i + dir + s.tabs.length) % s.tabs.length]
  switchTab(next.id)
}

export function moveTab(id: number, to: number): void {
  const tabs = [...st().tabs]
  const from = tabs.findIndex((t) => t.id === id)
  if (from < 0) return
  const [t] = tabs.splice(from, 1)
  tabs.splice(Math.max(0, Math.min(to, tabs.length)), 0, t)
  set({ tabs })
  saveTabs()
}

/** Go to a screen without losing work:
 *  - the same screen stays in this tab;
 *  - Space, Organize, Privacy and Settings have one tab each: go to it, or open it;
 *  - Files goes to the last used Files tab, or opens one;
 *  - Find results, Ask and summaries open a new tab (their callers do that). */
export function goto(screen: Screen): void {
  const s = st()
  if (s.screen === screen) {
    set({ cmdOpen: false })
    return
  }
  const others = s.tabs.filter((t) => t.id !== s.activeTab && t.snap)
  let hit: Tab | undefined
  if (SINGLE.includes(screen)) hit = others.find((t) => t.snap!.screen === screen)
  else if (screen === 'files') hit = [...others].filter((t) => t.snap!.screen === 'files').sort((a, b) => b.used - a.used)[0]
  if (hit) switchTab(hit.id)
  else newTab(screen)
}

/** The network share that holds `path`, so the UI can show "docs on pc" and not the raw gvfs mount name. */
export function networkPlace(path: string): { label: string; path: string } | null {
  const net = st().places?.network ?? []
  return net.find((n) => path === n.path || path.startsWith(n.path + '/')) ?? null
}

export function tabTitle(snap: Pick<TabSnap, 'screen' | 'path' | 'cmdQuery' | 'asks' | 'answer' | 'results'>): string {
  const home = st().places?.home
  switch (snap.screen) {
    case 'files':
      if (!snap.path) return 'Files'
      if (snap.path === home) return 'Home'
      return networkPlace(snap.path)?.path === snap.path ? networkPlace(snap.path)!.label : basename(snap.path) || '/'
    case 'results':
      return `Find: ${snap.results?.query ?? snap.cmdQuery}`
    case 'ask':
      return snap.asks[0]?.question ?? 'Ask'
    case 'answer':
      return snap.answer ? `Summary: ${basename(snap.answer.path)}` : 'Summary'
    case 'space':
      return 'Space'
    case 'organize':
      return 'Organize'
    case 'privacy':
      return 'Privacy'
    case 'settings':
      return 'Settings'
  }
}

// Tabs come back after a restart: the screen, the folder and the Ask documents.
type SavedTab = { screen: Screen; path: string; cmdQuery: string; asks: AskSection[]; history: string[]; histIdx: number }

let saveTimer: ReturnType<typeof setTimeout> | null = null
export function saveTabs(): void {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    const s = st()
    const out: SavedTab[] = s.tabs.map((t) => {
      const n = snapOf(t)
      const screen = n.screen === 'results' || n.screen === 'answer' ? (n.screen === 'results' ? 'results' : 'files') : n.screen
      return {
        screen, path: n.path, cmdQuery: n.screen === 'results' ? (n.results?.query ?? n.cmdQuery) : '',
        asks: n.asks.filter((a) => a.status === 'done'), history: n.history.slice(-30),
        histIdx: Math.min(n.histIdx, 29)
      }
    })
    const value = { tabs: out, active: s.tabs.findIndex((t) => t.id === s.activeTab) }
    ls.set('tabs', value)
    void post('/ui/tabs', { tabs: value }).catch(() => {})
  }, 400)
}

/** Build the saved tabs at start-up. Return the active tab's saved values, or null. */
export async function restoreTabs(): Promise<SavedTab | null> {
  type Saved = { tabs: SavedTab[]; active: number } | null
  let saved: Saved = null
  try {
    saved = (await get<{ tabs: Saved }>('/ui/tabs')).tabs
  } catch {
    saved = ls.get<Saved>('tabs', null)
  }
  if (!saved?.tabs?.length) return null
  const active = Math.max(0, Math.min(saved.active, saved.tabs.length - 1))
  const tabs: Tab[] = saved.tabs.map((t, i) => {
    const id = ++tabSeq
    const snap = i === active ? null : { ...blank(t.screen, t.path), cmdQuery: t.cmdQuery, asks: t.asks ?? [], history: t.history ?? [], histIdx: t.histIdx ?? -1 }
    return { id, snap, used: Date.now() - (i === active ? 0 : 1000 * (saved.tabs.length - i)) }
  })
  const a = saved.tabs[active]
  set({ tabs, activeTab: tabs[active].id, ...blank(a.screen, a.path), cmdQuery: a.cmdQuery, asks: a.asks ?? [], history: a.history ?? [], histIdx: a.histIdx ?? -1 })
  return a
}

// Save when the active tab changes what it shows.
useApp.subscribe((s, prev) => {
  if (s.screen !== prev.screen || s.path !== prev.path || s.asks !== prev.asks || s.results !== prev.results) saveTabs()
})

/** Open the Connect to server dialog, with an address when the user typed one. */
export function openConnect(url = ''): void {
  useApp.setState({ connectOpen: true, connectUrl: url })
}
