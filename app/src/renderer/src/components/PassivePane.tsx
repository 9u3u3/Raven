import { dragOver, dropInto } from '../lib/dnd'
import { date, size, tildify } from '../lib/format'
import { navigate, sortEntries, switchTab, useApp, type TabSnap } from '../store'
import { FileIcon } from './FileIcon'

/** The end of a long path is the part that tells where you are. */
function short(p: string): string {
  const parts = p.split('/')
  return p.length > 40 && parts.length > 4 ? `…/${parts.slice(-3).join('/')}` : p
}

/**
 * The other pane in dual pane mode (F3): the folder of a Files tab that is not live now.
 * A click makes it live (the live pane has every action). Drops copy or move into its folder.
 */
export default function PassivePane({ tabId }: { tabId: number }) {
  const tab = useApp((s) => s.tabs.find((t) => t.id === tabId))
  const home = useApp((s) => s.places?.home ?? '')
  const sort = useApp((s) => s.sort)
  const showHidden = useApp((s) => s.showHidden)
  const snap = tab?.snap as TabSnap | null | undefined
  if (!snap) return null
  const entries = snap.listing ? sortEntries(snap.listing.entries, sort.key, sort.dir, showHidden) : []
  return (
    <div
      className="flex min-h-0 min-w-0 flex-1 cursor-default flex-col opacity-80 transition-opacity duration-200 hover:opacity-100"
      onMouseDown={() => switchTab(tabId)}
      onDragOver={dragOver}
      onDrop={(e) => dropInto(e, snap.path)}
    >
      <div className="mono flex h-12 shrink-0 items-center border-b border-line px-4 text-[12px] text-muted">
        <span className="truncate" title={snap.path}>{short(tildify(snap.path, home))}</span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5 pl-3 text-[11px]"><kbd className="kbd">Tab</kbd> use this pane</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {!snap.listing && <div className="p-4 text-muted">Click to open {snap.path}.</div>}
        {entries.map((e) => (
          <div
            key={e.path}
            onDragOver={e.is_dir ? dragOver : undefined}
            onDrop={e.is_dir ? (ev) => dropInto(ev, e.path) : undefined}
            onDoubleClick={() => e.is_dir && void navigate(e.path)}
            className={`grid h-8 cursor-default grid-cols-[minmax(0,1fr)_80px_100px] items-center gap-3 px-4 transition-colors duration-75 ${snap.selected.has(e.path) ? 'bg-select shadow-[inset_3px_0_0_var(--accent)]' : 'hover:bg-hover'}`}
          >
            <span className="flex min-w-0 items-center gap-2">
              <FileIcon ext={e.is_dir ? '' : e.name.slice(e.name.lastIndexOf('.') + 1).toLowerCase()} isDir={e.is_dir} />
              <span className="truncate">{e.name}</span>
            </span>
            <span className="mono text-right text-[12px] text-muted">{e.is_dir ? '' : size(e.size)}</span>
            <span className="mono text-[12px] text-muted">{date(e.mtime)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
