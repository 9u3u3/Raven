import type { LucideIcon } from 'lucide-react'
import { ListChecks, MessageSquareText, Search } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { get } from '../lib/api'
import { mergedHits, PATHLIKE, useFind, useFolderFind, useVisualFind } from '../lib/search'
import type { FolderHit, SearchHit } from '../lib/types'
import {
  activity,
  copyPathToClipboard,
  createItem,
  navigate,
  redo,
  reload,
  reveal,
  setPref,
  setAiMode,
  setScreen,
  newTab,
  openOrganize,
  agentCommand,
  openConnect,
  ask,
  summarize,
  undo,
  useApp
} from '../store'
import FolderRow from './FolderRow'
import HitRow from './HitRow'
import VisualGrid, { openVisual } from './VisualGrid'
import { Seg, stagger } from './ui'

type Mode = 'find' | 'ask' | 'act'
const MODES: { id: Mode; label: string; icon: LucideIcon; blurb: string; tag: string; ph: string; examples: string[] }[] = [
  {
    id: 'find',
    label: 'Find',
    icon: Search,
    blurb: 'Files and folders by name, word, meaning or look',
    tag: 'Look for something',
    ph: 'Search by name, words inside files, meaning or look…',
    examples: ['invoice from march', 'a photo of the sea', 'type:pdf >2MB after:2026-01']
  },
  {
    id: 'ask',
    label: 'Ask',
    icon: MessageSquareText,
    blurb: 'Questions answered from your files, with sources',
    tag: 'Get an answer',
    ph: 'Ask a question about your files…',
    examples: ['what is the late fee in my lease?', 'when does my passport expire?', 'what did the report conclude?']
  },
  {
    id: 'act',
    label: 'Act',
    icon: ListChecks,
    blurb: 'Commands. You review the plan before anything runs',
    tag: 'Get something done',
    ph: 'Tell Raven what to do, for example “organize my Downloads”…',
    examples: ['organize my downloads', 'convert all docx in downloads to pdf', 'move all pdfs in downloads to documents']
  }
]
type Cmd = { label: string; hint?: string; run: () => void; disabled?: boolean }
type Row = { kind: 'hit'; hit: SearchHit } | { kind: 'folder'; folder: FolderHit } | { kind: 'cmd'; cmd: Cmd } | { kind: 'ask'; q: string }

const QUESTION = /^(what|when|where|who|whom|why|how|which|is|are|does|do|did|can|could|should|was|were|tell me|explain)\b/i
const VERB = /^(convert|move|copy|rename|compress|decompress|organi[sz]e|delete|trash|make|create|new|open|go|show|toggle|undo|redo|extract|archive|add|summari[sz]e|ai mode)\b/i
const AGENT_VERB = /^(convert|move|copy|rename|compress|decompress|organi[sz]e|delete|trash|make|extract|archive)\b/i

// Polite words and a place said first ("please, in Desktop make a folder …"), as the agent grammar reads them.
const POLITE = /^(?:(?:hey\s+)?raven,?|please|pls|kindly|can you|could you|would you|will you|i want to|i need to|i'd like to|i would like to|help me|let'?s|go ahead and|just)\s*,?\s+/i
const PLACE_FIRST = /^(?:in|inside|on|under|at|within)\s+(?:the\s+|my\s+)?[~\w./-]+?(?:\s+[\w.-]+?)?(?:\s+folder)?\s*,?\s+(?=(?:convert|move|copy|rename|compress|decompress|organi[sz]e|delete|trash|make|create|extract|archive)\b)/i
// A file command has an object: "move all pdfs …", "copy report.docx to …", "make a folder called …".
// "copy of trip photo" and "move semantics notes" are searches that start with a verb word.
const OBJECT = /^\S+\s+(?:a|an|the|all|my|these|those|this|that|it|them|every|each|any|some|new|files?|folders?|photos?|images?|pdfs?|docs?|videos?|\S+\.\w{2,5})\b|\s(?:to|into|as|called|named)\s/i

/** The words of a command without polite words or a leading place: "make a folder called x". */
export function commandCore(q: string): string {
  let t = q.trim()
  for (let i = 0; i < 3; i++) {
    const n = t.replace(POLITE, '').replace(/^[,\s]+/, '')
    if (n === t) break
    t = n
  }
  const m = PLACE_FIRST.exec(t)
  if (m) t = t.slice(m[0].length)
  return t
}

