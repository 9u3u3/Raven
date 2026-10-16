import { useEffect, useState } from 'react'
import { del, get, on, post } from '../lib/api'
import type { Places } from '../lib/types'
import { navigate, say, useApp } from '../store'

type Remote = { name: string; type: string; label: string; mounted: boolean; path: string }
type Status = {
  available: boolean
  version?: string
  fuse?: boolean
  remotes: Remote[]
  types: Record<string, { label: string; fields: string[]; oauth?: boolean }>
  auth?: Record<string, { state: string; error: string | null }>
}

const FIELD: Record<string, { label: string; placeholder?: string; secret?: boolean }> = {
  url: { label: 'Address', placeholder: 'https://cloud.example.com/remote.php/dav/files/me' },
  vendor: { label: 'Server', placeholder: 'nextcloud, owncloud or other' },
  user: { label: 'User' },
  pass: { label: 'Password', secret: true },
  provider: { label: 'Provider', placeholder: 'AWS, Minio, Cloudflare, Wasabi' },
  access_key_id: { label: 'Access key' },
  secret_access_key: { label: 'Secret key', secret: true },
  endpoint: { label: 'Endpoint', placeholder: 'empty for AWS' },
  region: { label: 'Region', placeholder: 'us-east-1' },
  host: { label: 'Host', placeholder: '192.168.1.30' },
  port: { label: 'Port', placeholder: '22' },
  key_file: { label: 'Key file', placeholder: '~/.ssh/id_ed25519' }
}

/** rclone remotes (section 9.12): WebDAV, S3, SFTP and cloud drives, mounted as folders. */
export default function RcloneView({ onDone }: { onDone: () => void }) {
  const [st, setSt] = useState<Status | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [typ, setTyp] = useState('webdav')
  const [name, setName] = useState('')
  const [params, setParams] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)

  const load = (): void => void get<Status>('/rclone').then(setSt).catch((e) => setErr(e.message))
  useEffect(() => {
    load()
    return on('rclone_changed', load)
  }, [])

  const open = async (r: Remote): Promise<void> => {
    setBusy(r.name)
    setErr(null)
    try {
      const m = await post<{ path: string }>('/rclone/mount', { name: r.name })
      useApp.setState({ places: await get<Places>('/places') })
      onDone()
      await navigate(m.path)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(null)
    }
  }
  const add = async (): Promise<void> => {
    setBusy('add')
    setErr(null)
    try {
      const r = await post<{ state: string; note?: string }>('/rclone/remotes', { name: name.trim(), type: typ, params })
      say(r.note ?? `Added ${name.trim()}`)
      setName('')
      setParams({})
      load()
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  if (!st) return <p className="text-muted">{err ?? 'Reading the rclone config…'}</p>
  if (!st.available)
    return (
      <div className="border-l-2 border-danger py-1 pl-3">
        <p className="mb-1">rclone is not installed:</p>
        <code className="mono select-all text-[12px]">sudo pacman -S rclone fuse3</code>
        <p className="mt-1 text-[12px] text-muted">sudo apt install rclone fuse3 · rclone.org/install</p>
      </div>
    )
  const t = st.types[typ]
  return (
    <div>
      <div className="mb-4">
        <div className="label mb-1">Remotes</div>
        {!st.remotes.length && <p className="text-muted">No remotes yet.</p>}
        {st.remotes.map((r) => (
          <div key={r.name} className="flex items-center justify-between gap-3 border-b border-line/60 px-1 py-2 transition-colors hover:bg-hover">
            <span className="min-w-0 truncate">
              {r.name} <span className="mono text-[11px] text-muted">{r.type}{r.mounted ? ' · mounted' : ''}</span>
              {st.auth?.[r.name]?.state === 'waiting' && <span className="ml-2 text-[12px] text-muted">waiting for the browser sign-in</span>}
            </span>
            <span className="flex shrink-0 gap-4">
              <button className="btn-text text-accent" disabled={busy === r.name} onClick={() => void open(r)}>
                {busy === r.name ? 'Mounting…' : 'Open'}
              </button>
              {r.mounted && (
                <button className="btn-text text-muted" onClick={() => void post('/rclone/unmount', { name: r.name }).then(load).catch((e) => setErr(e.message))}>
                  Unmount
                </button>
              )}
              <button className="btn-text text-danger" onClick={() => void del(`/rclone/remotes/${encodeURIComponent(r.name)}`).then(load).catch((e) => setErr(e.message))}>
                Remove
              </button>
            </span>
          </div>
        ))}
      </div>
      <div className="label mb-2">Add a remote</div>
      <div className="mb-3 grid grid-cols-[90px_1fr] items-center gap-y-2">
        <span className="text-muted">Kind</span>
        <select value={typ} onChange={(e) => (setTyp(e.target.value), setParams({}))} className="field">
          {Object.entries(st.types).map(([k, v]) => (
            <option key={k} value={k}>
              {v.label}
            </option>
          ))}
        </select>
        <span className="text-muted">Name</span>
        <input value={name} spellCheck={false} placeholder="work-nas" onChange={(e) => setName(e.target.value)} className="field" />
        {t.fields.map((f) => (
          <div key={f} className="contents">
            <span className="text-muted">{FIELD[f]?.label ?? f}</span>
            <input
              type={FIELD[f]?.secret ? 'password' : 'text'}
              value={params[f] ?? ''}
              spellCheck={false}
              placeholder={FIELD[f]?.placeholder ?? ''}
              onChange={(e) => setParams({ ...params, [f]: e.target.value })}
              className="field mono"
            />
          </div>
        ))}
      </div>
      {t.oauth && <p className="mb-3 text-[12px] text-muted">Sign-in opens in your browser.</p>}
      {!st.fuse && <p className="mb-3 text-[12px] text-danger">Install fuse3 to open remotes as folders.</p>}
      {err && <p className="mb-3 text-danger">{err}</p>}
      <div className="flex items-center justify-between">
        <span className="mono text-[11px] text-muted">{st.version}</span>
        <button className="btn-primary disabled:opacity-40" disabled={!name.trim() || busy === 'add'} onClick={() => void add()}>
          {busy === 'add' ? 'Checking…' : 'Add remote'}
        </button>
      </div>
    </div>
  )
}
