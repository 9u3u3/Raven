import { ArrowLeft, ArrowRight, ArrowUp, ChevronRight, Eye, EyeOff, LayoutGrid, List, PanelLeft, PanelRight, RotateCw } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { dragOver, dropInto } from '../lib/dnd'
import { expandPath, tildify } from '../lib/format'
import { PATHLIKE, useFolderFind } from '../lib/search'
import type { FolderHit } from '../lib/types'
import { back, forward, navigate, networkPlace, openConnect, reload, setPref, up, useApp } from '../store'
import FolderRow from './FolderRow'

const NET = /^(smb|sftp|ssh|ftp|ftps|dav|davs|webdav|nfs|afp):\/\/|^\\\\/i

function IconButton({ onClick, title, active, disabled, children }: { onClick: () => void; title: string; active?: boolean; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      title={title}
      disabled={disabled}
      data-active={active ? 'true' : undefined}
      className="icon-btn"
    >
      {children}
    </button>
  )
}

function PathEditor({ initial, home }: { initial: string; home: string }) {
  const [text, setText] = useState(initial)
  const [typed, setTyped] = useState(false)
  const [active, setActive] = useState(-1)
  const input = useRef<HTMLInputElement>(null)
  // Suggestions start after the first key press, so the current path does not open a list.
  const folders = useFolderFind(text, typed, 8)
  const hits = folders.q === text.trim() ? folders.hits : []
  useEffect(() => {
    input.current?.focus()
    input.current?.select()
  }, [])
  useEffect(() => setActive(-1), [text])
  const go = (h: FolderHit | undefined): void => {
    useApp.setState({ editPath: false })
    const t = text.trim()
    if (h) {
      if (h.why === 'connect') openConnect(h.path)
      else void navigate(h.path)
    } else if (NET.test(t)) openConnect(t)
    else if (PATHLIKE.test(t) || !hits.length) void navigate(expandPath(t, home))
    else void navigate(hits[0].path)
  }
  return (
    <div className="relative w-full">
      <input
        ref={input}
        value={text}
        spellCheck={false}
        placeholder="A path, a server such as smb://host/share, or a description of the folder"
        onChange={(e) => {
          setText(e.target.value)
          setTyped(true)
        }}
        onBlur={() => useApp.setState({ editPath: false })}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            go(active >= 0 ? hits[active] : undefined)
          } else if (e.key === 'Escape') {
            useApp.setState({ editPath: false })
          } else if (e.key === 'ArrowDown' && hits.length) {
            e.preventDefault()
            setActive((a) => Math.min(hits.length - 1, a + 1))
          } else if (e.key === 'ArrowUp' && hits.length) {
            e.preventDefault()
            setActive((a) => Math.max(-1, a - 1))
          } else if (e.key === 'Tab' && hits.length && hits[0].why === 'path') {
            // Tab completes the path, like a shell.
            e.preventDefault()
            const h = hits[Math.max(active, 0)]
            setText(tildify(h.path, home) + '/')
            setTyped(true)
          }
          e.stopPropagation()
        }}
        className="w-full py-0.5"
      />
      {typed && hits.length > 0 && (
        <div className="float-panel anim-pop absolute left-0 right-0 top-full z-40 mt-1.5 max-h-[60vh] overflow-y-auto py-1 font-sans">
          {hits.map((h, i) => (
            <FolderRow key={h.path} hit={h} active={i === active} onClick={() => go(h)} />
          ))}
          <div className="mono px-4 pt-1 text-[11px] text-muted">
            {hits[0].why === 'path' ? 'Tab completes · Enter opens' : 'Enter opens the first folder · arrows choose'}
          </div>
        </div>
      )}
    </div>
  )
}

