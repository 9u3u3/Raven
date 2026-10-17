import { useEffect, useState } from 'react'
import { get, post } from '../lib/api'
import { refreshPrivacy, say, useApp } from '../store'
import ClaudeAccount from './ClaudeAccount'
import { Chip, Ghost, Switch } from './ui'

/**
 * AI providers (PLAN4 4): the user's list, in the user's order. The first provider that works answers. Each
 * row has a key (it goes to the sidecar and never comes back: `key` is only a hint such as "…a1b2"), a model,
 * a test that sends no file data, and on or off. Drag a row, or use the arrows, to change the order.
 */

export type Provider = {
  id: string
  preset: string
  on: boolean
  model: string | null
  base_url: string
  name: string | null
  label: string
  adapter: string
  ready: boolean
  key: string
  key_hint: string
  key_url: string
  free: string
  needs_key: boolean
  store: string
}
type Preset = { id: string; name: string; adapter: string; key_hint: string; key_url: string; free: string; custom: boolean }
type ModelRow = { id: string; free: boolean }
type Test = { ok: boolean; detail: string }
type List = { providers: Provider[]; presets: Preset[]; key_store: string }

function Row({
  p,
  n,
  last,
  test,
  models,
  onChange,
  onTest,
  onModels,
  onMove,
  onDrag
}: {
  p: Provider
  n: number
  last: boolean
  test?: Test
  models?: ModelRow[]
  onChange: (body: Record<string, unknown>) => Promise<void>
  onTest: () => Promise<void>
  onModels: () => void
  onMove: (d: -1 | 1) => void
  onDrag: { start: () => void; drop: () => void }
}) {
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [over, setOver] = useState(false)
  const tone = !p.ready ? 'plain' : test ? (test.ok ? 'good' : 'bad') : 'warn'
  const state = !p.on ? 'off' : !p.ready ? (p.needs_key ? 'no key' : 'not ready') : test ? (test.ok ? `works · ${test.detail}` : test.detail) : 'not tested'
  const saveKey = async (): Promise<void> => {
    setBusy(true)
    try {
      if (key.trim()) await onChange({ key: key.trim() })
      setKey('')
      await onTest()
    } finally {
      setBusy(false)
    }
  }
  const choices = models ?? []
  const current = p.model ?? ''
  return (
    <div
      draggable
      onDragStart={(e) => (e.dataTransfer.setData('text/plain', p.id), onDrag.start())}
      onDragOver={(e) => (e.preventDefault(), setOver(true))}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => (e.preventDefault(), setOver(false), onDrag.drop())}
      className={`border-b border-line/70 py-3 transition-colors ${over ? 'bg-hover' : ''}`}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="mono w-5 cursor-grab text-[11px] text-muted" title="Drag to change the order">
          {n}
        </span>
        <span className="flex flex-col leading-none">
          <button className="text-[10px] text-muted hover:text-text disabled:opacity-30" disabled={n === 1} title="Move up" onClick={() => onMove(-1)}>
            ▲
          </button>
          <button className="text-[10px] text-muted hover:text-text disabled:opacity-30" disabled={last} title="Move down" onClick={() => onMove(1)}>
            ▼
          </button>
        </span>
        <Switch on={p.on} label={`Use ${p.label}`} onChange={(v) => void onChange({ on: v })} />
        <span className="min-w-[150px] font-medium">{p.label}</span>
        <Chip tone={tone}>{state}</Chip>
        <span className="flex-1" />
        <Ghost tone="plain" onClick={() => void onChange({ remove: true })}>
          Remove
        </Ghost>
      </div>
      <div className="ml-[72px] mt-2 grid grid-cols-[90px_minmax(0,1fr)] items-center gap-x-3 gap-y-2">
        {p.needs_key && (
          <>
            <span className="text-muted">Key</span>
            <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
              <input
                type="password"
                value={key}
                autoComplete="off"
                spellCheck={false}
                placeholder={p.key ? `saved ${p.key}` : p.key_hint}
                onChange={(e) => setKey(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && !busy && void saveKey()}
                className="field mono w-72 max-w-full"
              />
              <Ghost disabled={busy || !(key.trim() || p.key)} onClick={() => void saveKey()}>
                {busy ? 'Testing…' : key.trim() ? 'Save and test' : 'Test'}
              </Ghost>
              {p.key && !key && (
                <Ghost tone="plain" onClick={() => void onChange({ clear_key: true })}>
                  Delete key
                </Ghost>
              )}
              {p.key_url && (
                <a className="link text-[12px] text-muted" href={p.key_url} target="_blank" rel="noreferrer">
                  get a key
                </a>
              )}
            </span>
          </>
        )}
        {p.adapter === 'openai' && !p.needs_key && (
          <>
            <span className="text-muted">Server</span>
            <span className="flex flex-wrap items-center gap-3">
              <span className="mono text-[12px]">{p.base_url}</span>
              <Ghost disabled={busy} onClick={() => void (setBusy(true), onTest().finally(() => setBusy(false)))}>
                {busy ? 'Testing…' : 'Test'}
              </Ghost>
            </span>
          </>
        )}
        <span className="text-muted">Model</span>
        <span className="flex flex-wrap items-center gap-3">
          <select
            value={current}
            onFocus={() => !models && onModels()}
            onChange={(e) => void onChange(e.target.value ? { model: e.target.value } : { clear_model: true })}
            className="field mono w-72 max-w-full"
          >
            <option value="">Pick automatically</option>
            {current && !choices.some((m) => m.id === current) && <option value={current}>{current}</option>}
            {choices.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
                {m.free ? ' · free' : ''}
              </option>
            ))}
          </select>
          {!models && p.ready && (
            <button className="link text-[12px] text-muted" onClick={onModels}>
              list models
            </button>
          )}
        </span>
        {p.free && (
          <>
            <span className="text-muted">Free</span>
            <span className="text-[12px] text-muted">{p.free} Raven sends only redacted text.</span>
          </>
        )}
        {p.adapter === 'claude_cli' && (
          <>
            <span className="text-muted">Account</span>
            <ClaudeAccount />
          </>
        )}
      </div>
    </div>
  )
}

