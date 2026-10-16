// A folder picker inside Raven. The system dialog shows the real home of the user, but Raven may run on another
// home folder (a demo copy, HOME set to it). This picker lists folders through the sidecar, from Raven's home.

import { ArrowUp, Folder, HardDrive, Home } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { create } from 'zustand'
import { get } from '../lib/api'
import { tildify } from '../lib/format'
import type { Listing } from '../lib/types'
import { useApp } from '../store'

type Req = { title: string; start: string; resolve: (paths: string[]) => void }
const usePicker = create<{ req: Req | null }>(() => ({ req: null }))

/** Ask the user for one folder. Resolves to [path], or [] when the user cancels. */
export function pickFolder(title = 'Choose a folder', start?: string): Promise<string[]> {
  return new Promise((resolve) => {
    const home = useApp.getState().places?.home ?? '/'
    usePicker.setState({ req: { title, start: start || home, resolve } })
  })
}

/** True while the picker is open: the global keys of the app wait. */
export const pickerOpen = (): boolean => usePicker.getState().req !== null

export function FolderPickerHost() {
  const req = usePicker((s) => s.req)
  if (!req) return null
  return <Picker req={req} />
}

function Picker({ req }: { req: Req }) {
  const places = useApp((s) => s.places)
  const home = places?.home ?? '/'
  const [path, setPath] = useState(req.start)
  const [typed, setTyped] = useState(tildify(req.start, home))
  const [list, setList] = useState<Listing | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sel, setSel] = useState<string | null>(null)
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let live = true
    setError(null)
    setSel(null)
    setTyped(tildify(path, home))
    get<Listing>('/fs/list', { path, hidden: false })
      .then((l) => live && setList(l))
      .catch((e: Error) => live && setError(e.message))
    return () => {
      live = false
    }
  }, [path, home])

  const done = (paths: string[]): void => {
    usePicker.setState({ req: null })
    req.resolve(paths)
  }
  const go = (p: string): void => setPath(p.replace(/^~(?=\/|$)/, home) || '/')
  const dirs = (list?.entries ?? []).filter((e) => e.is_dir && !e.name.startsWith('.')).sort((a, b) => a.name.localeCompare(b.name))
  const chosen = sel ?? path
  const shortcuts: [string, string, typeof Home][] = [
    ['Home', home, Home],
    ...(places?.folders ?? []).map((f) => [f.label, f.path, Folder] as [string, string, typeof Home]),
    ...(places?.drives ?? []).filter((d) => d.path !== '/').map((d) => [d.label, d.path, HardDrive] as [string, string, typeof Home])
  ]
  const parent = path === '/' ? null : path.slice(0, path.lastIndexOf('/')) || '/'

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onMouseDown={(e) => e.target === e.currentTarget && done([])}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Escape') done([])
      }}
    >
      <div className="float-panel anim-pop flex h-[min(560px,80vh)] w-[min(820px,92vw)] flex-col" style={{ borderRadius: 0 }}>
        <div className="flex items-center gap-3 border-b border-line px-4 py-3">
          <span className="text-[14px]">{req.title}</span>
          <span className="ml-auto text-[11.5px] text-muted">Esc to cancel</span>
        </div>
        <div className="flex items-center gap-2 border-b border-line px-4 py-2">
          <button className="icon-btn !h-7 !w-7" disabled={!parent} title="Up" aria-label="Up" onClick={() => parent && go(parent)}>
            <ArrowUp size={14} />
          </button>
          <input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => {
              e.stopPropagation()
              if (e.key === 'Enter') go(typed.trim())
              if (e.key === 'Escape') done([])
            }}
            spellCheck={false}
            className="field mono w-full text-[12.5px]"
          />
        </div>
        <div className="flex min-h-0 flex-1">
          <div className="w-44 shrink-0 overflow-y-auto border-r border-line py-2">
            {shortcuts.map(([label, p, Icon]) => (
              <button
                key={p}
                onClick={() => go(p)}
                className={`flex w-full items-center gap-2 px-4 py-1.5 text-left text-[12.5px] transition-colors hover:bg-hover ${path === p ? 'text-accent' : ''}`}
              >
                <Icon size={13} strokeWidth={1.6} className="shrink-0 text-muted" />
                <span className="truncate">{label}</span>
              </button>
            ))}
          </div>
          <div ref={listRef} className="min-w-0 flex-1 overflow-y-auto py-1">
            {error && <div className="px-4 py-3 text-danger">{error}</div>}
            {!error && list && !dirs.length && <div className="px-4 py-3 text-muted">No folders here. Select chooses this folder.</div>}
            {dirs.map((d) => (
              <button
                key={d.path}
                onClick={() => setSel(d.path)}
                onDoubleClick={() => go(d.path)}
                className={`flex w-full items-center gap-2.5 px-4 py-1.5 text-left transition-colors ${sel === d.path ? 'bg-select' : 'hover:bg-hover'}`}
              >
                <Folder size={14} strokeWidth={1.6} className="shrink-0 text-k-folder" />
                <span className="truncate">{d.name}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-3 border-t border-line px-4 py-3">
          <span className="mono min-w-0 flex-1 truncate text-[12px] text-muted" title={chosen}>
            {tildify(chosen, home)}
          </span>
          <span className="text-[11.5px] text-muted">double-click opens a folder</span>
          <button className="link text-muted hover:text-text" onClick={() => done([])}>
            Cancel
          </button>
          <button className="btn-primary" onClick={() => done([chosen])}>
            Select
          </button>
        </div>
      </div>
    </div>
  )
}
