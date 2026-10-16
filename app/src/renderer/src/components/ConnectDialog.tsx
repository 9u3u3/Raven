import * as Dialog from '@radix-ui/react-dialog'
import { Seg } from './ui'
import { useEffect, useRef, useState } from 'react'
import { ApiError, get, post } from '../lib/api'
import type { Places } from '../lib/types'
import { navigate, say, useApp } from '../store'
import RcloneView from './RcloneView'

type Recent = { url: string; user: string; domain: string }
type Info = { gio: boolean; fuse: boolean; smbclient: boolean; schemes: Record<string, { ok: boolean; install: string | null }>; recent: Recent[] }
type Problem = { code: string; error: string; shares?: string[]; host?: string; question?: string; fingerprint?: string; install?: string }

// The common cases, so nobody has to know the address forms.
const KINDS: { label: string; hint: string; url: string }[] = [
  { label: 'Windows PC', hint: 'SMB', url: 'smb://' },
  { label: 'Linux or Mac (SSH)', hint: 'SFTP', url: 'sftp://' },
  { label: 'Linux Samba / NAS', hint: 'SMB', url: 'smb://' },
  { label: 'NFS export', hint: 'NFS', url: 'nfs://' },
  { label: 'FTP', hint: 'FTP', url: 'ftp://' },
  { label: 'WebDAV', hint: 'DAV', url: 'davs://' }
]

function scheme(url: string): string {
  const u = url.trim()
  if (u.startsWith('\\\\') || u.startsWith('//')) return 'smb'
  const m = /^([a-z]+):\/\//i.exec(u)
  const s = m ? m[1].toLowerCase() : ''
  return ({ ssh: 'sftp', webdav: 'dav', webdavs: 'davs', cifs: 'smb' } as Record<string, string>)[s] ?? s
}