export default function AiProviders() {
  const blocked = useApp((s) => s.privacy?.cloud_blocked ?? null)
  const [list, setList] = useState<List | null>(null)
  const [tests, setTests] = useState<Record<string, Test>>({})
  const [models, setModels] = useState<Record<string, ModelRow[]>>({})
  const [adding, setAdding] = useState('')
  const [form, setForm] = useState({ key: '', base_url: '', name: '' })
  const [drag, setDrag] = useState<string | null>(null)

  const load = (): Promise<void> => get<List>('/cloud/providers').then(setList)
  useEffect(() => void load().catch(() => setList(null)), [])
  if (!list) return null
  const rows = list.providers

  const apply = async (path: string, body: Record<string, unknown>): Promise<void> => {
    try {
      setList(await post<List>(path, body))
      void refreshPrivacy()
    } catch (e) {
      say((e as Error).message, 'error')
    }
  }
  const test = async (id: string): Promise<void> => {
    try {
      const t = await post<Test>('/cloud/test', { provider: id })
      setTests((x) => ({ ...x, [id]: t }))
      if (t.ok) void loadModels(id)
    } catch (e) {
      setTests((x) => ({ ...x, [id]: { ok: false, detail: (e as Error).message } }))
    }
  }
  const loadModels = async (id: string): Promise<void> => {
    try {
      const r = await get<{ models: ModelRow[] }>(`/cloud/models?id=${encodeURIComponent(id)}`)
      setModels((x) => ({ ...x, [id]: r.models }))
    } catch (e) {
      say((e as Error).message, 'error')
    }
  }
  const change = async (id: string, body: Record<string, unknown>): Promise<void> => {
    if (body.remove) {
      useApp.setState({
        confirm: {
          title: 'Remove the provider',
          body: `Raven removes ${rows.find((r) => r.id === id)?.label ?? id} and deletes its key from this computer.`,
          action: 'Remove',
          danger: true,
          run: () => void apply('/cloud/providers/remove', { id })
        }
      })
      return
    }
    await apply('/cloud/providers/update', { id, ...body })
    if ('key' in body || 'clear_key' in body) setTests((x) => ({ ...x, [id]: undefined as unknown as Test }))
  }
  const order = (ids: string[]): void => void apply('/cloud/providers/order', { ids })
  const move = (id: string, d: -1 | 1): void => {
    const ids = rows.map((r) => r.id)
    const i = ids.indexOf(id)
    const j = i + d
    if (j < 0 || j >= ids.length) return
    ;[ids[i], ids[j]] = [ids[j], ids[i]]
    order(ids)
  }
  const dropOn = (target: string): void => {
    if (!drag || drag === target) return
    const ids = rows.map((r) => r.id).filter((x) => x !== drag)
    ids.splice(ids.indexOf(target), 0, drag)
    order(ids)
    setDrag(null)
  }

  const taken = new Set(rows.map((r) => r.preset))
  const open = list.presets.filter((p) => p.custom || !taken.has(p.id))
  const pick = list.presets.find((p) => p.id === adding)
  const add = async (): Promise<void> => {
    if (!pick) return
    const body: Record<string, unknown> = { preset: pick.id }
    if (form.key.trim()) body.key = form.key.trim()
    if (pick.custom) Object.assign(body, { base_url: form.base_url.trim(), name: form.name.trim() || null })
    try {
      const r = await post<List>('/cloud/providers/add', body)
      setList(r)
      setAdding('')
      setForm({ key: '', base_url: '', name: '' })
      void refreshPrivacy()
      const row = pick.custom ? r.providers[r.providers.length - 1] : r.providers.find((x) => x.id === pick.id)
      if (row && (row.key || !row.needs_key)) void test(row.id)
    } catch (e) {
      say((e as Error).message, 'error')
    }
  }

  return (
    <div>
      {blocked && (
        <div className="mb-4 max-w-[720px] border-l-2 border-accent pl-3 text-[12.5px]">
          {blocked} The local model answers until then.
        </div>
      )}
      {rows.length === 0 && <p className="mb-3 text-muted">No providers yet. Without one, the local model answers everything.</p>}
      <div className="mb-4 max-w-[860px]">
        {rows.map((p, k) => (
          <Row
            key={p.id}
            p={p}
            n={k + 1}
            last={k === rows.length - 1}
            test={tests[p.id]}
            models={models[p.id]}
            onChange={(b) => change(p.id, b)}
            onTest={() => test(p.id)}
            onModels={() => void loadModels(p.id)}
            onMove={(d) => move(p.id, d)}
            onDrag={{ start: () => setDrag(p.id), drop: () => dropOn(p.id) }}
          />
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <select value={adding} onChange={(e) => setAdding(e.target.value)} className="field w-64">
          <option value="">Add a provider…</option>
          {open.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
              {p.free ? ' · free plan' : ''}
            </option>
          ))}
        </select>
        {pick && pick.custom && (
          <>
            <input value={form.base_url} placeholder="http://192.168.1.20:1234/v1" spellCheck={false} onChange={(e) => setForm({ ...form, base_url: e.target.value })} className="field mono w-72" />
            <input value={form.name} placeholder="name (optional)" onChange={(e) => setForm({ ...form, name: e.target.value })} className="field w-40" />
          </>
        )}
        {pick && pick.adapter !== 'claude_cli' && (
          <input
            type="password"
            value={form.key}
            autoComplete="off"
            spellCheck={false}
            placeholder={pick.custom ? 'key (optional)' : pick.key_hint}
            onChange={(e) => setForm({ ...form, key: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && void add()}
            className="field mono w-64"
          />
        )}
        {pick && (
          <button className="btn-primary" disabled={pick.custom && !form.base_url.trim()} onClick={() => void add()}>
            Add and test
          </button>
        )}
      </div>
      {pick?.free && <p className="mt-2 max-w-[640px] text-[12px] text-muted">{pick.free}</p>}
      <p className="mt-4 text-[12px] text-muted">
        Keys are stored in {list.key_store === 'keyring' ? 'the system keyring' : 'the index on this computer. Start GNOME Keyring or KWallet to use a keyring'}. Every request goes
        through the redaction gate first. Photos, videos and audio never go to a provider.
      </p>
    </div>
  )
}
