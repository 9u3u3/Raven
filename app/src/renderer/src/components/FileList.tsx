import * as CM from '@radix-ui/react-context-menu'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useMemo, useRef, useState } from 'react'
import { dragOver, dropInto, startDrag } from '../lib/dnd'
import { date, size } from '../lib/format'
import type { Entry } from '../lib/types'
import {
  openEntry,
  openFilesTab,
  rangeSelect,
  selectOnly,
  selectedPaths,
  setPref,
  sortEntries,
  toggleSelect,
  useApp,
  type SortKey
} from '../store'
import { FileIcon } from './FileIcon'
import { EmptyFolder } from './EmptyState'
import { ItemMenuContent } from './ItemMenu'
import RenameInput from './RenameInput'

// The columns follow the width of the list (dual pane, an open inspector): Name keeps its room.
const LAYOUTS = [
  { min: 720, cols: 'minmax(0,1fr) 120px 150px 150px', date: true, kind: true },
  { min: 520, cols: 'minmax(0,1fr) 110px 130px', date: true, kind: false },
  { min: 0, cols: 'minmax(0,1fr) 90px', date: false, kind: false }
]
type Layout = (typeof LAYOUTS)[number]
const ROW = 34

export function onRowClick(e: React.MouseEvent, entry: Entry): void {
  e.stopPropagation()
  if (e.ctrlKey || e.metaKey) toggleSelect(entry.path)
  else if (e.shiftKey) rangeSelect(entry.path, e.ctrlKey)
  else selectOnly(entry.path)
}

export function onRowContext(entry: Entry): void {
  if (!useApp.getState().selected.has(entry.path)) selectOnly(entry.path)
}

export function onRowDragStart(e: React.DragEvent, entry: Entry): void {
  const sel = useApp.getState().selected
  startDrag(e, sel.has(entry.path) ? selectedPaths() : [entry.path])
}

function Header({ layout }: { layout: Layout }) {
  const sort = useApp((s) => s.sort)
  const col = (key: SortKey, label: string, right = false) => (
    <button
      onClick={() => setPref('sort', { key, dir: sort.key === key ? ((-sort.dir) as 1 | -1) : 1 })}
      className={`label flex items-center gap-1 transition-colors hover:text-text ${right ? 'justify-end' : ''} ${sort.key === key ? 'text-text' : ''}`}
    >
      {label}
      {sort.key === key && <span>{sort.dir === 1 ? '↑' : '↓'}</span>}
    </button>
  )
  return (
    <div className="grid h-9 shrink-0 items-center gap-4 border-b border-line px-6" style={{ gridTemplateColumns: layout.cols }}>
      {col('name', 'Name')}
      {col('size', 'Size', true)}
      {layout.date && col('mtime', 'Modified')}
      {layout.kind && col('kind', 'Kind')}
    </div>
  )
}