// Connect to server (section 9.12): gio mounts the share and Raven opens it like a local folder.
export default function ConnectDialog() {
  const open = useApp((s) => s.connectOpen)
  const preset = useApp((s) => s.connectUrl)
  const [url, setUrl] = useState('smb://')
  const [user, setUser] = useState('')
  const [password, setPassword] = useState('')
  const [domain, setDomain] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<Problem | null>(null)
  const [info, setInfo] = useState<Info | null>(null)
  const [tab, setTab] = useState<'server' | 'rclone'>('server')
  const pwRef = useRef<HTMLInputElement>(null)
  const urlRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    if (preset) setUrl(preset)
    setProblem(null)
    get<Info>('/net/info').then(setInfo).catch(() => setInfo(null))
  }, [open, preset])

  const close = (): void => {
    useApp.setState({ connectOpen: false, connectUrl: '' })
    setPassword('')
    setProblem(null)
  }

  const connect = async (opts: { trust?: boolean; to?: string } = {}): Promise<void> => {
    const target = opts.to ?? url
    setBusy(true)
    setProblem(null)
    try {
      const r = await post<{ path: string; places: Places }>('/net/connect', { url: target, user, password, domain, trust: !!opts.trust })
      useApp.setState({ places: r.places })
      close()
      say(`Connected: ${target}`)
      await navigate(r.path)
    } catch (e) {
      const d = e instanceof ApiError && e.data && typeof e.data === 'object' ? (e.data as Problem) : null
      setProblem(d ?? { code: 'error', error: (e as Error).message })
      if (d?.code === 'needs_password' || d?.code === 'auth_failed') requestAnimationFrame(() => pwRef.current?.focus())
    } finally {
      setBusy(false)
    }
  }

  const s = scheme(url)
  const backend = info?.schemes[s]
  const missing = backend && !backend.ok ? backend.install : null
  const recent = (info?.recent ?? []).slice(0, 5)

  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && close()}>
      <Dialog.Portal>
        <Dialog.Overlay className="anim-fade fixed inset-0 bg-black/45 backdrop-blur-md" />
        <Dialog.Content
          className="fixed left-1/2 top-[12%] max-h-[80vh] w-[560px] max-w-[calc(100vw-32px)] -translate-x-1/2 overflow-y-auto float-panel anim-pop p-6"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && tab === 'server' && !busy && problem?.code !== 'pick_share') {
              e.preventDefault()
              void connect({ trust: problem?.code === 'host_key' })
            }
          }}
        >
          <Dialog.Title className="serif mb-3 text-[18px] leading-none">Connect to storage</Dialog.Title>
          <div className="mb-3">
            <Seg options={[['server', 'Server on your network'], ['rclone', 'Cloud and S3']]} value={tab} onChange={setTab} />
          </div>
          <Dialog.Description className="mb-4 text-muted">
            {tab === 'server'
              ? 'A shared folder on another computer.'
              : 'WebDAV, S3, SFTP, Drive, Dropbox, OneDrive (rclone).'}
          </Dialog.Description>
          {tab === 'rclone' ? (
            <RcloneView onDone={close} />
          ) : (
          <>

          <div className="mb-4 flex flex-wrap gap-x-4 gap-y-1">
            {KINDS.map((k) => (
              <button
                key={k.label}
                className="link text-muted transition-colors hover:text-text"
                onClick={() => {
                  setUrl(k.url)
                  setProblem(null)
                  requestAnimationFrame(() => urlRef.current?.focus())
                }}
              >
                {k.label} <span className="mono text-[11px]">{k.hint}</span>
              </button>
            ))}
          </div>

          <div className="mb-4 grid grid-cols-[90px_1fr] items-center gap-y-3">
            <span className="text-muted">Address</span>
            <input
              ref={urlRef}
              autoFocus
              value={url}
              spellCheck={false}
              placeholder="smb://192.168.1.20/Users or sftp://user@192.168.1.20/home/user"
              onChange={(e) => (setUrl(e.target.value), setProblem(null))}
              className="field mono"
            />
            <span className="text-muted">User</span>
            <input value={user} spellCheck={false} placeholder={s === 'sftp' ? 'your user on that computer' : 'empty for a guest'} onChange={(e) => setUser(e.target.value)} className="field" />
            <span className="text-muted">Password</span>
            <input ref={pwRef} type="password" value={password} placeholder={s === 'sftp' ? 'empty when an SSH key works' : ''} onChange={(e) => setPassword(e.target.value)} className="field" />
            {s === 'smb' && (
              <>
                <span className="text-muted">Domain</span>
                <input value={domain} spellCheck={false} placeholder="empty, or WORKGROUP" onChange={(e) => setDomain(e.target.value)} className="field" />
              </>
            )}
          </div>

          {s === 'smb' && !problem && (
            <p className="mb-4 text-[12px] text-muted">
              A Windows PC: use its IP address (ipconfig on that PC) and its user name and password. Leave out the share name to see the list of shares.
            </p>
          )}

          {missing && (
            <div className="anim-pop mb-4 border-l-2 border-danger py-1 pl-3">
              <p className="mb-1">This computer cannot open {s.toUpperCase()} shares yet. Run this in a terminal, then try again:</p>
              <code className="mono select-all text-[12px]">{missing}</code>
            </div>
          )}

          {problem && problem.code === 'host_key' && (
            <div className="anim-pop mb-4 border-l-2 border-accent py-1 pl-3">
              <p className="mb-1">Raven does not know this computer yet.</p>
              {problem.fingerprint && (
                <p className="mb-1 text-[12px] text-muted">
                  Its key: <span className="mono select-all">{problem.fingerprint}</span>
                </p>
              )}
              <p className="mb-2 text-[12px] text-muted">On the other computer, ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub shows the same key when it is the right computer.</p>
              <button className="btn-primary" disabled={busy} onClick={() => void connect({ trust: true })}>
                Trust and connect (Enter)
              </button>
            </div>
          )}

          {problem && problem.code === 'pick_share' && (
            <div className="mb-4">
              <p className="mb-2">{problem.error}:</p>
              {problem.shares && problem.shares.length > 0 ? (
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  {problem.shares.map((sh) => (
                    <button
                      key={sh}
                      className="mono rounded-full border border-accent/50 px-3 py-1 text-accent transition-all hover:bg-accent hover:text-on-accent"
                      onClick={() => {
                        const to = `smb://${problem.host}/${sh}`
                        setUrl(to)
                        void connect({ to })
                      }}
                    >
                      {sh}
                    </button>
                  ))}
                </div>
              ) : (
                <p className="text-muted">This computer shares no folders.</p>
              )}
            </div>
          )}

          {problem && !['host_key', 'pick_share'].includes(problem.code) && (
            <div className="anim-pop mb-4 border-l-2 border-danger py-1 pl-3 text-danger">
              <p>{problem.error}</p>
              {problem.install && <code className="mono select-all text-[12px] text-text">{problem.install}</code>}
            </div>
          )}

          {recent.length > 0 && (
            <div className="mb-4">
              <div className="label mb-1">Recent</div>
              {recent.map((r) => (
                <button
                  key={r.url}
                  className="mono block w-full truncate px-1 py-1 text-left text-[12px] text-muted transition-colors hover:bg-hover hover:text-text"
                  onClick={() => {
                    setUrl(r.url)
                    setUser(r.user || '')
                    setDomain(r.domain || '')
                    setProblem(null)
                    requestAnimationFrame(() => pwRef.current?.focus())
                  }}
                >
                  {r.user ? `${r.user} · ` : ''}
                  {r.url}
                </button>
              ))}
            </div>
          )}

          <div className="flex items-center justify-end gap-5">
            <Dialog.Close className="btn-text text-muted">Cancel (Esc)</Dialog.Close>
            <button className="btn-primary disabled:opacity-40" disabled={busy || url.length < 7 || !!missing} onClick={() => void connect()}>
              {busy ? 'Connecting…' : 'Connect (Enter)'}
            </button>
          </div>
          </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
