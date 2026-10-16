import { ChevronRight, Plug, Trash2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { dragOver, dropInto } from '../lib/dnd'
import { basename, size } from '../lib/format'
import { get, post } from '../lib/api'
import type { Listing, Place, Places } from '../lib/types'
import { navigate, openConnect, openFilesTab, say, setPref, useApp } from '../store'
import { ResizeEdge, Track, TreeGuides, stagger } from './ui'

type BlockDrive = { name: string; path: string; label: string; size: number; fstype: string; mount: string | null; removable: boolean }
const MOUNTABLE = new Set(['ext4', 'ext3', 'ext2', 'btrfs', 'xfs', 'ntfs', 'ntfs3', 'vfat', 'exfat', 'f2fs', 'hfsplus', 'apfs'])

/** Mount, unmount or eject a drive (PLAN2 P2-5) with udisksctl, then list the places again. */
async function driveAction(device: string, action: 'mount' | 'unmount' | 'eject', label: string): Promise<void> {
  say(`${action === 'mount' ? 'Mounting' : action === 'eject' ? 'Ejecting' : 'Unmounting'} ${label}…`, 'busy')
  try {
    const r = await post<{ mount: string | null }>(`/drives/${action}`, { device })
    useApp.setState({ places: await get<Places>('/places') })
    say(action === 'mount' ? `Mounted ${label}` : `${action === 'eject' ? 'Ejected' : 'Unmounted'} ${label}. You can remove it.`)
    if (r.mount) void navigate(r.mount)
  } catch (e) {
    say((e as Error).message, 'error')
  }
}

async function disconnect(path: string, label: string): Promise<void> {
  try {
    const r = await post<{ places: Places }>('/net/disconnect', { path })
    useApp.setState({ places: r.places })
    const s = useApp.getState()
    if (s.path.startsWith(path)) void navigate(r.places.home)
    say(`Disconnected: ${label}`)
  } catch (e) {
    say((e as Error).message, 'error')
  }
}

type Kid = { name: string; path: string }

// The folder tree. Home is the root. Each folder opens in place, to any depth, and the tree follows the folder you
// are in: its branch opens by itself and ends in a brass node. Every row also takes a drop, and a middle click opens
// the folder in a new tab. The tree scrolls on its own, so the places underneath stay in reach.
function useKids() {
  const [kids, setKids] = useState<Record<string, Kid[] | 'loading'>>({})
  const asked = useRef(new Set<string>())
  const showHidden = useApp((s) => s.showHidden)
  const load = (path: string): void => {
    if (asked.current.has(path)) return
    asked.current.add(path)
    setKids((k) => ({ ...k, [path]: 'loading' }))
    get<Listing>('/fs/list', { path, hidden: showHidden })
      .then((l) =>
        setKids((k) => ({
          ...k,
          [path]: l.entries
            .filter((e) => e.is_dir && (showHidden || !e.is_hidden))
            .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }))
            .map((e) => ({ name: e.name, path: e.path }))
        }))
      )
      .catch(() => setKids((k) => ({ ...k, [path]: [] })))
  }
  // A change of the hidden-files setting reads the folders again.
  useEffect(() => {
    asked.current.clear()
    setKids({})
  }, [showHidden])
  return { kids, load }
}

function Node({
  name,
  path,
  trail,
  last,
  k,
  open,
  toggle,
  kids,
  load
}: {
  name: string
  path: string
  trail: boolean[]
  last: boolean
  k: number
  open: Set<string>
  toggle: (p: string) => void
  kids: Record<string, Kid[] | 'loading'>
  load: (p: string) => void
}) {
  const current = useApp((s) => s.path === path && s.screen === 'files')
  const isOpen = open.has(path)
  const children = kids[path]
  const empty = Array.isArray(children) && children.length === 0
  useEffect(() => {
    if (isOpen) load(path)
  }, [isOpen]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <div
        style={k >= 0 ? stagger(k) : undefined}
        className="group flex h-[26px] w-full items-stretch"
        onDragOver={dragOver}
        onDrop={(e) => dropInto(e, path)}
      >
        <TreeGuides trail={trail} last={last} step={16} draw={false} />
        <button
          aria-label={isOpen ? 'Collapse' : 'Expand'}
          onClick={() => toggle(path)}
          className={`grid w-4 shrink-0 place-content-center text-muted transition-colors hover:text-text ${empty ? 'invisible' : ''}`}
        >
          <ChevronRight size={11} strokeWidth={2} className={`transition-transform duration-200 ${isOpen ? 'rotate-90' : ''}`} />
        </button>
        <button
          onClick={() => void navigate(path)}
          onAuxClick={(e) => {
            if (e.button === 1) {
              e.preventDefault()
              openFilesTab(path)
            }
          }}
          title={path}
          className="flex min-w-0 flex-1 items-center py-0.5 text-left"
        >
          <span className={`truncate rounded-sm px-1.5 py-[2px] transition-all duration-200 ${current ? 'bg-accent-soft font-medium text-accent' : 'text-muted group-hover:translate-x-0.5 group-hover:text-text'}`}>{name}</span>
        </button>
      </div>
      {isOpen && Array.isArray(children) && (
        <div className="anim-fade">
          {children.map((c, i) => (
            <Node key={c.path} name={c.name} path={c.path} trail={[...trail, !last]} last={i === children.length - 1} k={-1} open={open} toggle={toggle} kids={kids} load={load} />
          ))}
        </div>
      )}
    </>
  )
}