/** Find, Ask or Act from the words alone, so the user never has to switch by hand. */
export function detectIntent(q: string): Mode {
  const t = q.trim()
  if (!t || PATHLIKE.test(t)) return 'find'
  const core = commandCore(t)
  // "can you move …" is a command, "how do I move …" is a question. A file verb needs an object (OBJECT).
  const isCmd = VERB.test(core) && (!AGENT_VERB.test(core) || OBJECT.test(core) || /^\S+$/.test(core))
  if (isCmd && (core !== t || !t.endsWith('?'))) return 'act'
  if (t.endsWith('?') || QUESTION.test(t)) return 'ask'
  return 'find'
}

function appCommands(): Cmd[] {
  const s = useApp.getState()
  const p = s.places
  const cmds: Cmd[] = [
    { label: 'Go to Files', hint: 'G F', run: () => setScreen('files') },
    { label: 'Go to Space', hint: 'G S', run: () => setScreen('space') },
    { label: 'Go to Organize', hint: 'G O', run: () => setScreen('organize') },
    { label: 'Go to Privacy', hint: 'G P', run: () => setScreen('privacy') },
    { label: 'Open settings', hint: 'Ctrl+,', run: () => setScreen('settings') },
    { label: 'New folder', hint: 'Ctrl+Shift+N', run: () => (setScreen('files'), void createItem('mkdir')) },
    { label: 'New file', run: () => (setScreen('files'), void createItem('newfile')) },
    { label: s.showHidden ? 'Hide hidden files' : 'Show hidden files', hint: 'Ctrl+H', run: () => setPref('showHidden', !s.showHidden) },
    { label: 'List view', hint: 'Ctrl+1', run: () => setPref('view', 'list') },
    { label: 'Grid view', hint: 'Ctrl+2', run: () => setPref('view', 'grid') },
    { label: s.inspector ? 'Close inspector' : 'Open inspector', hint: 'Space', run: () => setPref('inspector', !s.inspector) },
    { label: s.leftOpen ? 'Hide left column' : 'Show left column', hint: 'Ctrl+B', run: () => setPref('leftOpen', !s.leftOpen) },
    { label: `Undo${s.journal.undo ? `: ${s.journal.undo}` : ''}`, hint: 'Ctrl+Z', run: () => void undo(), disabled: !s.journal.undo },
    { label: `Redo${s.journal.redo ? `: ${s.journal.redo}` : ''}`, hint: 'Ctrl+Shift+Z', run: () => void redo(), disabled: !s.journal.redo },
    { label: 'Open terminal here', run: () => void window.raven?.openTerminal(s.path) },
    { label: 'Copy path of this folder', run: () => void copyPathToClipboard([s.path]) },
    { label: 'Reload folder', hint: 'F5', run: () => void reload() },
    { label: 'Edit path', hint: 'Ctrl+L', run: () => (setScreen('files'), useApp.setState({ editPath: true })) },
    { label: 'Add folder to index', run: () => setScreen('settings') },
    { label: 'Connect to server…', hint: 'smb, sftp', run: () => openConnect() },
    { label: 'Summarize this folder', run: () => void summarize(s.path) },
    { label: 'Organize this folder', hint: 'before and after', run: () => (useApp.setState({ organizeView: 'plan' }), setScreen('organize')) },
    { label: 'Open the file map', hint: 'files by meaning', run: () => (useApp.setState({ organizeView: 'map' }), setScreen('organize')) },
    { label: 'Watch rules', hint: 'act on new files', run: () => (useApp.setState({ organizeView: 'rules' }), setScreen('organize')) },
    { label: 'Summarize this folder, think harder', hint: 'cloud, redacted', run: () => void summarize(s.path, true) },
    { label: 'AI mode: Local only', run: () => void setAiMode('local') },
    { label: 'AI mode: Hybrid', run: () => void setAiMode('hybrid') },
    { label: 'AI mode: Ask each time', run: () => void setAiMode('ask') },
    { label: 'Toggle latency overlay', hint: 'Ctrl+Shift+D', run: () => useApp.setState({ overlay: !s.overlay }) },
    { label: 'Theme: follow system', run: () => setPref('theme', 'system') },
    { label: 'Theme: light', run: () => setPref('theme', 'light') },
    { label: 'Theme: dark', run: () => setPref('theme', 'dark') }
  ]
  if (p) {
    cmds.push({ label: 'Open folder Home', run: () => void navigate(p.home) })
    for (const f of [...p.folders, ...p.drives, ...p.network]) cmds.push({ label: `Open folder ${f.label}`, run: () => void navigate(f.path) })
    for (const f of s.favorites) cmds.push({ label: `Open folder ${f.slice(f.lastIndexOf('/') + 1)}`, hint: 'favorite', run: () => void navigate(f) })
    cmds.push({ label: 'Open Trash', run: () => void navigate(p.trash) })
  }
  return cmds
}