export default function PathLine() {
  const path = useApp((s) => s.path)
  const home = useApp((s) => s.places?.home ?? '')
  const editing = useApp((s) => s.editPath)
  const histIdx = useApp((s) => s.histIdx)
  const histLen = useApp((s) => s.history.length)
  const view = useApp((s) => s.view)
  const inspector = useApp((s) => s.inspector)
  const showHidden = useApp((s) => s.showHidden)
  const leftOpen = useApp((s) => s.leftOpen)
  const crumbs: { label: string; path: string }[] = []
  const shown = tildify(path, home)
  const share = networkPlace(path)
  if (share) {
    // A network share starts at its own name, not at /run/user/<uid>/gvfs/<mount name>.
    crumbs.push({ label: share.label, path: share.path })
    let cur = share.path
    for (const part of path.slice(share.path.length).split('/').filter(Boolean)) {
      cur = `${cur}/${part}`
      crumbs.push({ label: part, path: cur })
    }
  } else if (shown.startsWith('~')) {
    crumbs.push({ label: '~', path: home })
    let cur = home
    for (const part of shown.slice(2).split('/').filter(Boolean)) {
      cur = `${cur}/${part}`
      crumbs.push({ label: part, path: cur })
    }
  } else {
    crumbs.push({ label: '/', path: '/' })
    let cur = ''
    for (const part of path.split('/').filter(Boolean)) {
      cur = `${cur}/${part}`
      crumbs.push({ label: part, path: cur })
    }
  }

  // A narrow pane hides the middle crumbs: the first one, "…", then the last ones that fit.
  const crumbBox = useRef<HTMLDivElement>(null)
  const [skip, setSkip] = useState(0)
  useLayoutEffect(() => setSkip(0), [path])
  useLayoutEffect(() => {
    const el = crumbBox.current
    if (el && el.scrollWidth > el.clientWidth + 1 && skip < crumbs.length - 2) setSkip(skip + 1)
  })
  useEffect(() => {
    const el = crumbBox.current
    if (!el) return
    const ro = new ResizeObserver(() => setSkip(0))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const visible = skip ? [crumbs[0], { label: '…', path: crumbs[skip].path }, ...crumbs.slice(skip + 1)] : crumbs

  return (
    <div data-tour="pathline" className="flex h-11 shrink-0 items-center gap-1 border-b border-line px-4">
      <IconButton onClick={() => setPref('leftOpen', !leftOpen)} title="Sidebar (Ctrl+B)" active={leftOpen}>
        <PanelLeft size={16} strokeWidth={1.5} />
      </IconButton>
      <span className="mx-1.5 h-4 w-px bg-line" />
      <IconButton onClick={back} title="Back (Alt+Left)" disabled={histIdx <= 0}>
        <ArrowLeft size={16} strokeWidth={1.5} />
      </IconButton>
      <IconButton onClick={forward} title="Forward (Alt+Right)" disabled={histIdx >= histLen - 1}>
        <ArrowRight size={16} strokeWidth={1.5} />
      </IconButton>
      <IconButton onClick={up} title="Parent folder (Alt+Up)" disabled={path === '/'}>
        <ArrowUp size={16} strokeWidth={1.5} />
      </IconButton>
      <IconButton onClick={() => void reload()} title="Reload (F5)">
        <RotateCw size={15} strokeWidth={1.5} />
      </IconButton>
      <div className="mono ml-3 flex min-w-0 flex-1 items-center text-[12.5px]" onDoubleClick={() => useApp.setState({ editPath: true })}>
        {editing ? (
          <PathEditor initial={tildify(path, home)} home={home} />
        ) : (
          <div ref={crumbBox} className="flex min-w-0 items-center overflow-hidden whitespace-nowrap" title={`${path}\nEdit path (Ctrl+L)`}>
            {visible.map((c, i) => (
              <span key={`${c.label}:${c.path}`} className="flex shrink-0 items-center">
                {i > 0 && c.label !== '/' && visible[i - 1].label !== '/' && <span className="serif px-1.5 text-[17px]  leading-none text-muted/60">/</span>}
                <button
                  onClick={() => void navigate(c.path)}
                  onDragOver={dragOver}
                  onDrop={(e) => dropInto(e, c.path)}
                  className={`link py-0.5 ${i === visible.length - 1 ? 'text-text' : 'text-muted hover:text-text'}`}
                >
                  {c.label}
                </button>
              </span>
            ))}
          </div>
        )}
      </div>
      <button
        className={`mr-3 flex items-center gap-1.5 text-[12px]r transition-colors ${showHidden ? 'text-accent' : 'text-muted hover:text-text'}`}
        title="Hidden files (Ctrl+H)"
        onClick={() => setPref('showHidden', !showHidden)}
      >
        {showHidden ? <Eye size={13} strokeWidth={1.6} /> : <EyeOff size={13} strokeWidth={1.6} />}
        Hidden files
      </button>
      <div data-tour="views" className="flex items-center">
        <IconButton onClick={() => setPref('view', 'list')} title="List view (Ctrl+1)" active={view === 'list'}>
          <List size={16} strokeWidth={1.5} />
        </IconButton>
        <IconButton onClick={() => setPref('view', 'grid')} title="Grid view (Ctrl+2)" active={view === 'grid'}>
          <LayoutGrid size={16} strokeWidth={1.5} />
        </IconButton>
      </div>
      <IconButton onClick={() => setPref('inspector', !inspector)} title="Inspector (Space)" active={inspector}>
        <PanelRight size={16} strokeWidth={1.5} />
      </IconButton>
    </div>
  )
}
