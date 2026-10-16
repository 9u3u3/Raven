// Granular control in the Inspector (PLAN2 P2-5): permissions, owner, tags, lock, who uses a file,
// and a bulk editor for many items. Every change has one undo.

import { useEffect, useState } from 'react'
import { ApiError, get, post } from '../lib/api'
import type { Journal } from '../lib/types'
import { reload, say, useApp } from '../store'

type Done = { changed?: number; journal: Journal; label?: string }

async function change(route: string, body: object, what: string): Promise<boolean> {
  try {
    const r = await post<Done>(route, body)
    useApp.setState({ journal: r.journal })
    say(`${r.label ?? what}${r.changed != null ? `: ${r.changed} changed` : ''}`, 'info', { undo: true })
    await reload()
    return true
  } catch (e) {
    // Files of another user: one retry as administrator (pkexec asks for the password).
    if (e instanceof ApiError && e.data?.code === 'needs_admin' && route === '/fs/chmod') return change(route, { ...body, elevated: true }, what)
    say((e as Error).message, 'error')
    return false
  }
}

const WHO = ['u', 'g', 'o'] as const
const BITS = [
  ['r', 4],
  ['w', 2],
  ['x', 1]
] as const

export function PermEditor({ paths, octal, isDir }: { paths: string[]; octal: string; isDir: boolean }) {
  const [mode, setMode] = useState(octal.slice(-3))
  const [deep, setDeep] = useState(false)
  useEffect(() => setMode(octal.slice(-3)), [octal])
  const digit = (i: number): number => Number(mode[i] ?? 0)
  const flip = (i: number, b: number): void => setMode(mode.split('').map((c, k) => (k === i ? String(Number(c) ^ b) : c)).join(''))
  const dirty = mode !== octal.slice(-3)
  return (
    <div className="py-1">
      <div className="grid grid-cols-[60px_repeat(3,28px)] items-center gap-y-0.5 text-[12px]">
        <span />
        {BITS.map(([l]) => (
          <span key={l} className="text-center text-muted">
            {l}
          </span>
        ))}
        {WHO.map((w, i) => (
          <div key={w} className="contents">
            <span className="text-muted">{w === 'u' ? 'owner' : w === 'g' ? 'group' : 'others'}</span>
            {BITS.map(([l, b]) => (
              <input key={l} type="checkbox" checked={!!(digit(i) & b)} onChange={() => flip(i, b)} className="mx-auto accent-[var(--accent)]" aria-label={`${w} ${l}`} />
            ))}
          </div>
        ))}
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        <input value={mode} onChange={(e) => /^[0-7]{0,4}$/.test(e.target.value) && setMode(e.target.value)} className="field mono w-14" aria-label="Octal mode" />
        {isDir && (
          <label className="flex items-center gap-1 text-[12px] text-muted">
            <input type="checkbox" checked={deep} onChange={(e) => setDeep(e.target.checked)} className="accent-[var(--accent)]" />
            inside too
          </label>
        )}
        <button className="btn-primary ml-auto !py-0.5" disabled={!dirty && !deep} onClick={() => void change('/fs/chmod', { paths, mode, recursive: deep }, 'Permissions')}>
          Apply
        </button>
      </div>
    </div>
  )
}

export function OwnerEditor({ paths, owner, group }: { paths: string[]; owner: string; group: string }) {
  const [v, setV] = useState(`${owner}:${group}`)
  useEffect(() => setV(`${owner}:${group}`), [owner, group])
  const [o, g] = v.split(':')
  return (
    <div className="flex items-center gap-2">
      <input value={v} onChange={(e) => setV(e.target.value)} className="field mono min-w-0 flex-1" aria-label="owner:group" />
      <button className="link text-[12px] text-accent disabled:opacity-30" disabled={v === `${owner}:${group}`} title="Needs an administrator" onClick={() => void change('/fs/chown', { paths, owner: o, group: g ?? '' }, 'Owner')}>
        Change
      </button>
    </div>
  )
}

export function Tags({ paths, tags }: { paths: string[]; tags: string[] }) {
  const [add, setAdd] = useState('')
  const send = (body: { add?: string[]; remove?: string[] }): void => void change('/fs/tags', { paths, ...body }, 'Tags')
  return (
    <div className="flex flex-wrap items-center gap-1">
      {tags.map((t) => (
        <span key={t} className="mono flex items-center gap-1 border border-line px-1.5 text-[11px]">
          {t}
          <button className="text-muted hover:text-danger" aria-label={`Remove ${t}`} onClick={() => send({ remove: [t] })}>
            ×
          </button>
        </span>
      ))}
      <input
        value={add}
        placeholder="+ tag"
        onChange={(e) => setAdd(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && add.trim()) {
            send({ add: add.split(',').map((x) => x.trim()) })
            setAdd('')
          }
        }}
        className="field mono w-20 text-[11px]"
      />
    </div>
  )
}

const LEVEL: Record<string, string> = { safe: 'text-ok', user: 'text-muted', system: 'text-accent', danger: 'text-danger' }

export function Explain({ e }: { e: { text: string; level: string } | null | undefined }) {
  if (!e) return null
  return <div className={`mb-2 text-[12px] ${LEVEL[e.level] ?? 'text-muted'}`}>{e.text}</div>
}

export function Uses({ path }: { path: string }) {
  const [procs, setProcs] = useState<{ pid: number; name?: string }[] | null>(null)
  useEffect(() => setProcs(null), [path])
  if (procs == null)
    return (
      <button className="link text-[12px] text-muted hover:text-accent" onClick={() => void get<{ processes: { pid: number; name?: string }[] }>('/fs/uses', { path }).then((r) => setProcs(r.processes))}>
        Who uses it?
      </button>
    )
  return <span className="mono text-[12px] text-muted">{procs.length ? procs.map((p) => `${p.name ?? '?'} (${p.pid})`).join(', ') : 'no program'}</span>
}

export function Lock({ path, flags }: { path: string; flags: string }) {
  const locked = flags.includes('i')
  return (
    <button className="link text-[12px] text-muted hover:text-accent" title="Immutable: nobody can change or delete it. Needs an administrator." onClick={() => void change('/fs/flag', { path, flag: 'i', on: !locked }, locked ? 'Unlocked' : 'Locked')}>
      {locked ? 'Locked · unlock' : 'Lock'}
    </button>
  )
}

/** Many items selected: one permission and one date for all of them. */
export function BulkProps({ paths }: { paths: string[] }) {
  const [day, setDay] = useState('')
  return (
    <div className="p-4">
      <div className="mb-2 text-muted">{paths.length} items</div>
      <div className="label mb-1">Permissions</div>
      <PermEditor paths={paths} octal="644" isDir />
      <div className="label mb-1 mt-3">Modified date</div>
      <div className="flex items-center gap-2">
        <input type="date" value={day} onChange={(e) => setDay(e.target.value)} className="field mono" />
        <button className="btn-primary !py-0.5" disabled={!day} onClick={() => void change('/fs/touch', { paths, mtime: new Date(`${day}T12:00:00`).getTime() / 1000 }, 'Date')}>
          Apply
        </button>
      </div>
      <div className="label mb-1 mt-3">Tags</div>
      <Tags paths={paths} tags={[]} />
    </div>
  )
}
