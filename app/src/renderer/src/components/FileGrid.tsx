import * as CM from '@radix-ui/react-context-menu'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useMemo, useRef, useState } from 'react'
import { thumbUrl } from '../lib/api'
import { dragOver, dropInto } from '../lib/dnd'
import { openEntry, openFilesTab, selectOnly, sortEntries, useApp } from '../store'
import { FileIcon, THUMB_EXTS } from './FileIcon'
import { onRowClick, onRowContext, onRowDragStart } from './FileList'
import { ItemMenuContent } from './ItemMenu'
import RenameInput from './RenameInput'
import { EmptyFolder } from './EmptyState'

const CELL_W = 136
const CELL_H = 150

function Thumb({ path, ext, isDir, mtime, group }: { path: string; ext: string; isDir: boolean; mtime: number; group?: string }) {
  const [failed, setFailed] = useState(false)
  if (!isDir && THUMB_EXTS.has(ext) && !failed) {
    return (
      <img
        src={thumbUrl(path, 256, mtime)}
        loading="lazy"
        draggable={false}
        onError={() => setFailed(true)}
        onLoad={(e) => (e.currentTarget.naturalWidth === 0 ? setFailed(true) : null)}
        className="max-h-[96px] max-w-[112px] object-contain shadow-[0_6px_14px_-8px_rgb(0_0_0/0.55)]"
        alt=""
      />
    )
  }
  return <FileIcon ext={ext} isDir={isDir} group={group} size={28} />
}

export default function FileGrid() {
  const listing = useApp((s) => s.listing)
  const sort = useApp((s) => s.sort)
  const showHidden = useApp((s) => s.showHidden)
  const selected = useApp((s) => s.selected)
  const cursor = useApp((s) => s.cursor)
  const renaming = useApp((s) => s.renaming)
  const cols = useApp((s) => s.gridCols)
  const parentRef = useRef<HTMLDivElement>(null)

  const entries = useMemo(
    () => (listing ? sortEntries(listing.entries, sort.key, sort.dir, showHidden) : []),
    [listing, sort, showHidden]
  )

  useEffect(() => {
    const el = parentRef.current
    if (!el) return
    const ro = new ResizeObserver(() => useApp.setState({ gridCols: Math.max(1, Math.floor((el.clientWidth - 16) / CELL_W)) }))
    ro.observe(el)
    return () => {
      ro.disconnect()
      useApp.setState({ gridCols: 1 })
    }
  }, [])

  const rows = Math.ceil(entries.length / cols)
  const v = useVirtualizer({ count: rows, getScrollElement: () => parentRef.current, estimateSize: () => CELL_H, overscan: 3 })

  useEffect(() => {
    const i = entries.findIndex((e) => e.path === cursor)
    if (i >= 0) v.scrollToIndex(Math.floor(i / cols), { align: 'auto' })
  }, [cursor, entries, cols, v])

  return (
    <CM.Root>
      <CM.Trigger asChild>
        <div
          ref={parentRef}
          className="min-h-0 flex-1 overflow-y-auto px-2 pt-2"
          onClick={() => selectOnly(null)}
          onDragOver={dragOver}
          onDrop={(e) => listing && dropInto(e, listing.path)}
        >
          {entries.length === 0 && listing && <EmptyFolder />}
          <div style={{ height: v.getTotalSize(), position: 'relative' }}>
            {v.getVirtualItems().map((vr) => (
              <div key={vr.index} className="absolute left-0 flex" style={{ top: vr.start, height: CELL_H }}>
                {entries.slice(vr.index * cols, vr.index * cols + cols).map((e) => {
                  const sel = selected.has(e.path)
                  return (
                    <div
                      key={e.path}
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
                      onDragOver={e.is_dir ? dragOver : undefined}
                      onDrop={e.is_dir ? (ev) => dropInto(ev, e.path) : undefined}
                      className={`group m-[3px] flex flex-col items-center gap-2 px-2 pb-2 pt-3 transition-all duration-200 hover:-translate-y-0.5 ${sel ? 'bg-select shadow-[inset_0_-2px_0_var(--accent)]' : 'hover:bg-hover'} ${e.is_hidden ? 'opacity-55' : ''} ${cursor === e.path && !sel ? 'outline outline-1 -outline-offset-1 outline-line' : ''}`}
                      style={{ width: CELL_W - 6, height: CELL_H - 12 }}
                    >
                      <div className="flex h-[96px] items-center justify-center">
                        <Thumb path={e.path} ext={e.ext} isDir={e.is_dir} mtime={e.mtime} group={e.kind_group} />
                      </div>
                      {renaming === e.path ? (
                        <RenameInput path={e.path} name={e.name} className="text-center" />
                      ) : (
                        <span className="line-clamp-2 w-full break-all text-center leading-tight">{e.name}</span>
                      )}
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
        </div>
      </CM.Trigger>
      <ItemMenuContent />
    </CM.Root>
  )
}
