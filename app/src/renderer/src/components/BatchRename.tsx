import * as Dialog from '@radix-ui/react-dialog'
import { Chip, Ghost, Seg } from './ui'
import { useEffect, useMemo, useState } from 'react'
import { post } from '../lib/api'
import type { Entry, Journal } from '../lib/types'
import { reload, say, useApp } from '../store'

type Mode = 'replace' | 'template' | 'case'
// One empty list for the selector: a new [] on each call makes React render without end.
const NONE: Entry[] = []

function splitExt(name: string): [string, string] {
  const i = name.lastIndexOf('.')
  return i > 0 ? [name.slice(0, i), name.slice(i)] : [name, '']
}

/**
 * Batch rename with a live preview (section 6.1). Three ways:
 *  replace   find text or a regular expression, replace it ($1 works with regex);
 *  template  {name} {ext} {n} {n:3} (counter with padding) {date} (modified date, YYYY-MM-DD);
 *  case      lower, upper or title case.
 * Nothing changes before Rename. One undo reverses all items.
 */
export default function BatchRename() {
  const paths = useApp((s) => s.batchRename)
  const entries = useApp((s) => s.listing?.entries ?? NONE)
  const [mode, setMode] = useState<Mode>('replace')
  const [find, setFind] = useState('')
  const [repl, setRepl] = useState('')
  const [regex, setRegex] = useState(false)
  const [icase, setIcase] = useState(true)
  const [ext, setExt] = useState(false)
  const [tpl, setTpl] = useState('{name} {n:2}{ext}')
  const [start, setStart] = useState(1)
  const [kase, setKase] = useState<'lower' | 'upper' | 'title'>('lower')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setFind('')
    setRepl('')
    setBusy(false)
  }, [paths])

  const items = useMemo(() => {
    if (!paths) return []
    const byPath = new Map(entries.map((e) => [e.path, e]))
    return paths.map((p) => ({ path: p, name: p.slice(p.lastIndexOf('/') + 1), mtime: byPath.get(p)?.mtime ?? 0, dir: byPath.get(p)?.is_dir ?? false }))
  }, [paths, entries])

  const { rows, error } = useMemo(() => {
    let re: RegExp | null = null
    let error: string | null = null
    if (mode === 'replace' && find) {
      try {
        re = new RegExp(regex ? find : find.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), icase ? 'gi' : 'g')
      } catch (e) {
        error = `The regular expression is not valid: ${(e as Error).message}`
      }
    }
    const rows = items.map((it, i) => {
      const [stem, dot] = it.dir ? [it.name, ''] : splitExt(it.name)
      let out = it.name
      if (mode === 'replace' && re) {
        out = ext ? it.name.replace(re, repl) : stem.replace(re, repl) + dot
      } else if (mode === 'template') {
        const d = it.mtime ? new Date(it.mtime) : null
        out = tpl
          .replace(/\{name\}/g, stem)
          .replace(/\{ext\}/g, dot)
          .replace(/\{n(?::(\d+))?\}/g, (_, w) => String(start + i).padStart(Number(w || 0), '0'))
          .replace(/\{date\}/g, d ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` : '')
      } else if (mode === 'case') {
        const t = kase === 'lower' ? stem.toLowerCase() : kase === 'upper' ? stem.toUpperCase() : stem.toLowerCase().replace(/(^|[\s_-])(\p{L})/gu, (_, a, b) => a + b.toUpperCase())
        out = t + dot
      }
      return { ...it, out: out.trim() }
    })
    // Problems: an empty or invalid name, two items with one name, a clash with an item outside the batch.
    const names = new Map<string, number>()
    for (const r of rows) names.set(r.out.toLowerCase(), (names.get(r.out.toLowerCase()) ?? 0) + 1)
    const outside = new Set(entries.filter((e) => !paths?.includes(e.path)).map((e) => e.name.toLowerCase()))
    const checked = rows.map((r) => {
      let problem: string | null = null
      if (!r.out || r.out === '.' || r.out === '..' || r.out.includes('/')) problem = 'not a valid name'
      else if ((names.get(r.out.toLowerCase()) ?? 0) > 1) problem = 'two items get this name'
      else if (outside.has(r.out.toLowerCase()) && r.out.toLowerCase() !== r.name.toLowerCase()) problem = 'exists in this folder'
      return { ...r, problem }
    })
    return { rows: checked, error }
  }, [items, mode, find, repl, regex, icase, ext, tpl, start, kase, entries, paths])

  if (!paths) return null
  const changed = rows.filter((r) => r.out !== r.name)
  const blocked = rows.some((r) => r.problem) || !!error
  const close = (): void => useApp.setState({ batchRename: null })
  const run = async (): Promise<void> => {
    if (blocked || !changed.length || busy) return
    setBusy(true)
    try {
      const r = await post<{ paths: string[]; journal: Journal }>('/fs/rename_many', { items: changed.map((c) => ({ path: c.path, name: c.out })) })
      useApp.setState({ journal: r.journal, selected: new Set(r.paths), batchRename: null })
      say(`Renamed ${r.paths.length} item${r.paths.length === 1 ? '' : 's'}`, 'info', { undo: true })
      await reload()
    } catch (e) {
      say((e as Error).message, 'error')
      setBusy(false)
    }
  }
  return (
    <Dialog.Root open onOpenChange={(o) => !o && close()}>
      <Dialog.Portal>
        <Dialog.Overlay className="anim-fade fixed inset-0 bg-black/45 backdrop-blur-md" />
        <Dialog.Content
          className="fixed left-1/2 top-[10%] flex max-h-[78vh] w-[760px] -translate-x-1/2 flex-col float-panel anim-pop p-6"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.ctrlKey || (e.target as HTMLElement).tagName !== 'INPUT')) (e.preventDefault(), void run())
          }}
        >
          <Dialog.Title className="serif mb-3 text-[18px] leading-none">Rename {paths.length} items</Dialog.Title>
          <Dialog.Description className="sr-only">Batch rename with a live preview</Dialog.Description>
          <div className="mb-4">
            <Seg options={[['replace', 'Find and replace'], ['template', 'Template'], ['case', 'Case']]} value={mode} onChange={setMode} />
          </div>
          {mode === 'replace' && (
            <div className="mb-4 grid grid-cols-[90px_minmax(0,1fr)] items-center gap-x-4 gap-y-2">
              <span className="label">find</span>
              <input autoFocus value={find} spellCheck={false} onChange={(e) => setFind(e.target.value)} className="field mono" />
              <span className="label">replace</span>
              <input value={repl} spellCheck={false} onChange={(e) => setRepl(e.target.value)} className="field mono" />
              <span />
              <div className="flex gap-5 text-[12px]">
                <label className="flex items-center gap-1.5"><input type="checkbox" checked={regex} onChange={(e) => setRegex(e.target.checked)} /> Regular expression</label>
                <label className="flex items-center gap-1.5"><input type="checkbox" checked={icase} onChange={(e) => setIcase(e.target.checked)} /> Ignore case</label>
                <label className="flex items-center gap-1.5"><input type="checkbox" checked={ext} onChange={(e) => setExt(e.target.checked)} /> Include the extension</label>
              </div>
            </div>
          )}
          {mode === 'template' && (
            <div className="mb-4 grid grid-cols-[90px_minmax(0,1fr)] items-center gap-x-4 gap-y-2">
              <span className="label">name</span>
              <input autoFocus value={tpl} spellCheck={false} onChange={(e) => setTpl(e.target.value)} className="field mono" />
              <span className="label">start at</span>
              <input type="number" value={start} onChange={(e) => setStart(Number(e.target.value) || 0)} className="field mono w-24" />
              <span />
              <span className="mono text-[11px] text-muted">{'{name}'} name · {'{ext}'} extension · {'{n}'} counter · {'{n:3}'} counter with 3 digits · {'{date}'} modified date</span>
            </div>
          )}
          {mode === 'case' && (
            <div className="mb-4">
              <Seg options={[['lower', 'lower case'], ['upper', 'UPPER CASE'], ['title', 'Title Case']]} value={kase} onChange={setKase} />
            </div>
          )}
          {error && <p className="mb-2 border-l-2 border-danger py-1 pl-3 text-danger">{error}</p>}
          <div className="label mb-1.5">Preview</div>
          <div className="min-h-0 flex-1 overflow-y-auto border-t border-line">
            {rows.map((r) => (
              <div key={r.path} className="grid grid-cols-[minmax(0,1fr)_16px_minmax(0,1fr)] gap-3 border-b border-line/60 px-1 py-1.5 text-[12.5px] transition-colors hover:bg-hover">
                <span className="mono truncate text-muted">{r.name}</span>
                <span className="text-muted">→</span>
                <span className={`mono truncate ${r.problem ? 'text-danger' : r.out !== r.name ? 'text-accent' : 'text-muted'}`} title={r.problem ?? ''}>
                  {r.out}
                  {r.problem && <span className="ml-2 text-[11px]">({r.problem})</span>}
                </span>
              </div>
            ))}
          </div>
          <div className="mt-4 flex items-center justify-between">
            <span className="flex items-center gap-1.5"><Chip tone={changed.length ? 'good' : 'plain'}>{changed.length} of {rows.length} names change</Chip><Chip>one undo for all</Chip></span>
            <div className="flex items-center gap-5">
              <Ghost tone="plain" onClick={close}>Cancel (Esc)</Ghost>
              <button className="btn-primary" disabled={blocked || !changed.length || busy} onClick={() => void run()}>
                Rename (Ctrl+Enter)
              </button>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
