import * as Dialog from '@radix-ui/react-dialog'
import { Ghost, Seg } from './ui'
import { useEffect, useState } from 'react'
import { dateTime, size } from '../lib/format'
import type { ConflictChoice } from '../lib/types'
import { useApp } from '../store'

const LABEL: Record<ConflictChoice, string> = { replace: 'Replace', skip: 'Skip', keep_both: 'Keep both' }

/**
 * Conflict dialog (section 6.1): names that exist in the target folder. One choice for all items,
 * or one choice for each item. Replace moves the old item to the trash, so Undo brings it back.
 */
export default function ConflictDialog() {
  const c = useApp((s) => s.conflicts)
  const [all, setAll] = useState<ConflictChoice>('keep_both')
  const [each, setEach] = useState<Record<string, ConflictChoice>>({})
  useEffect(() => {
    setAll('keep_both')
    setEach({})
  }, [c])
  if (!c) return null
  const done = (ok: boolean): void => {
    if (!ok) return c.resolve(null)
    const out: Record<string, ConflictChoice> = {}
    for (const it of c.items) out[it.src] = each[it.src] ?? all
    c.resolve(out)
  }
  const newer = (a: number, b: number): string => (a > b ? 'newer' : a < b ? 'older' : 'same date')
  return (
    <Dialog.Root open onOpenChange={(o) => !o && done(false)}>
      <Dialog.Portal>
        <Dialog.Overlay className="anim-fade fixed inset-0 bg-black/45 backdrop-blur-md" />
        <Dialog.Content
          className="fixed left-1/2 top-[18%] flex max-h-[64vh] w-[640px] -translate-x-1/2 flex-col float-panel anim-pop p-6"
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              done(true)
            }
          }}
        >
          <Dialog.Title className="serif mb-2 text-[18px] leading-none">
            {c.items.length} name{c.items.length === 1 ? '' : 's'} already in {c.dst.slice(c.dst.lastIndexOf('/') + 1) || c.dst}
          </Dialog.Title>
          <Dialog.Description className="mb-4 text-muted">
            Choose what Raven does with each one. Replace moves the old item to the trash, so Undo brings it back.
          </Dialog.Description>
          <div className="mb-3 flex items-center gap-4">
            <span className="label">for all</span>
            <Seg
              options={(Object.keys(LABEL) as ConflictChoice[]).map((k) => [k, LABEL[k]] as [ConflictChoice, string])}
              value={(all === undefined || Object.keys(each).length ? '' : all) as ConflictChoice}
              onChange={(k) => (setAll(k), setEach({}))}
            />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto border-t border-line">
            {c.items.map((it) => {
              const cur = each[it.src] ?? all
              return (
                <div key={it.src} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 border-b border-line/70 px-1 py-3 transition-colors hover:bg-hover">
                  <div className="min-w-0">
                    <div className="truncate">{it.name}</div>
                    <div className="mono text-[11px] text-muted">
                      new: {it.src_dir ? 'folder' : size(it.src_size)} · {dateTime(it.src_mtime)} ({newer(it.src_mtime, it.dst_mtime)})
                    </div>
                    <div className="mono text-[11px] text-muted">
                      old: {it.dst_dir ? 'folder' : size(it.dst_size)} · {dateTime(it.dst_mtime)}
                    </div>
                  </div>
                  <Seg
                    size="sm"
                    options={(Object.keys(LABEL) as ConflictChoice[]).map((k) => [k, LABEL[k]] as [ConflictChoice, string])}
                    value={cur}
                    onChange={(k) => setEach({ ...each, [it.src]: k })}
                  />
                </div>
              )
            })}
          </div>
          <div className="mt-4 flex items-center justify-end gap-5">
            <Ghost tone="plain" onClick={() => done(false)}>
              Cancel (Esc)
            </Ghost>
            <button autoFocus className="btn-primary" onClick={() => done(true)}>
              {c.mode === 'copy' ? 'Copy' : 'Move'} (Enter)
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
