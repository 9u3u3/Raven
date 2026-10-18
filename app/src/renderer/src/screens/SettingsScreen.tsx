import { Cloud, Cpu, Database, Download, FolderSearch, Folder, HelpCircle, KeyRound, Lock, Palette, Plug, SlidersHorizontal, type LucideIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { get, post } from '../lib/api'
import { size, tildify } from '../lib/format'
import { FONTS, POLICY_NAMES, loadPolicies, refreshPrivacy, say, setAiMode, setPref, setPrivate, togglePolicy, useApp, type Font } from '../store'
import { PRIVACY_TEXT } from '../lib/privacyText'
import AiProviders from '../components/AiProviders'
import PrivacyWords from '../components/PrivacyWords'
import FeaturePicker, { DeviceLine, useFeatures } from '../components/FeaturePicker'
import OllamaSetup from '../components/OllamaSetup'
import { Chip, Explained, Ghost, Seg, Switch, Track, stagger } from '../components/ui'
import { pickFolder } from '../components/FolderPicker'

// One page at a time: the list on the left picks the page. Each description stays hidden until the user opens it.

function Section({ id, title, text, children }: { id: string; icon?: LucideIcon; title: string; text?: string; children: React.ReactNode; i?: number }) {
  return (
    <section id={`set-${id}`} data-section={id} className="anim-fade mb-4">
      <div className="mb-5 border-b border-line pb-4">
        <Explained label={<span role="heading" aria-level={2} className="serif text-[22px] leading-none">{title}</span>} text={text} />
      </div>
      {children}
    </section>
  )
}
/** Folder rules (PLAN2 P2-5): one row per folder, four switches. Add a folder from its context menu. */
function FolderRules() {
  const policies = useApp((s) => s.policies)
  useEffect(() => void loadPolicies(), [])
  if (!policies.length) return <div className="py-2 text-muted">None. Right-click a folder, Folder rules.</div>
  const cols = `minmax(0,1fr) repeat(${POLICY_NAMES.length}, auto)`
  return (
    <div>
      <div className="grid items-center gap-x-4 border-b border-line pb-1 text-[11px] text-muted" style={{ gridTemplateColumns: cols }}>
        <span />
        {POLICY_NAMES.map(([k, l]) => (
          <span key={k}>{l}</span>
        ))}
      </div>
      {policies.map((p) => (
        <div key={p.path} className="grid items-center gap-x-4 border-b border-line/70 py-1.5" style={{ gridTemplateColumns: cols }}>
          <span className="mono truncate text-[12px]" title={p.path}>
            {p.path}
          </span>
          {POLICY_NAMES.map(([k, l]) => (
            <span key={k} className="flex justify-center" style={{ width: `${l.length}ch` }}>
              <Switch on={!!p[k]} label={l} onChange={() => void togglePolicy(p.path, k)} />
            </span>
          ))}
        </div>
      ))}
    </div>
  )
}

/** Private folders (PLAN4 7.6): no AI and no model reads them. Raven works on them as a plain file manager. */
function PrivateFolders() {
  const policies = useApp((s) => s.policies)
  const home = useApp((s) => s.places?.home ?? '')
  useEffect(() => void loadPolicies(), [])
  const own = policies.filter((p) => p.private)
  return (
    <div>
      <div className="mb-4">
        {own.length === 0 && <p className="py-2 text-muted">No private folders.</p>}
        {own.map((p, k) => (
          <div key={p.path} style={stagger(k)} className="anim-row group flex items-center gap-3 border-b border-line/70 py-2.5 transition-colors hover:bg-hover">
            <Lock size={15} strokeWidth={1.5} className="text-muted" />
            <span className="mono flex-1 truncate" title={p.path}>
              {tildify(p.path, home)}
            </span>
            <button className="link text-muted opacity-0 transition-all hover:text-danger group-hover:opacity-100" title="Let Raven's AI read this folder again" onClick={() => setPrivate(p.path, false)}>
              remove
            </button>
          </div>
        ))}
      </div>
      <button className="btn-primary" onClick={() => void pickFolder('A private folder').then((ps) => ps[0] && setPrivate(ps[0], true))}>
        Add a private folder
      </button>
    </div>
  )
}

type Update = { enabled: boolean; current: string; latest?: string; url?: string; newer?: boolean; error?: string; notes?: string }

/** A small part of a section, with its own heading. */
function Sub({ title, text, children }: { title: string; text?: string; children: React.ReactNode }) {
  return (
    <div className="mb-7">
      <Explained className="mb-3" label={<span className="label">{title}</span>} text={text} />
      {children}
    </div>
  )
}

/** The notes of one version, from CHANGELOG.md or a release: headings and list lines, as plain text. */
function Notes({ text }: { text: string }) {
  return (
    <div className="selectable max-w-[720px] text-[12.5px] leading-relaxed">
      {text.split('\n').map((ln, k) => {
        if (ln.startsWith('## ')) return <div key={k} className="mt-3 font-medium">{ln.slice(3)}</div>
        if (ln.startsWith('### ')) return <div key={k} className="label mt-2">{ln.slice(4)}</div>
        if (ln.startsWith('- ')) return <div key={k} className="pl-4 -indent-3 text-muted">· {ln.slice(2)}</div>
        if (ln.startsWith('  ')) return <div key={k} className="pl-4 text-muted">{ln.trim()}</div>
        return ln.trim() && !ln.startsWith('# ') ? <p key={k} className="mt-1 text-muted">{ln}</p> : null
      })}
    </div>
  )
}

/** Updates (PLAN4 section 6 item 8): the version, the opt-in check (one request a day, no file data) and the notes. */
function UpdatesSection({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  const [u, setU] = useState<Update | null>(null)
  const [about, setAbout] = useState<{ version: string; dev: boolean; changelog: string } | null>(null)
  const [all, setAll] = useState(false)
  useEffect(() => void get<typeof about>('/about').then(setAbout).catch(() => {}), [])
  useEffect(() => {
    if (on) void get<Update>('/update').then(setU).catch(() => setU(null))
    else setU(null)
  }, [on])
  const note = !u?.enabled ? 'Off: Raven sends no request.' : u.error ? u.error : u.newer ? `Raven ${u.latest} is out.` : `Raven ${u.current} is the latest.`
  // The notes of this version: the changelog from its "## <version>" heading to the next one.
  const log = about?.changelog ?? ''
  const start = log.indexOf('\n## ')
  const next = start < 0 ? -1 : log.indexOf('\n## ', start + 4)
  const mine = start < 0 ? '' : log.slice(start + 1, next < 0 ? undefined : next)
  return (
    <Section id="updates" i={8} title="Updates">
      <Row label="Version" status={about?.dev ? 'Development build' : 'Release build'}>
        <span className="mono">{about?.version ?? ''}</span>
      </Row>
      <Row label="Check for updates" text="Raven asks GitHub once a day for the newest version. The request holds no file data." status={note}>
        <Switch on={on} label="Check for updates" onChange={onChange} />
      </Row>
      {u?.newer && u.url && (
        <div className="mt-3 flex items-center gap-4">
          <button className="btn-primary" onClick={() => void window.raven?.openUrl(u.url!).then((e) => e && say(e, 'error'))}>
            Get Raven {u.latest}
          </button>
        </div>
      )}
      {u?.newer && u.notes && (
        <div className="mt-4">
          <div className="label mb-1">What is new in {u.latest}</div>
          <Notes text={u.notes} />
        </div>
      )}
      {mine && (
        <div className="mt-5">
          <Notes text={all ? log.slice(start + 1) : mine} />
          <Ghost tone="plain" onClick={() => setAll((v) => !v)}>
            {all ? 'Show this version only' : 'Show all versions'}
          </Ghost>
        </div>
      )}
    </Section>
  )
}

/** One setting: the label (its description opens from the chevron), a state line that always shows, the control. */
function Row({ label, text, status, children }: { label: string; text?: string; status?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-line/70 py-2">
      <div className="min-w-0">
        <Explained label={<span>{label}</span>} text={text} />
        {status && <div className="max-w-[520px] text-[12.5px] leading-snug text-muted">{status}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  )
}
const KEYS: [string, string][] = [
  ['Ctrl+K or /', 'Command bar'],
  ['Ctrl+Enter', 'Full results screen'],
  ['Tab (command bar)', 'Change mode: Find, Ask, Act'],
  ['Space or I', 'Inspector'],
  ['Enter', 'Open'],
  ['Backspace or Alt+Left', 'Back'],
  ['Alt+Right', 'Forward'],
  ['Alt+Up', 'Parent folder'],
  ['Ctrl+L', 'Edit path'],
  ['F2', 'Rename (with many items selected: batch rename)'],
  ['F3', 'Dual pane'],
  ['Tab (dual pane)', 'Use the other pane'],
  ['Ctrl+C, Ctrl+X, Ctrl+V', 'Copy, cut, paste'],
  ['Delete / Shift+Delete', 'Trash / permanent delete'],
  ['Ctrl+Z / Ctrl+Shift+Z', 'Undo / redo'],
  ['Ctrl+A / Ctrl+Shift+I', 'Select all / invert selection'],
  ['Ctrl+Shift+N', 'New folder'],
  ['Ctrl+H', 'Hidden files'],
  ['Ctrl+1, Ctrl+2', 'List view, grid view'],
  ['Ctrl+B', 'Left column'],
  ['Ctrl+,', 'Settings'],
  ['Ctrl+T / Ctrl+W', 'New tab / close tab'],
  ['Ctrl+Shift+T', 'Open the last closed tab again'],
  ['Ctrl+Tab, Ctrl+PageDown / PageUp', 'Next / previous tab'],
  ['Alt+1 … Alt+9', 'Go to tab 1 … the last tab'],
  ['Middle-click', 'Open a folder in a new tab, or close a tab'],
  ['G then F / S / O / P', 'Go to Files, Space, Organize, Privacy'],
  ['Ctrl+Shift+D', 'Latency overlay (development build)'],
  ['F12', 'Developer tools (development build)']
]

/** Encrypted index (secure.py): SQLCipher with a passphrase. The sidecar restarts to swap the files. */
function EncryptionBlock() {
  const [st, setSt] = useState<{ enabled: boolean; keyring: boolean; remembered: boolean } | null>(null)
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [remember, setRemember] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => void get<typeof st>('/security').then(setSt).catch(() => {}), [])
  if (!st) return null

  // After the swap the sidecar starts locked: unlock it with the same passphrase, then load the app again.
  const restartAndOpen = async (unlockWith: string | null): Promise<void> => {
    setBusy('Restarting the index…')
    await window.raven?.restartSidecar()
    for (let i = 0; i < 100; i++) {
      await new Promise((r) => setTimeout(r, 200))
      try {
        const h = await get<{ locked: boolean }>('/health')
        if (h.locked && unlockWith) await post('/security/unlock', { passphrase: unlockWith, remember })
        break
      } catch {
        /* the new sidecar is not up yet */
      }
    }
    location.reload()
  }
  const turnOn = async (): Promise<void> => {
    setErr(null)
    if (pw.length < 8) return setErr('The passphrase needs at least 8 characters.')
    if (pw !== pw2) return setErr('The two passphrases are not the same.')
    setBusy('Encrypting the index…')
    try {
      await post('/security/encrypt', { passphrase: pw, remember })
      await restartAndOpen(pw)
    } catch (e) {
      setErr((e as Error).message)
      setBusy(null)
    }
  }
  const turnOff = async (): Promise<void> => {
    setErr(null)
    setBusy('Decrypting the index…')
    try {
      await post('/security/decrypt', { passphrase: pw })
      await restartAndOpen(null)
    } catch (e) {
      setErr((e as Error).message)
      setBusy(null)
    }
  }
  return (
    <Sub title="Encrypted index" text="The index holds the text of your indexed files. Encryption keeps it unreadable without the passphrase.">
      <div className="mb-3 flex max-w-[520px] flex-col gap-2">
        <input type="password" value={pw} placeholder={st.enabled ? 'Passphrase, to turn encryption off' : 'New passphrase (8 characters or more)'} onChange={(e) => setPw(e.target.value)} className="field" />
        {!st.enabled && <input type="password" value={pw2} placeholder="The same passphrase again" onChange={(e) => setPw2(e.target.value)} className="field" />}
        {!st.enabled && st.keyring && (
          <label className="flex items-center gap-3 text-muted">
            <Switch on={remember} onChange={setRemember} label="Remember on this computer" /> Remember on this computer (system keyring)
          </label>
        )}
        {!st.enabled && !st.keyring && <span className="text-[12px] text-muted">No keyring: Raven asks at each start.</span>}
      </div>
      {err && <p className="mb-2 rounded-lg bg-danger/10 px-3 py-2 text-danger">{err}</p>}
      {busy ? (
        <span className="mono text-[12px] text-muted">{busy}</span>
      ) : st.enabled ? (
        <Ghost tone="danger" disabled={!pw} onClick={() => void turnOff()}>
          Turn encryption off
        </Ghost>
      ) : (
        <button className="btn-primary disabled:opacity-40" disabled={!pw} onClick={() => void turnOn()}>
          Encrypt the index
        </button>
      )}
    </Sub>
  )
}

/** Binary vectors with a rescore (section 13.12 item 3): automatic above 50,000 chunks. */
/** Features and models (PLAN4 section 5.4): the device, Ollama, and one row per feature with its fit. */
function FeaturesSection() {
  const [st, load, setSt] = useFeatures()
  const [testing, setTesting] = useState(false)
  if (!st) return null
  const bench = async (): Promise<void> => {
    setTesting(true)
    try {
      await post('/device/bench')
      await load(true)
    } finally {
      setTesting(false)
    }
  }
  const usesOllama = st.features.some((f) => f.ollama && (f.always || f.on))
  return (
    <Section id="features" title="Features and models" i={3}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <DeviceLine d={st.device} />
        <span className="flex gap-5">
          <Ghost tone="plain" disabled={testing} onClick={() => void bench()} title="A CPU test of about 1 s. It makes the advice more exact.">
            {testing ? 'Testing…' : st.device.bench ? `Speed ${st.device.bench} GFLOPS` : 'Test the speed'}
          </Ghost>
          <Ghost tone="plain" onClick={() => void load(true)}>
            Check again
          </Ghost>
        </span>
      </div>
      <Row label="Ollama" status={st.ollama.running ? `${st.ollama.version ?? ''} running${st.ollama.managed ? ', installed by Raven' : ''}` : st.ollama.installed ? 'installed, not running' : 'not installed'}>
        <Chip tone={st.ollama.running ? 'good' : usesOllama ? 'bad' : 'plain'}>{st.ollama.running ? 'running' : 'off'}</Chip>
      </Row>
      {usesOllama && !st.ollama.running && (
        <div className="mt-3">
          <OllamaSetup status={st.ollama} onChange={() => void load()} />
        </div>
      )}
      <div className="mt-4">
        <FeaturePicker st={st} mode="live" onStatus={setSt} />
      </div>
    </Section>
  )
}

type Notice = { name: string; version?: string; license: string; source?: string; notice?: string }
type About = { version: string; dev: boolean; notices: { models: Notice[]; programs: Notice[]; python: Notice[]; node: Notice[] } }

const ISSUES = 'https://github.com/9u3u3/Raven/issues/new'

/** A new GitHub issue with the version and a short form. The diagnostics stay out unless the user pastes them. */
function issueUrl(version: string): string {
  const body = `Raven ${version} on ${window.raven?.platform ?? 'unknown'}\n\nWhat happened:\n\nWhat you expected:\n\nSteps:\n1.\n\nDiagnostics (Settings, Help, Copy diagnostics), if you want to add them:\n`
  return `${ISSUES}?title=${encodeURIComponent('Problem: ')}&body=${encodeURIComponent(body)}`
}

/** What Raven sends, where and when (lib/privacyText.ts, the same text as docs/PRIVACY.md). */
export function PrivacySent() {
  return (
    <div className="mt-3 grid max-w-[760px] gap-4 md:grid-cols-2">
      {PRIVACY_TEXT.map((g) => (
        <div key={g.title}>
          <div className="label mb-1">{g.title}</div>
          {g.lines.map((l) => (
            <p key={l} className="mb-1 text-[12.5px] leading-snug text-muted">
              {l}
            </p>
          ))}
        </div>
      ))}
    </div>
  )
}

/** Help (PLAN4 B5, B6): the version, "Copy diagnostics", the log folder and the third-party notices. */
function HelpSection() {
  const [about, setAbout] = useState<About | null>(null)
  const [logDir, setLogDir] = useState('')
  const [open, setOpen] = useState(false)
  const [sent, setSent] = useState(false)
  const [busy, setBusy] = useState(false)
  useEffect(() => void get<About>('/about').then(setAbout).catch(() => {}), [])
  const copy = async (): Promise<void> => {
    setBusy(true)
    try {
      const r = await get<{ text: string; dir: string }>('/diagnostics')
      setLogDir(r.dir)
      await navigator.clipboard.writeText(r.text)
      say('Copied the diagnostics. They hold no file content and no keys.')
    } catch (e) {
      say((e as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  const openLogs = async (): Promise<void> => {
    const dir = logDir || (await get<{ dir: string }>('/diagnostics')).dir
    setLogDir(dir)
    const err = await window.raven?.openPath(dir)
    if (err) say(err, 'error')
  }
  const group = (title: string, rows: Notice[]) =>
    rows.length > 0 && (
      <div className="mb-4">
        <div className="label mb-1">{title}</div>
        {rows.map((n) => (
          <div key={`${n.name}${n.version ?? ''}`} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-3 border-b border-line/50 py-1 text-[12px]">
            <span className="min-w-0 truncate">
              {n.name} <span className="mono text-muted">{n.version}</span>
            </span>
            <span className="min-w-0 truncate text-muted" title={n.notice ?? n.license}>
              {n.license}
              {n.notice ? ` · ${n.notice}` : ''}
            </span>
          </div>
        ))}
      </div>
    )
  return (
    <Section id="help" title="Help" i={9}>
      <Row label="Welcome cards" text="The cards from the first start: what Raven does, and what stays on this computer.">
        <Ghost onClick={() => useApp.setState({ welcome: true })}>Show</Ghost>
      </Row>
      <Row label="Guide" text="Press ? anywhere for the guide, the tour and a short help page for the screen you are on.">
        <kbd className="kbd">?</kbd>
      </Row>
      <Row label="Report a problem" text="Opens a new issue on GitHub in the browser. Copy the diagnostics first and paste them in, if you want to.">
        <Ghost onClick={() => void window.raven?.openUrl(issueUrl(about?.version ?? '')).then((e) => e && say(e, 'error'))}>Report a problem</Ghost>
      </Row>
      <Row label="Diagnostics" text="A text report for a bug report: the version, the device, the settings without keys, and the last log lines. It holds no file content.">
        <Ghost disabled={busy} onClick={() => void copy()}>
          {busy ? 'Collecting…' : 'Copy diagnostics'}
        </Ghost>
      </Row>
      <Row label="Log files" text="Raven keeps 5 log files of 5 MB each.">
        <Ghost tone="plain" onClick={() => void openLogs()}>
          Open the log folder
        </Ghost>
      </Row>
      <Row label="Third-party notices" text="The licenses of the models, programs and libraries that Raven uses.">
        <Ghost tone="plain" onClick={() => setOpen((v) => !v)}>
          {open ? 'Hide' : 'Show'}
        </Ghost>
      </Row>
      {open && about && (
        <div className="mt-4">
          {group('Models', about.notices.models)}
          {group('Programs', about.notices.programs)}
          {group('Python libraries (Raven core)', about.notices.python)}
          {group('JavaScript libraries (the window)', about.notices.node)}
        </div>
      )}
      <Row label="What Raven sends" text="What leaves this computer, where it goes, and when.">
        <Ghost tone="plain" onClick={() => setSent((v) => !v)}>
          {sent ? 'Hide' : 'Show'}
        </Ghost>
      </Row>
      {sent && <PrivacySent />}
    </Section>
  )
}

function PerformanceBlock() {
  const [st, setSt] = useState<{ mode: string; chunks: number; copies: number; active: boolean; threshold: number } | null>(null)
  const load = (): void => void get<typeof st>('/search/vectors').then(setSt).catch(() => {})
  useEffect(load, [])
  if (!st) return null
  const set = (v: string): void => void post('/settings', { key: 'binary_vectors', value: v }).then(() => setTimeout(load, 1500))
  return (
    <Sub title="Binary vectors" text="A smaller copy of each vector makes search by meaning faster on a large index.">
      <div className="mb-3">
        <Seg
          options={[['auto', `Automatic (from ${st.threshold.toLocaleString()} chunks)`], ['on', 'On'], ['off', 'Off']]}
          value={(st.mode as 'auto' | 'on' | 'off') ?? 'auto'}
          onChange={set}
        />
      </div>
      <div className="mono text-[11px] text-muted">
        {st.chunks.toLocaleString()} chunks · {st.copies.toLocaleString()} binary copies · {st.active ? 'binary search on' : 'exact search'}
      </div>
    </Sub>
  )
}

type IndexSize = { bytes: number; free_bytes: number; free_share: number; auto_vacuum: string }

/** The size of the index file, and "Make the index smaller" (PLAN3 P0-8). Raven also does this by itself when the
 * laptop is idle and on power. */
function IndexFile() {
  const [info, setInfo] = useState<IndexSize | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const load = (): void => void get<IndexSize>('/index/size').then(setInfo).catch(() => {})
  useEffect(load, [])
  if (!info) return null
  const compact = async (): Promise<void> => {
    setBusy(true)
    setMsg('')
    try {
      const r = await post<{ freed_bytes: number }>('/index/compact', {})
      setMsg(r.freed_bytes ? `${size(r.freed_bytes)} freed.` : 'The index has no free space to give back.')
      load()
    } catch (e) {
      setMsg((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 text-[12.5px]">
      <span className="text-muted">Index file</span>
      <span className="mono">{size(info.bytes)}</span>
      <span className="text-muted">{info.free_bytes ? `${size(info.free_bytes)} of it is free space` : 'no free space inside'}</span>
      {info.free_share > 0.02 && (
        <Ghost disabled={busy} onClick={() => void compact()}>
          {busy ? 'Making it smaller…' : 'Make the index smaller'}
        </Ghost>
      )}
      {msg && <span className="text-muted">{msg}</span>}
    </div>
  )
}

/** Raven as an MCP server (section 13.12 item 1): off by default, with the config block to paste. */
type McpClient = { id: string; name: string; found: boolean; added: boolean; current: boolean; path: string }

/** MCP clients (PLAN4 R4): one button adds Raven to a client's config. Raven keeps the client's other servers and
 * writes a copy of the file first (<name>.raven-backup). */
function McpSection() {
  const [cfg, setCfg] = useState<{ enabled: boolean; config: unknown } | null>(null)
  const [clients, setClients] = useState<McpClient[]>([])
  const load = (): void => {
    void get<{ enabled: boolean; config: unknown }>('/mcp/config').then(setCfg).catch(() => {})
    void get<{ clients: McpClient[] }>('/mcp/clients').then((r) => setClients(r.clients)).catch(() => {})
  }
  useEffect(load, [])
  if (!cfg) return null
  const text = JSON.stringify(cfg.config, null, 2)
  const act = (c: McpClient, add: boolean): void => {
    const run = (): void =>
      void post<{ clients: McpClient[] }>(`/mcp/clients/${add ? 'add' : 'remove'}`, { id: c.id })
        .then((r) => (setClients(r.clients), say(add ? `Added Raven to ${c.name}. Restart ${c.name} to use it.` : `Removed Raven from ${c.name}`)))
        .catch((e) => say((e as Error).message, 'error'))
    useApp.setState({
      confirm: {
        title: add ? `Add Raven to ${c.name}` : `Remove Raven from ${c.name}`,
        body: add
          ? `Raven adds its entry to ${c.path} and keeps the other entries. It saves a copy of the file first. ${c.name} can then search and read your indexed files through Raven, with redaction.`
          : `Raven removes its entry from ${c.path}. The other entries stay.`,
        action: add ? 'Add' : 'Remove',
        danger: !add,
        run
      }
    })
  }
  return (
    <Section id="integrations" title="Integrations" i={6} text="MCP clients on this computer can search and read your indexed files through Raven, with redaction. They cannot change files.">
      <div className="mb-4 flex items-center gap-3">
        <Switch on={cfg.enabled} label="Let MCP clients use Raven" onChange={(v) => void post('/settings', { key: 'mcp_enabled', value: v }).then(load)} />
        <span className="font-medium">Let MCP clients use Raven</span>
        <Chip tone={cfg.enabled ? 'good' : 'plain'}>{cfg.enabled ? 'on' : 'off'}</Chip>
      </div>
      <div className="mb-5 max-w-[720px]">
        {clients.map((c) => (
          <div key={c.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line/70 py-2">
            <span className="w-[130px] font-medium">{c.name}</span>
            <Chip tone={c.added ? (c.current ? 'good' : 'warn') : 'plain'}>{c.added ? (c.current ? 'added' : 'added, old entry') : c.found ? 'found' : 'not found'}</Chip>
            <span className="mono min-w-0 flex-1 truncate text-[11px] text-muted" title={c.path}>
              {c.path}
            </span>
            {(!c.added || !c.current) && (
              <Ghost disabled={!c.found && c.id === 'claude_code'} onClick={() => act(c, true)}>
                {c.added ? 'Update' : 'Add'}
              </Ghost>
            )}
            {c.added && (
              <Ghost tone="plain" onClick={() => act(c, false)}>
                Remove
              </Ghost>
            )}
          </div>
        ))}
      </div>
      <div className="mb-1 text-muted">Or add this block to the client's MCP config by hand:</div>
      <pre className="selectable max-w-[720px] overflow-x-auto rounded-lg bg-console-bg p-4 font-mono text-[11.5px] text-console-text">{text}</pre>
      <div className="mt-3">
        <Ghost onClick={() => void navigator.clipboard.writeText(text).then(() => say('Copied the MCP config'))}>Copy the config</Ghost>
      </div>
    </Section>
  )
}

type DataInfo = { dir: string; models_dir: string; models_inside: boolean; total: number; parts: { id: string; label: string; bytes: number }[] }

/** Data (PLAN4 section 6 item 7): the data folder, its parts, the index size, the settings export, and "Delete
 * all Raven data". The main process does the delete, because the sidecar cannot delete its own open files. */
function DataSection() {
  const [d, setD] = useState<DataInfo | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => void get<DataInfo>('/data').then(setD).catch(() => {}), [])
  const exportSettings = async (): Promise<void> => {
    const out = await get<unknown>('/data/export')
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 2)], { type: 'application/json' }))
    a.download = 'raven-settings.json'
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 10_000)
  }
  const deleteAll = (): void =>
    useApp.setState({
      confirm: {
        title: 'Delete all Raven data',
        body:
          'Raven deletes its index, its models, the Ollama that it installed, the keys in the keyring, the settings and the logs. Then it starts again as new. ' +
          'Your files stay, and the Trash stays. Models in a system Ollama stay: remove them with "ollama rm". You cannot undo this.',
        action: 'Delete all',
        danger: true,
        run: () => {
          setBusy(true)
          void window.raven?.deleteAllData().then((err) => {
            setBusy(false)
            if (err) say(err, 'error')
          })
        }
      }
    })
  return (
    <Section id="data" i={7} title="Data" text="Raven keeps its index, models and settings in one folder. Your files stay where they are.">
      {d && (
        <>
          <Row label="Data folder" status={d.models_inside ? undefined : `Models: ${d.models_dir}`}>
            <span className="flex items-center gap-4">
              <span className="mono max-w-[360px] truncate text-[12px]" title={d.dir}>
                {d.dir}
              </span>
              <Ghost tone="plain" onClick={() => void window.raven?.openPath(d.dir).then((e) => e && say(e, 'error'))}>
                Open
              </Ghost>
            </span>
          </Row>
          <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1">
            <Chip>{size(d.total) || '0 B'} in all</Chip>
            {d.parts
              .filter((p) => p.bytes > 0)
              .map((p) => (
                <span key={p.id} className="text-[12.5px] text-muted">
                  {p.label} <span className="mono text-text">{size(p.bytes)}</span>
                </span>
              ))}
          </div>
        </>
      )}
      <IndexFile />
      <div className="mt-5">
        <Row label="Export the settings" text="A JSON file with your settings and the provider order. It holds no keys.">
          <Ghost onClick={() => void exportSettings().catch((e) => say((e as Error).message, 'error'))}>Export</Ghost>
        </Row>
        <Row label="Delete all Raven data" text="The index, the models, the keys, the settings and the logs. Your files and the Trash stay.">
          <Ghost tone="danger" disabled={busy || !window.raven} onClick={deleteAll}>
            {busy ? 'Deleting…' : 'Delete all'}
          </Ghost>
        </Row>
      </div>
    </Section>
  )
}

/** Start with the computer (Settings, General). Only a packaged app can do it; on Linux, only the AppImage. */
function StartRow() {
  const [st, setSt] = useState<{ supported: boolean; on: boolean } | null>(null)
  useEffect(() => void window.raven?.loginItem().then(setSt), [])
  if (!st) return null
  return (
    <Row
      label="Start with the computer"
      text={st.supported ? 'Raven opens minimized when you log in, and keeps the index up to date.' : undefined}
      status={st.supported ? undefined : 'Only the installed app can start with the computer.'}
    >
      <Switch on={st.on} label="Start with the computer" onChange={(v) => void window.raven?.loginItem(v).then(setSt)} />
    </Row>
  )
}

export default function SettingsScreen() {
  const index = useApp((s) => s.index)
  const home = useApp((s) => s.places?.home ?? '')
  const theme = useApp((s) => s.theme)
  const scale = useApp((s) => s.scale)
  const font = useApp((s) => s.font)
  const showHidden = useApp((s) => s.showHidden)
  const fullView = useApp((s) => s.fullView)
  const [settings, setSettings] = useState<Record<string, unknown>>({})
  const [manual, setManual] = useState('')
  const roots = index?.roots ?? []

  const reload = (): Promise<void> =>
    get<Record<string, unknown>>('/settings').then(setSettings)
  useEffect(() => {
    void reload()
    void refreshPrivacy() // the AI mode
  }, [])

  const setRoots = async (next: string[]): Promise<void> => {
    try {
      await post('/index/roots', { roots: next })
      useApp.setState({ index: await get('/index/status') })
    } catch (e) {
      say((e as Error).message, 'error')
    }
  }

  const addFolder = async (): Promise<void> => {
    const picked = await pickFolder('A folder to index')
    if (picked.length) await setRoots([...roots, ...picked])
  }

  const setSetting = async (key: string, value: unknown): Promise<void> => {
    await post('/settings', { key, value })
    setSettings((s) => ({ ...s, [key]: key.endsWith('_api_key') ? `…${String(value).slice(-4)}` : value }))
  }

  // The order of PLAN4 section 6.
  const NAV: [string, string, LucideIcon][] = [
    ['general', 'General', FolderSearch],
    ['appearance', 'Appearance', Palette],
    ['features', 'Features and models', Cpu],
    ['cloud', 'AI providers', Cloud],
    ['privacy', 'Privacy', Lock],
    ['integrations', 'Integrations', Plug],
    ['data', 'Data', Database],
    ['updates', 'Updates', Download],
    ['help', 'Help', HelpCircle],
    ['advanced', 'Advanced', SlidersHorizontal]
  ]
  const aiMode = useApp((s) => s.privacy?.ai_mode ?? 'hybrid')
  const scroller = useRef<HTMLDivElement>(null)
  const tab = useApp((s) => s.settingsTab)
  const here = NAV.some(([id]) => id === tab) ? tab : 'general'
  const [keyFilter, setKeyFilter] = useState('')
  const jump = (id: string): void => {
    useApp.setState({ settingsTab: id })
    scroller.current?.scrollTo({ top: 0 })
  }
  const keys = KEYS.filter(([k, v]) => `${k} ${v}`.toLowerCase().includes(keyFilter.toLowerCase()))
  const used = index ? (index.files ? 1 - index.text_pending / Math.max(index.files, 1) : 0) : 0

  return (
    <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto">
      <div className="flex gap-4 px-4 pb-6 pt-6">
        <nav className="sticky top-0 h-fit w-[180px] shrink-0 self-start pt-1" aria-label="Settings pages">
          {NAV.map(([id, label], n) => (
            <button key={id} aria-current={here === id ? 'page' : undefined} onClick={() => jump(id)} className="group relative flex w-full items-baseline gap-3 py-1.5 text-left">
              <span className={`serif w-6 text-[17px]  leading-none transition-colors duration-300 ${here === id ? 'text-accent' : 'text-muted/70'}`}>{String(n + 1).padStart(2, '0')}</span>
              <span className={`transition-all duration-300 ${here === id ? 'translate-x-1 text-text' : 'text-muted group-hover:text-text'}`}>{label}</span>
              <span className={`absolute -left-4 top-1/2 h-px bg-accent transition-all duration-300 ${here === id ? 'w-3' : 'w-0'}`} />
            </button>
          ))}
        </nav>

        <div className="min-w-0 flex-1">

          {here === 'general' && (
          <Section id="general" i={1} title="General">
            <StartRow />
            <div className="label mb-2 mt-6">Indexed folders</div>
            {roots.length === 0 && <p className="mb-3 text-muted">No folders are indexed yet. Add one to start.</p>}
            <div className="mb-5">
              {roots.map((r, k) => (
                <div key={r} style={stagger(k)} className="anim-row group flex items-center gap-3 border-b border-line/70 py-2.5 transition-colors hover:bg-hover">
                  <Folder size={15} strokeWidth={1.5} className="text-k-folder" />
                  <span className="mono flex-1 truncate">{tildify(r, home)}</span>
                  <button className="link text-muted opacity-0 transition-all hover:text-danger group-hover:opacity-100" title="Stop indexing this folder" onClick={() => void setRoots(roots.filter((x) => x !== r))}>
                    remove
                  </button>
                </div>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <button className="btn-primary" onClick={() => void addFolder()}>
                Add a folder
              </button>
              <input
                value={manual}
                placeholder="or type a path"
                spellCheck={false}
                onChange={(e) => setManual(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && manual.trim()) {
                    void setRoots([...roots, manual.trim()])
                    setManual('')
                  }
                }}
                className="field mono w-80"
              />
            </div>
            {index && (
              <div className="mt-5">
                <div className="mb-2.5 flex flex-wrap gap-x-6 gap-y-1">
                  <Chip>{index.files.toLocaleString()} items</Chip>
                  <Chip>{index.chunks.toLocaleString()} text chunks</Chip>
                  <Chip tone={index.text_pending ? 'warn' : 'good'}>{index.text_pending} waiting for text</Chip>
                  <Chip tone={index.embed_pending ? 'warn' : 'good'}>{index.embed_pending} waiting for meaning</Chip>
                </div>
                <Track pct={used * 100} color="var(--accent)" />
              </div>
            )}
          </Section>
          )}

          {here === 'appearance' && (
          <Section id="appearance" i={2} title="Appearance">
            <div className="mb-5 grid grid-cols-3 gap-3">
              {(['system', 'light', 'dark'] as const).map((t) => {
                const on = theme === t
                const light = { bg: '#f7f6fa', bar: '#dedae7', ink: '#17151d', acc: '#6a4ce0' }
                const dark = { bg: '#131217', bar: '#33303d', ink: '#ebe8f2', acc: '#a88bfa' }
                const pick = (c: typeof light): React.ReactNode => (
                  <div className="flex h-full flex-1 flex-col gap-1.5 p-2.5" style={{ background: c.bg }}>
                    <span className="h-1.5 w-8 rounded-full" style={{ background: c.acc }} />
                    <span className="h-1.5 w-full rounded-full" style={{ background: c.bar }} />
                    <span className="h-1.5 w-3/4 rounded-full" style={{ background: c.bar }} />
                    <span className="mt-auto h-1.5 w-1/2 rounded-full" style={{ background: c.ink, opacity: 0.6 }} />
                  </div>
                )
                return (
                  <button
                    key={t}
                    onClick={() => setPref('theme', t)}
                    className="group text-left transition-transform duration-300 hover:-translate-y-0.5"
                  >
                    <div className={`flex h-[84px] overflow-hidden border transition-colors duration-300 ${on ? 'border-accent' : 'border-line group-hover:border-muted'}`}>{t === 'system' ? <>{pick(light)}{pick(dark)}</> : pick(t === 'light' ? light : dark)}</div>
                    <div className="mt-2 flex items-center gap-2.5">
                      <span className={on ? 'text-text' : 'text-muted'}>{t === 'system' ? 'Follow the system' : t === 'light' ? 'Light' : 'Dark'}</span>
                      <span className={`h-[7px] w-[7px] rounded-full transition-all duration-300 ${on ? 'scale-100 bg-accent' : 'scale-0'}`} />
                    </div>
                  </button>
                )
              })}
            </div>
            <Row label="Font">
              <select value={font} onChange={(e) => setPref('font', e.target.value as Font)} className="field">
                {FONTS.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </Row>
            <Row label="Text size">
              <div className="flex items-center gap-4">
                <input type="range" min={0.9} max={1.3} step={0.05} value={scale} onChange={(e) => setPref('scale', Number(e.target.value))} className="w-44 accent-[var(--accent)]" />
                <span className="mono w-10 text-right text-[12px]">{Math.round(scale * 100)}%</span>
                <span className="text-muted" style={{ fontSize: `${scale * 1.2}rem` }}>Aa</span>
              </div>
            </Row>
          </Section>
          )}

          {here === 'features' && <FeaturesSection />}

          {here === 'cloud' && (
          <Section id="cloud"
            i={4}
            title="AI providers"
            text="Heavy tasks and Think harder can use a cloud model, after redaction and your review. Raven tries the providers in this order and uses the first one that works."
          >
            <Row label="AI mode" text={aiMode === 'local' ? 'Local only: nothing leaves this computer.' : 'Hybrid: only redacted text leaves, for hard tasks, after you review it.'}>
              <Seg
                options={[
                  ['local', 'Local only'],
                  ['hybrid', 'Hybrid']
                ]}
                value={aiMode === 'local' ? 'local' : 'hybrid'}
                onChange={(v) => void setAiMode(v)}
              />
            </Row>
            <div className="mt-5">
              <AiProviders />
            </div>
          </Section>
          )}

          {here === 'privacy' && (
          <Section id="privacy" i={5} title="Privacy" text="What Raven hides before a cloud request, and what no AI reads at all. The Privacy screen shows the ledger of what went.">
            <Sub
              title="Words to always hide"
              text="Family names, a company, a client, a project code. Raven hides these words in every cloud request, in text and in file names. Case does not count."
            >
              <PrivacyWords />
            </Sub>
            <Sub
              title="Private folders"
              text="No AI and no model reads a private folder, local or cloud. Raven does not index it, search its content, summarize it, organize it, name its files or learn from it. Photos, videos and audio never go to a cloud AI, in any folder."
            >
              <PrivateFolders />
            </Sub>
            <EncryptionBlock />
            <Sub title="What Raven sends">
              <PrivacySent />
            </Sub>
          </Section>
          )}

          {here === 'integrations' && <McpSection />}
          {here === 'data' && <DataSection />}
          {here === 'updates' && <UpdatesSection on={!!settings.update_check} onChange={(v) => void setSetting('update_check', v)} />}
          {here === 'help' && <HelpSection />}

          {here === 'advanced' && (
          <Section id="advanced" i={10} title="Advanced">
            <Sub title="Power user">
            <Row label="Power user mode">
              <Switch
                on={!!settings.power_user}
                label="Power user mode"
                onChange={(v) => {
                  void setSetting('power_user', v)
                  useApp.setState({ powerUser: v })
                }}
              />
            </Row>
            <Row label="Full view" text="Root folder, hidden files and system folders in the left column.">
              <Switch
                on={fullView}
                label="Full view"
                onChange={(v) => {
                  setPref('fullView', v)
                  if (v) setPref('showHidden', true)
                }}
              />
            </Row>
            <Row label="Show hidden files">
              <Switch on={showHidden} label="Show hidden files" onChange={(v) => setPref('showHidden', v)} />
            </Row>
            <Row label="Index hidden files and folders">
              <Switch on={!!settings.index_hidden} label="Index hidden files" onChange={(v) => void setSetting('index_hidden', v)} />
            </Row>
            <Row
              label="Read code projects too"
              text="Off: names only in projects and SDKs. On: a large home can take days."
            >
              <Switch on={!!settings.index_code} label="Read code projects" onChange={(v) => void setSetting('index_code', v)} />
            </Row>
            </Sub>
            <Sub title="Folder rules">
              <FolderRules />
            </Sub>
            <PerformanceBlock />
            <Sub title="Shortcuts">
            <div className="relative mb-3 max-w-[360px]">
              <KeyRound size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
              <input value={keyFilter} onChange={(e) => setKeyFilter(e.target.value)} placeholder="Find a shortcut" className="field w-full !pl-9" spellCheck={false} />
            </div>
            <div className="grid grid-cols-[minmax(0,260px)_1fr] items-center gap-x-4 gap-y-1.5">
              {keys.map(([k, v]) => (
                <div key={k} className="contents">
                  <span>
                    <kbd className="kbd !h-auto !whitespace-normal !py-1 text-left">{k}</kbd>
                  </span>
                  <span className="text-muted">{v}</span>
                </div>
              ))}
              {!keys.length && <span className="col-span-2 text-muted">No shortcut matches “{keyFilter}”.</span>}
            </div>
            </Sub>
          </Section>
          )}
        </div>
      </div>
    </div>
  )
}