export default function FileList() {
  const listing = useApp((s) => s.listing)
  const sort = useApp((s) => s.sort)
  const showHidden = useApp((s) => s.showHidden)
  const selected = useApp((s) => s.selected)
  const cursor = useApp((s) => s.cursor)
  const renaming = useApp((s) => s.renaming)
  const clipboard = useApp((s) => s.clipboard)
  const cut = useMemo(() => (clipboard?.mode === 'cut' ? new Set(clipboard.paths) : null), [clipboard])
  const [dropTarget, setDropTarget] = useState<string | null>(null)
  const parentRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(1000)
  useEffect(() => {
    const el = parentRef.current
    if (!el) return
    const ro = new ResizeObserver(() => setWidth(el.clientWidth))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const layout = LAYOUTS.find((l) => width >= l.min) ?? LAYOUTS[2]

  const entries = useMemo(
    () => (listing ? sortEntries(listing.entries, sort.key, sort.dir, showHidden) : []),
    [listing, sort, showHidden]
  )
  // Folder sizes come from the last Space scan. The bar compares folders in this list.
  const maxDir = useMemo(() => Math.max(1, ...entries.map((e) => e.dir_size ?? 0)), [entries])
  // The same for files: a small bar shows which files are the big ones in this folder.
  const maxFile = useMemo(() => Math.max(1, ...entries.map((e) => (e.is_dir ? 0 : (e.size ?? 0)))), [entries])
  const v = useVirtualizer({ count: entries.length, getScrollElement: () => parentRef.current, estimateSize: () => ROW, overscan: 12 })

  useEffect(() => {
    if (!cursor) return
    const i = entries.findIndex((e) => e.path === cursor)
    if (i >= 0) v.scrollToIndex(i, { align: 'auto' })
  }, [cursor, entries, v])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <Header layout={layout} />
      <CM.Root>
        <CM.Trigger asChild>
          <div
            ref={parentRef}
            className="min-h-0 flex-1 overflow-y-auto"
            onClick={() => selectOnly(null)}
            onDragOver={dragOver}
            onDrop={(e) => listing && dropInto(e, listing.path)}
          >
            {entries.length === 0 && listing && (
              <EmptyFolder />
            )}
            <div style={{ height: v.getTotalSize(), position: 'relative' }}>
              {v.getVirtualItems().map((vi) => {
                const e = entries[vi.index]
                const sel = selected.has(e.path)
                return (
                  <div
                    key={e.path}
                    data-path={e.path}
                    draggable={renaming !== e.path}
                    onClick={(ev) => onRowClick(ev, e)}
                    onDoubleClick={() => void openEntry(e)}
                    onAuxClick={(ev) => {
                      // Middle-click on a folder opens it in a new tab.
                      if (ev.button === 1 && e.is_dir) {
                        ev.preventDefault()
                        openFilesTab(e.path)
                      }
                    }}
                    onContextMenu={() => onRowContext(e)}
                    onDragStart={(ev) => onRowDragStart(ev, e)}
                    onDragOver={e.is_dir ? (ev) => (dragOver(ev), setDropTarget(e.path)) : undefined}
                    onDragLeave={e.is_dir ? () => setDropTarget(null) : undefined}
                    onDrop={e.is_dir ? (ev) => (setDropTarget(null), dropInto(ev, e.path)) : undefined}
                    className={`group absolute left-0 grid w-full items-center gap-4 px-6 transition-colors duration-100 ${sel ? 'bg-select' : 'hover:bg-hover'} ${cut?.has(e.path) || e.is_hidden ? 'opacity-55' : ''} ${dropTarget === e.path ? 'outline outline-1 -outline-offset-1 outline-accent bg-accent-soft' : ''} ${cursor === e.path && !sel ? 'outline outline-1 -outline-offset-1 outline-line' : ''}`}
                    style={{ top: vi.start, height: ROW, gridTemplateColumns: layout.cols }}
                  >
                    {/* A short brass tick grows in beside a selected row. */}
                    <span aria-hidden className={`absolute left-0 top-[6px] h-[calc(100%-12px)] w-[2px] origin-center bg-accent transition-transform duration-300 ease-[cubic-bezier(0.3,0.9,0.3,1)] ${sel ? 'scale-y-100' : 'scale-y-0'}`} />
                    <div className="flex min-w-0 items-center gap-2.5">
                      <FileIcon ext={e.ext} isDir={e.is_dir} isLink={e.is_link} group={e.kind_group} />
                      {renaming === e.path ? <RenameInput path={e.path} name={e.name} /> : <span className="truncate transition-transform duration-200 group-hover:translate-x-[2px]">{e.name}</span>}
                    </div>
                    <div className="mono truncate text-right text-[12px] text-muted">
                      {e.is_dir ? (
                        e.dir_size != null ? (
                          <span className="flex items-center justify-end gap-2" title={e.child_count != null ? `${e.child_count} items` : ''}>
                            <span className="inline-block h-px w-10 bg-line">
                              <span className="block h-[2px] -translate-y-px bg-k-folder" style={{ width: `${Math.max(4, (e.dir_size / maxDir) * 100)}%` }} />
                            </span>
                            {size(e.dir_size)}
                          </span>
                        ) : e.child_count != null ? (
                          `${e.child_count} item${e.child_count === 1 ? '' : 's'}`
                        ) : (
                          ''
                        )
                      ) : (
                        <span className="flex items-center justify-end gap-2">
                          {(e.size ?? 0) > 0 && (
                            <span className="inline-block h-px w-10 bg-line">
                              <span className="block h-[2px] -translate-y-px bg-muted/70" style={{ width: `${Math.max(6, Math.sqrt((e.size ?? 0) / maxFile) * 100)}%` }} />
                            </span>
                          )}
                          {size(e.size)}
                        </span>
                      )}
                    </div>
                    {layout.date && <div className="mono truncate text-[12px] text-muted">{date(e.mtime)}</div>}
                    {layout.kind && (
                      <div className="flex min-w-0 items-center gap-1.5 truncate text-muted" title={e.category_why}>
                        <span className="truncate">{e.kind}</span>
                        {!!e.kind_mismatch && (
                          <span className={`shrink-0 text-[11px] ${e.kind_mismatch === 2 ? 'text-danger' : 'text-accent'}`} title={e.kind_mismatch === 2 ? `A program (${e.sniffed_kind}) with a document name` : `Content: ${e.sniffed_kind}`}>
                            ≠ {e.sniffed_kind}
                          </span>
                        )}
                        {e.category && <span className="label shrink-0 leading-none !text-accent">{e.category}</span>}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </CM.Trigger>
        <ItemMenuContent />
      </CM.Root>
    </div>
  )
}