function matchCmds(q: string, cmds: Cmd[]): Cmd[] {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return cmds.slice(0, 8)
  return cmds.filter((c) => words.every((w) => c.label.toLowerCase().includes(w)))
}

export default function CommandBar() {
  const open = useApp((s) => s.cmdOpen)
  const index = useApp((s) => s.index)
  const [q, setQ] = useState('')
  const [manual, setManual] = useState<Mode | null>(null)
  const [active, setActive] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const auto = detectIntent(q)
  const mode = manual ?? auto
  const pathMode = PATHLIKE.test(q.trim())
  const find = useFind(q, open && mode !== 'act' && !pathMode, 20)
  const folders = useFolderFind(q, open && mode !== 'act', pathMode ? 12 : 5)
  const visual = useVisualFind(q, open && mode === 'find' && !pathMode, 8)
  // An empty command bar lists the folders you open most and most lately (frecency).
  const [frecent, setFrecent] = useState<FolderHit[]>([])
  useEffect(() => {
    if (!open) return
    void get<{ folders: { path: string; name: string }[] }>('/ui/frecent', { limit: 6 })
      .then((r) =>
        setFrecent(r.folders.map((f) => ({ path: f.path, name: f.name, parent: f.path.slice(0, f.path.lastIndexOf('/')) || '/', why: 'path', files: null, size: null, mtime: 0, summary: null, top: [] }) as FolderHit))
      )
      .catch(() => {})
  }, [open])
  const visualHits = visual.q === q.trim() ? visual.hits : []

  useEffect(() => {
    if (open) {
      setQ(useApp.getState().cmdQuery)
      setManual(null)
      setActive(0)
      requestAnimationFrame(() => input.current?.select())
    }
  }, [open])

  const close = (): void => useApp.setState({ cmdOpen: false, cmdQuery: q })

  const folderHits = folders.q === q.trim() ? folders.hits : []
  const folderPaths = new Set(folderHits.map((f) => f.path))
  const hits = useMemo(() => mergedHits(find, 8), [find]).filter((h) => !folderPaths.has(h.path) && !(h.is_dir && folderHits.length))
  const cmds = useMemo(() => (open ? matchCmds(q, appCommands()) : []), [q, open])

  const sections: { title: string; rows: Row[] }[] = []
  // Room for the folders: fewer file rows when folders match too.
  const findSec = { title: 'Find', rows: hits.slice(0, folderHits.length ? 5 : 8).map((hit) => ({ kind: 'hit', hit }) as Row) }
  const folderSec = q.trim()
    ? { title: 'Folders', rows: folderHits.slice(0, pathMode ? 12 : 4).map((folder) => ({ kind: 'folder', folder }) as Row) }
    : { title: 'Frequent folders', rows: frecent.map((folder) => ({ kind: 'folder', folder }) as Row) }
  // A path, or a folder whose name is the best match, puts the folders first.
  const foldersFirst = pathMode || folderHits[0]?.ranks?.name === 1 || folderHits[0]?.ranks?.disk === 1 || folderHits[0]?.ranks?.project === 1 || /\b(folders?|director(y|ies)|dir|projects?)\b/i.test(q)
  const askSec = { title: 'Ask', rows: q.trim() ? [{ kind: 'ask', q: q.trim() } as Row] : [] }
  const actRows: Row[] = []
  const t = q.trim()
  if (/^(organi[sz]e|tidy|clean up|sort out)\b/i.test(t)) {
    actRows.push({ kind: 'cmd', cmd: { label: 'Organize the folder in view', hint: 'before and after', run: () => openOrganize(useApp.getState().path) } })
  } else if ((AGENT_VERB.test(commandCore(t)) && OBJECT.test(commandCore(t))) || (mode === 'act' && t && !cmds.length)) {
    // Section 9.10: the plan opens in the review view. Nothing changes before Run.
    actRows.push({ kind: 'cmd', cmd: { label: `Plan “${t}”`, hint: 'review first', run: () => void agentCommand(t) } })
    actRows.push({ kind: 'cmd', cmd: { label: `Plan “${t}” with the cloud model`, hint: 'think harder, redacted', run: () => void agentCommand(t, true) } })
  }
  actRows.push(...cmds.slice(0, mode === 'act' ? 12 : 4).map((cmd) => ({ kind: 'cmd', cmd }) as Row))
  const actSec = { title: 'Act', rows: actRows }
  const findSecs = foldersFirst ? [folderSec, findSec] : [findSec, folderSec]
  const order = pathMode
    ? [folderSec, actSec]
    : mode === 'ask' ? [askSec, ...findSecs, actSec] : mode === 'act' ? [actSec, findSec, askSec] : [...findSecs, askSec, actSec]
  for (const s of order) if (s.rows.length) sections.push(s)
  const flat = sections.flatMap((s) => s.rows)

  useEffect(() => setActive(0), [q, mode])
  useEffect(() => {
    listRef.current?.querySelector('[data-active]')?.scrollIntoView({ block: 'nearest' })
  }, [active])

  const run = (row: Row | undefined, how: 'enter' | 'shift' = 'enter'): void => {
    if (!row) return
    if (row.kind === 'cmd') {
      if (row.cmd.disabled) return
      close()
      row.cmd.run()
    } else if (row.kind === 'ask') {
      close()
      void ask(row.q)
    } else if (row.kind === 'folder') {
      close()
      const f = row.folder
      if (f.why === 'connect') openConnect(f.path)
      else if (how === 'shift') void navigate(f.path, { newTab: true })
      else void navigate(f.path)
    } else {
      close()
      const h = row.hit
      activity('result_click', h.path, q)
      if (how === 'shift' && !h.is_dir) void window.raven?.openPath(h.path)
      else if (h.is_dir) void navigate(h.path)
      else reveal(h.path, true, h.page, h.at)
    }
  }

  if (!open) return null
  const noRoots = index && index.roots.length === 0
  const cur = MODES.find((m) => m.id === mode)!
  return (
    <>
      <div className="anim-fade absolute inset-0 z-30 bg-black/55 backdrop-blur-md" onClick={close} />
      <div className="float-panel anim-pop absolute left-1/2 top-8 z-40 flex max-h-[78vh] w-[min(820px,94%)] -translate-x-1/2 flex-col overflow-hidden">
        <div className="flex items-center gap-6 px-7 pt-5">
          <Seg
            options={MODES.map((m) => [m.id, m.label] as [Mode, string])}
            value={mode}
            onChange={(m) => {
              setManual(m)
              input.current?.focus()
            }}
          />
          <span className="ml-auto" />
          {(find.pending || folders.pending) && mode !== 'act' && <span className="mono anim-breathe text-[10.5px] text-accent">searching</span>}
          <span className="hidden items-center gap-1.5 text-[11.5px] text-muted sm:flex">
            <kbd className="kbd">Tab</kbd> switch
          </span>
        </div>
        <div className="relative px-7 pb-2 pt-3">
          <input
            ref={input}
            value={q}
            spellCheck={false}
            placeholder={cur.ph}
            onChange={(e) => {
              setQ(e.target.value)
              setManual(null)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.preventDefault()
                close()
              } else if (e.key === 'ArrowDown') {
                e.preventDefault()
                setActive((a) => Math.min(flat.length - 1, a + 1))
              } else if (e.key === 'ArrowUp') {
                e.preventDefault()
                setActive((a) => Math.max(0, a - 1))
              } else if (e.key === 'Tab') {
                e.preventDefault()
                const modes: Mode[] = ['find', 'ask', 'act']
                setManual(modes[(modes.indexOf(mode) + (e.shiftKey ? 2 : 1)) % 3])
              } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault()
                // The full results get their own tab, so the folder in view stays.
                newTab('results', { cmdQuery: q, results: null })
              } else if (e.key === 'Enter') {
                e.preventDefault()
                run(flat[active], e.shiftKey ? 'shift' : 'enter')
              }
            }}
            className="w-full border-0 py-1 text-[16px] placeholder:text-muted/70"
            style={{ fontFamily: 'var(--font-serif)', letterSpacing: '-0.01em' }}
          />
          <div className="relative mt-1 h-px overflow-hidden bg-line">
            {(find.pending || folders.pending) && mode !== 'act' && <span className="absolute inset-y-0 w-1/3 bg-accent" style={{ animation: 'rv-scan 1s ease-in-out infinite' }} />}
          </div>

        </div>
        {(find.hybrid?.chips.length ?? 0) > 0 && (
          <div className="flex items-center gap-4 px-7 pb-2">
            <span className="label">filters</span>
            {find.hybrid!.chips.map((c) => (
              <span key={c.token} className="mono text-[12px] text-accent underline decoration-accent/40 underline-offset-4">
                {c.label}
              </span>
            ))}
          </div>
        )}
        <div ref={listRef} className="overflow-y-auto border-t border-line pb-3 pt-1">
          {!q.trim() && (
            <div key={mode} className="px-7 pb-3 pt-3">
              {cur.examples.map((ex, k) => (
                <button
                  key={ex}
                  onClick={() => {
                    setQ(ex)
                    input.current?.focus()
                  }}
                  style={stagger(k)}
                  className="anim-row group flex w-full items-baseline gap-3 border-b border-line/70 py-2.5 text-left text-muted transition-colors hover:text-text"
                >
                  <span aria-hidden className="w-3 text-accent opacity-0 transition-all duration-200 group-hover:translate-x-0.5 group-hover:opacity-100">
                    →
                  </span>
                  <span className="truncate text-[17px]">{ex}</span>
                </button>
              ))}
            </div>
          )}
          {noRoots && mode === 'find' && (
            <div className="px-7 py-3 text-muted">
              No folders are indexed, so Find searches nothing yet.{' '}
              <button className="link text-accent" onClick={() => (close(), setScreen('settings'))}>
                Add a folder
              </button>
            </div>
          )}
          {q.trim() && mode === 'find' && !hits.length && !folderHits.length && !find.pending && !folders.pending && !noRoots && (
            <div className="serif px-7 py-5 text-[14px]  text-muted">{pathMode ? `No folder matches “${q.trim()}”.` : `Nothing matches “${q.trim()}”.`}</div>
          )}
          {sections.map((sec) => (
            <div key={sec.title} className="pb-1">
              <div className="flex items-center gap-4 px-7 pb-0.5 pt-3">
                <span className="label text-[0.72rem]">{sec.title}</span>
                <span className="anim-rule h-px flex-1 bg-line" />
              </div>
              {sec.rows.map((row) => {
                const i = flat.indexOf(row)
                const isActive = i === active
                const st = { ['--i' as string]: Math.min(i, 9) } as React.CSSProperties
                if (row.kind === 'hit')
                  return (
                    <div key={row.hit.path} className="anim-row" style={st}>
                      <HitRow hit={row.hit} active={isActive} onClick={() => run(row)} />
                    </div>
                  )
                if (row.kind === 'folder')
                  return (
                    <div key={`d:${row.folder.path}`} className="anim-row" style={st}>
                      <FolderRow hit={row.folder} active={isActive} onClick={() => run(row)} />
                    </div>
                  )
                const isAsk = row.kind === 'ask'
                return (
                  <div
                    key={row.kind === 'cmd' ? row.cmd.label : 'ask'}
                    data-active={isActive || undefined}
                    onClick={() => run(row)}
                    style={st}
                    className={`anim-row group relative flex cursor-default items-center gap-3 px-7 py-2 transition-colors duration-100 ${isActive ? 'bg-select' : 'hover:bg-hover'} ${row.kind === 'cmd' && row.cmd.disabled ? 'text-muted' : ''}`}
                  >
                    <span aria-hidden className={`absolute left-0 top-1.5 h-[calc(100%-12px)] w-[2px] origin-center bg-accent transition-transform duration-300 ${isActive ? 'scale-y-100' : 'scale-y-0'}`} />
                    <span className="serif w-5 shrink-0 text-[17px]  text-muted">{isAsk ? '?' : '›'}</span>
                    <span className="min-w-0 flex-1 truncate">{row.kind === 'cmd' ? row.cmd.label : `Ask “${row.q}” about your files`}</span>
                    {row.kind === 'cmd' ? <span className="mono text-[11px] text-muted">{row.cmd.hint}</span> : <kbd className="kbd">{mode === 'ask' ? 'Enter' : 'Tab'}</kbd>}
                  </div>
                )
              })}
            </div>
          ))}
          {mode === 'find' && visualHits.length > 0 && (
            <div className="pb-1">
              <div className="flex items-center gap-4 px-7 pb-1 pt-3">
                <span className="label text-[0.72rem]">Looks like</span>
                <span className="anim-rule h-px flex-1 bg-line" />
              </div>
              <VisualGrid
                compact
                hits={visualHits}
                onActive={(path) => {
                  const h = visualHits.find((v) => v.path === path)
                  if (h) (close(), openVisual(h))
                }}
              />
            </div>
          )}
        </div>
        <div className="flex items-center gap-6 border-t border-line px-7 py-2 text-[11.5px] text-muted">
          <span className="flex items-center gap-1.5">
            <kbd className="kbd">↑</kbd>
            <kbd className="kbd">↓</kbd> choose
          </span>
          <span className="flex items-center gap-1.5">
            <kbd className="kbd">Enter</kbd> run
          </span>
          <span className="flex items-center gap-1.5">
            <kbd className="kbd">Ctrl</kbd>
            <kbd className="kbd">Enter</kbd> all results
          </span>
          <span className="ml-auto flex items-center gap-1.5">
            <kbd className="kbd">Esc</kbd> close
          </span>
        </div>
      </div>
    </>
  )
}