/** A flat place: a favorite, a recent folder or a share. */
function Leaf({ label, path, hint, last, k = 0 }: { label: string; path: string; hint?: string; last: boolean; k?: number }) {
  const current = useApp((s) => s.path === path && s.screen === 'files')
  return (
    <button
      onClick={() => void navigate(path)}
      onAuxClick={(e) => {
        if (e.button === 1) {
          e.preventDefault()
          openFilesTab(path)
        }
      }}
      onDragOver={dragOver}
      onDrop={(e) => dropInto(e, path)}
      title={path}
      style={stagger(k)}
      className="anim-row group flex h-[24px] w-full items-stretch text-left"
    >
      <TreeGuides trail={[]} last={last} step={16} />
      <span className="relative flex min-w-0 flex-1 items-center gap-2 pl-1.5">
        <span className={`absolute -left-[3px] top-1/2 h-[6px] w-[6px] -translate-y-1/2 rounded-full transition-all duration-300 ${current ? 'scale-100 bg-accent' : 'scale-0'}`} />
        <span className={`truncate transition-all duration-200 group-hover:translate-x-0.5 ${current ? 'font-medium text-text' : 'text-muted group-hover:text-text'}`}>{label}</span>
        {hint && <span className="mono ml-auto shrink-0 pr-1 text-[10.5px] text-muted/80">{hint}</span>}
      </span>
    </button>
  )
}

function Heading({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-1 mt-3 flex items-center gap-3">
      <span className="label text-[0.75rem]">{children}</span>
      <span className="h-px flex-1 bg-line" />
    </div>
  )
}

function Drive({ d, removable }: { d: Place; removable?: boolean }) {
  const current = useApp((s) => s.path.startsWith(d.path) && d.path !== '/' && s.screen === 'files')
  const used = d.total && d.free !== undefined ? 1 - d.free / d.total : undefined
  const canUnmount = !!d.device && d.path !== '/' && /^\/(run\/media|media|mnt)\//.test(d.path)
  return (
    <div className="group/drive relative">
    {canUnmount && (
      <span className="mono absolute right-0 top-0 z-10 hidden gap-1 bg-bg text-[10.5px] text-muted group-hover/drive:flex">
        <button className="hover:text-text" onClick={() => void driveAction(d.device!, 'unmount', d.label)}>unmount</button>
        {removable && <button className="hover:text-danger" onClick={() => void driveAction(d.device!, 'eject', d.label)}>eject</button>}
      </span>
    )}
    <button onClick={() => void navigate(d.path)} onDragOver={dragOver} onDrop={(e) => dropInto(e, d.path)} title={d.path} className="group mb-1.5 block w-full text-left">
      <span className="flex items-baseline justify-between gap-2">
        <span className={`truncate transition-colors ${current ? 'text-text' : 'text-muted group-hover:text-text'}`}>{d.label}</span>
        {d.free !== undefined && <span className="mono shrink-0 text-[10.5px] text-muted">{size(d.free)} free</span>}
      </span>
      {used !== undefined && (
        <span className="mt-1 block">
          <Track pct={used * 100} color={used > 0.9 ? 'var(--danger)' : 'var(--accent)'} h={2} />
        </span>
      )}
    </button>
    </div>
  )
}

const MIN_W = 180
const MAX_W = 520

export default function Sidebar() {
  const width = useApp((s) => s.leftWidth)
  const places = useApp((s) => s.places)
  const favorites = useApp((s) => s.favorites)
  const recent = useApp((s) => s.recent)
  const path = useApp((s) => s.path)
  const homeCurrent = useApp((s) => !!s.places && s.path === s.places.home && s.screen === 'files')
  const { kids, load } = useKids()
  const [open, setOpen] = useState<Set<string>>(new Set())
  const toggle = (p: string): void =>
    setOpen((o) => {
      const n = new Set(o)
      if (n.has(p)) n.delete(p)
      else n.add(p)
      return n
    })

  // The tree follows you: every folder between Home and where you are opens.
  const home = places?.home ?? ''
  useEffect(() => {
    if (!home || !path.startsWith(home + '/')) return
    const parts = path.slice(home.length + 1).split('/').filter(Boolean)
    let cur = home
    const chain: string[] = []
    for (const part of parts.slice(0, -1)) {
      cur = `${cur}/${part}`
      chain.push(cur)
    }
    if (chain.length) setOpen((o) => (chain.every((c) => o.has(c)) ? o : new Set([...o, ...chain])))
  }, [path, home])

  const fullView = useApp((s) => s.fullView)
  const [unmounted, setUnmounted] = useState<BlockDrive[]>([])
  useEffect(() => {
    get<{ drives: BlockDrive[] }>('/drives')
      .then((r) => setUnmounted(r.drives.filter((d) => !d.mount && MOUNTABLE.has(d.fstype))))
      .catch(() => setUnmounted([]))
  }, [places])

  if (!places) return <aside className="shrink-0 border-r border-line" style={{ width }} />
  const drives = [...places.drives, ...places.removable]
  const removable = new Set(places.removable.map((d) => d.path))
  return (
    <aside data-tour="sidebar" className="relative flex shrink-0 flex-col border-r border-line" style={{ width }}>
      <ResizeEdge
        side="right"
        get={() => useApp.getState().leftWidth}
        onWidth={(w) => useApp.setState({ leftWidth: w })}
        onDone={(w) => setPref('leftWidth', w)}
        min={MIN_W}
        max={MAX_W}
        reset={240}
      />
      {/* The tree: it scrolls by itself. */}
      <div className="min-h-0 shrink overflow-y-auto overscroll-contain py-3 pl-5 pr-3">
        <button
          onClick={() => void navigate(places.home)}
          onDragOver={dragOver}
          onDrop={(e) => dropInto(e, places.home)}
          className="group mb-0.5 flex w-full items-center gap-2 py-1 text-left"
          title={places.home}
        >
          <span className={`inline-block h-[8px] w-[8px] rotate-45 border transition-colors duration-300 ${homeCurrent ? 'border-accent bg-accent' : 'border-muted group-hover:border-accent'}`} />
          <span className="text-[15px] font-medium leading-none">Home</span>
        </button>
        <div className="ml-[3px]">
          {fullView && <Leaf label="/ (root)" path="/" last={false} k={0} />}
          {places.folders.map((f, i) => (
            <Node key={f.path} name={f.label} path={f.path} trail={[]} last={i === places.folders.length - 1} k={i + 1} open={open} toggle={toggle} kids={kids} load={load} />
          ))}
        </div>

        {favorites.length > 0 && (
          <>
            <Heading>Favorites</Heading>
            <div className="ml-[3px]">
              {favorites.map((p, i) => (
                <Leaf key={p} label={basename(p)} path={p} last={i === favorites.length - 1} k={i} />
              ))}
            </div>
          </>
        )}
        {recent.length > 1 && (
          <>
            <Heading>Recent</Heading>
            <div className="ml-[3px]">
              {recent.slice(1, 5).map((p, i, a) => (
                <Leaf key={p} label={basename(p)} path={p} last={i === a.length - 1} k={i} />
              ))}
            </div>
          </>
        )}
      </div>

      {/* Drives, shares and the Trash stay in view under the tree. */}
      <div className="shrink-0 border-t border-line px-5 pb-3 pt-1">
        <Heading>Drives</Heading>
        {drives.map((d) => (
          <Drive key={d.path} d={d} removable={removable.has(d.path)} />
        ))}
        {unmounted.map((d) => (
          <button key={d.path} className="group mb-1 flex w-full items-baseline justify-between gap-2 text-left text-muted" title={`${d.path} · ${d.fstype}`} onClick={() => void driveAction(d.path, 'mount', d.label || d.name)}>
            <span className="truncate group-hover:text-text">{d.label || d.name}</span>
            <span className="mono shrink-0 text-[10.5px] group-hover:text-accent">mount</span>
          </button>
        ))}
        {places.network.length > 0 && (
          <>
            <Heading>Network</Heading>
            <div className="ml-[3px]">
              {places.network.map((n, i) => (
                <div key={n.path} className="group/net relative">
                  <Leaf label={n.label} path={n.path} hint={n.protocol} last={i === places.network.length - 1} k={i} />
                  {n.path.includes('/gvfs/') && (
                    <button
                      title="Disconnect"
                      onClick={() => void disconnect(n.path, n.label)}
                      className="mono absolute right-0 top-[2px] hidden bg-bg px-1.5 text-[10.5px] text-muted hover:text-danger group-hover/net:block"
                    >
                      eject
                    </button>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
        <div className="mt-3 flex items-center justify-between">
          <button onClick={() => void navigate(places.trash)} className="group flex items-center gap-2 text-muted transition-colors hover:text-text">
            <Trash2 size={13} strokeWidth={1.5} className="transition-transform duration-300 group-hover:-rotate-6" />
            <span className="link-g">Trash</span>
          </button>
          <button className="group flex items-center gap-2 text-muted transition-colors hover:text-text" onClick={() => openConnect()} title="Connect to a server">
            <Plug size={13} strokeWidth={1.5} className="transition-transform duration-300 group-hover:rotate-12" />
            <span className="link-g">Connect</span>
          </button>
        </div>
      </div>
    </aside>
  )
}
