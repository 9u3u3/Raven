import { Check, Copy } from 'lucide-react'
import { useEffect, useState } from 'react'
import KeyField from '../components/KeyField'
import ClaudeAccount from '../components/ClaudeAccount'
import { indexText } from '../components/StatusLine'
import { Mark } from '../components/TopBar'
import { Chip, Ghost, Seg, Track, TreeGuides, stagger } from '../components/ui'
import { get, on, post } from '../lib/api'
import { size, tildify } from '../lib/format'
import { navigate, refreshPrivacy, useApp } from '../store'
import { pickFolder } from '../components/FolderPicker'

type Job = { state: string; done: number; total: number; error: string | null }
type ModelRow = { name: string; label: string; adds: string; mb: number; present: boolean; job?: Job | null }
type Models = { files: ModelRow[]; ollama: ModelRow[]; ollama_running: boolean; dir: string }
type Tool = { name: string; adds: string; ok: boolean; install: string }
type Check = {
  core: { version: string; data_dir: string; models_dir: string; platform: string; distro: string; frozen: boolean }
  ollama: { installed: boolean; running: boolean; url: string; install: string }
  models: Models
  tools: Tool[]
  install_all: string
  cloud: { groq: boolean; claude: boolean; anthropic: boolean; ai_mode: string }
}

const MODES: { id: 'local' | 'hybrid' | 'ask'; label: string; text: string }[] = [
  { id: 'local', label: 'Local only', text: 'Nothing leaves this computer. Heavy questions are slower.' },
  { id: 'hybrid', label: 'Hybrid', text: 'Cloud only after local redaction.' },
  { id: 'ask', label: 'Ask each time', text: 'Raven shows each payload and waits for your OK before it sends it.' }
]

// The usual folders of a person's files. Home holds everything, and a large home folder takes hours to index.
const SUGGESTED = ['Desktop', 'Documents', 'Downloads', 'Pictures']

function State({ ok, text }: { ok: boolean | null; text: string }) {
  return <Chip tone={ok === null ? 'plain' : ok ? 'good' : 'bad'}>{text}</Chip>
}

function pct(j?: Job | null): number {
  return j && j.total ? Math.min(100, Math.floor((j.done / j.total) * 100)) : 0
}

/** One step of the setup: a big hanging number, then the content. It lives outside FirstRun so its fields keep what you typed. */
function Step({ n, title, text, children, k }: { n: number; title: string; text?: string; children: React.ReactNode; k: number }) {
  return (
    <section style={stagger(k)} className="anim-row relative mb-4 grid grid-cols-[84px_minmax(0,1fr)] gap-x-4 border-t border-line pt-6">
      <span className="serif text-[28px]  leading-[0.8] text-accent">{n}</span>
      <div>
        <h2 className="serif text-[18px] leading-none">{title}</h2>
        {text && <p className="mb-5 mt-2 max-w-[560px] text-muted">{text}</p>}
        {!text && <div className="mb-5" />}
        {children}
      </div>
    </section>
  )
}

/**
 * Setup (section 12.7): a system check, then the folders, the AI mode and the cloud keys. After Start, a
 * "getting ready" view follows the model downloads and the index. Raven can be used at once.
 */
export default function FirstRun({ onDone }: { onDone: () => void }) {
  const places = useApp((s) => s.places)
  const index = useApp((s) => s.index)
  const home = places?.home ?? ''
  const [check, setCheck] = useState<Check | null>(null)
  const [picked, setPicked] = useState<string[] | null>(null)
  const [mode, setMode] = useState<'local' | 'hybrid' | 'ask'>('hybrid')
  const [want, setWant] = useState<Set<string> | null>(null)
  const [busy, setBusy] = useState(false)
  const [starting, setStarting] = useState(false)
  const [phase, setPhase] = useState<'setup' | 'ready'>('setup')
  const [settings, setSettings] = useState<Record<string, unknown>>({})
  // Lite mode (PLAN2 P2-6): the hardware suggests a mode, the user may change it.
  const [hw, setHw] = useState<{ suggested: 'full' | 'lite'; lite_model: string; hardware: { ram: number; vram: number; threads: number; gpu: string | null } } | null>(null)
  const [perf, setPerf] = useState<'full' | 'lite' | null>(null)
  useEffect(() => {
    get<NonNullable<typeof hw>>('/lite').then((l) => {
      setHw(l)
      setPerf((p) => p ?? l.suggested)
    }).catch(() => setPerf('full'))
  }, [])

  const load = (): void => void get<Check>('/setup/check').then(setCheck).catch(() => {})
  const loadSettings = (): void => void get<Record<string, unknown>>('/settings').then(setSettings).catch(() => {})
  useEffect(() => {
    load()
    loadSettings()
    const off = on('models_progress', load)
    const t = setInterval(load, 5000)
    return () => (off(), clearInterval(t))
  }, [])

  const folders = places ? [{ label: 'Home', path: home }, ...places.folders] : []
  useEffect(() => {
    if (picked === null && places) setPicked(places.folders.filter((f) => SUGGESTED.includes(f.label)).map((f) => f.path))
  }, [places, picked])
  useEffect(() => {
    if (check && perf) {
      const m = check.models
      const missing = m.files.filter((f) => !f.present).map((f) => f.name)
      // Lite: the static embedding model only. Full: everything except the static models.
      setWant(
        perf === 'lite'
          ? new Set(missing.filter((n) => n === (hw?.lite_model ?? 'potion-base-8m')))
          : new Set([...missing.filter((n) => !n.startsWith('potion')), ...m.ollama.filter((o) => !o.present).map((o) => `ollama:${o.name}`)])
      )
    }
  }, [check, perf, hw])

  const sel = picked ?? []
  const toggle = (p: string): void =>
    setPicked((xs) => {
      const cur = xs ?? []
      if (cur.includes(p)) return cur.filter((x) => x !== p)
      // Home holds the other folders: pick one or the other.
      return p === home ? [home] : [...cur.filter((x) => x !== home), p]
    })

  const startOllama = async (): Promise<void> => {
    setStarting(true)
    try {
      await window.raven?.startOllama()
    } finally {
      setStarting(false)
      load()
    }
  }

  const start = async (): Promise<void> => {
    setBusy(true)
    try {
      await post('/settings', { key: 'ai_mode', value: mode })
      if (perf) await post('/lite/mode', { mode: perf })
      if (sel.length) await post('/index/roots', { roots: sel })
      const names = [...(want ?? [])].filter((n) => !n.startsWith('ollama:') || check?.ollama.running)
      if (names.length) await post('/models/download', { names })
      await post('/settings', { key: 'first_run_done', value: true })
      void refreshPrivacy()
      setPhase('ready')
    } finally {
      setBusy(false)
    }
  }
  const open = async (): Promise<void> => {
    onDone()
    await navigate(home)
  }

  const m = check?.models
  const allModels = m ? [...m.files.map((f) => ({ ...f, key: f.name })), ...m.ollama.map((o) => ({ ...o, key: `ollama:${o.name}` }))] : []
  const missingTools = check?.tools.filter((t) => !t.ok) ?? []
  const [copied, setCopied] = useState<string | null>(null)
  const copy = (t: string): void => {
    void navigator.clipboard.writeText(t).then(() => {
      setCopied(t)
      setTimeout(() => setCopied(null), 1600)
    })
  }
  const Cmd = ({ text }: { text: string }) => (
    <div className="mt-2 flex items-center gap-2 bg-console-bg px-3 py-2" style={{ borderRadius: 3 }}>
      <code className="selectable mono flex-1 select-all overflow-x-auto whitespace-nowrap text-[12px] text-console-text">{text}</code>
      <button className="flex items-center gap-1 px-2 py-0.5 text-[11px] text-console-text/80 transition-colors hover:text-console-text" onClick={() => copy(text)}>
        {copied === text ? <Check size={12} /> : <Copy size={12} />}
        {copied === text ? 'Copied' : 'Copy'}
      </button>
    </div>
  )
  const modelRow = (r: ModelRow & { key: string }, k: number) => {
    const j = r.job
    const needsOllama = r.key.startsWith('ollama:') && !check?.ollama.running
    const on = r.present || (!!want?.has(r.key) && !needsOllama)
    const running = j?.state === 'running'
    return (
      <label key={r.key} style={stagger(k)} className={`anim-row grid grid-cols-[22px_minmax(0,1fr)_auto] items-center gap-3 border-b border-line/70 py-2.5 transition-colors ${needsOllama || r.present ? 'cursor-default' : 'cursor-pointer hover:bg-hover'}`}>
        <input
          type="checkbox"
          disabled={r.present || needsOllama}
          checked={on}
          onChange={(e) =>
            setWant((w) => {
              const n = new Set(w ?? [])
              if (e.target.checked) n.add(r.key)
              else n.delete(r.key)
              return n
            })
          }
        />
        <span className="min-w-0">
          <span className="font-medium">{r.label}</span>
          <span className="block truncate text-[12.5px] text-muted">{r.adds}</span>
          {running && (
            <span className="mt-1.5 block">
              <Track pct={pct(j)} color="var(--accent)" />
            </span>
          )}
        </span>
        <State
          ok={r.present ? true : j?.state === 'error' ? false : null}
          text={r.present ? 'ready' : running ? `downloading ${pct(j)}%` : j?.state === 'error' ? 'failed' : j?.state === 'queued' ? 'waiting' : needsOllama ? 'needs Ollama' : size(r.mb * 1048576)}
        />
      </label>
    )
  }

  const wrapCls = 'flex h-full justify-center overflow-y-auto bg-bg'

  if (phase === 'ready') {
    const pending = allModels.filter((r) => !r.present && r.job && r.job.state !== 'error')
    const failed = allModels.filter((r) => r.job?.state === 'error')
    const indexDone = !!index && index.roots.length > 0 && index.text_pending === 0 && index.embed_pending === 0 && !(index.visual_pending ?? 0) && index.stage !== 'metadata'
    const row = (label: string, ok: boolean | null, text: string, k: number, bar?: number) => (
      <div key={label} style={stagger(k)} className="anim-row grid grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-3 border-b border-line/70 py-3">
        <span className={`serif text-[14px]  leading-none ${ok ? 'text-ok' : ok === false ? 'text-danger' : 'anim-breathe text-muted'}`}>{ok ? '✓' : ok === false ? '!' : '·'}</span>
        <span className="min-w-0">
          <span className="block truncate">{label}</span>
          {bar !== undefined && bar > 0 && bar < 100 && (
            <span className="mt-1.5 block">
              <Track pct={bar} color="var(--accent)" />
            </span>
          )}
        </span>
        <State ok={ok} text={text} />
      </div>
    )
    return (
      <div className={wrapCls}>
        <div className="w-[740px] max-w-[calc(100vw-32px)] py-4">
          <div className="anim-row mb-2 flex items-center gap-3">
            <Mark size={20} />
            <span className="label">Almost there</span>
          </div>
          <h1 className="serif anim-reveal mb-3 text-[28px] leading-[0.95]">Getting ready.</h1>

          <div className="mb-4 border-t border-line">
            {row(`Raven core ${check?.core.version ?? ''}`, true, 'running', 0)}
            {row('Ollama, for the local AI models', check?.ollama.running ?? null, check?.ollama.running ? 'running' : 'not running · AI features wait', 1)}
            {row(`Index of ${sel.map((p) => tildify(p, home)).join(', ') || 'no folder'}`, indexDone ? true : null, indexDone ? indexText(index) : indexText(index).toLowerCase(), 2)}
            {allModels.map((r, k) =>
              row(r.label, r.present ? true : r.job?.state === 'error' ? false : null, r.present ? 'ready' : r.job?.state === 'running' ? `downloading ${pct(r.job)}%` : r.job?.state === 'error' ? `failed: ${r.job.error ?? ''}`.slice(0, 40) : r.job ? 'waiting' : 'not downloaded', k + 3, pct(r.job))
            )}
          </div>
          {failed.length > 0 && (
            <p className="mb-4 border-l-2 border-danger py-1 pl-3 text-danger">
              {failed.length} download{failed.length === 1 ? '' : 's'} failed. Check the network, then download again in Settings, Models.
            </p>
          )}
          <div className="flex items-center gap-5">
            <button className="btn-primary !px-4 !py-1.5" onClick={() => void open()}>
              Open Raven
            </button>
            <span className="text-[12.5px] text-muted">{pending.length ? `${pending.length} download${pending.length === 1 ? '' : 's'} continue in the background.` : 'The status line at the bottom shows the index.'}</span>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className={wrapCls}>
      <div className="w-[800px] max-w-[calc(100vw-32px)] pb-32 pt-4">
        {/* Welcome */}
        <div className="mb-4">
          <div className="anim-row mb-4 flex items-center gap-3">
            <Mark size={20} />
            <span className="label">Raven</span>
          </div>
          <h1 className="serif anim-reveal text-[28px] leading-[0.92]">
            Welcome. <em className="text-accent">Let us look</em> at your files.
          </h1>

        </div>

        <Step n={1} k={5} title="System check">
          <div className="-mt-3 mb-2 flex justify-end">
            <Ghost tone="plain" onClick={load}>
              Check again
            </Ghost>
          </div>
          {!check && (
            <div className="flex items-center gap-3 text-muted">
              <span className="relative h-px w-24 overflow-hidden bg-line">
                <span className="absolute inset-y-0 w-1/3 bg-accent" style={{ animation: 'rv-scan 1s ease-in-out infinite' }} />
              </span>
              checking this computer…
            </div>
          )}
          {check && (
            <>
              <div className="border-t border-line">
                <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-line/70 py-2.5">
                  <span>
                    Raven core <span className="text-muted">· {check.core.version} · data in {tildify(check.core.data_dir, home)}</span>
                  </span>
                  <State ok text="running" />
                </div>
                <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-line/70 py-2.5">
                  <span>
                    Ollama <span className="text-muted">· local AI</span>
                  </span>
                  <State ok={check.ollama.running} text={check.ollama.running ? 'running' : check.ollama.installed ? 'not running' : 'not installed'} />
                </div>
              </div>
              {!check.ollama.running && (
                <div className="anim-pop mt-3 border-l-2 border-danger py-1 pl-4">
                  {check.ollama.installed ? (
                    <span className="flex items-center gap-4">
                      Ollama is installed but does not run.
                      <Ghost disabled={starting} onClick={() => void startOllama()}>
                        {starting ? 'Starting…' : 'Start Ollama'}
                      </Ghost>
                    </span>
                  ) : (
                    <>
                      <p>Install Ollama, then Check again (3 GB of models).</p>
                      <Cmd text={check.ollama.install} />
                    </>
                  )}
                  <p className="mt-2 text-[12.5px] text-muted">Without it: browse, find, Space.</p>
                </div>
              )}

              <div className="label mb-1 mt-4">Mode</div>
              <div className="flex flex-wrap items-center gap-3">
                <Seg options={[['full', 'Full'], ['lite', 'Lite']]} value={perf ?? 'full'} onChange={(v) => setPerf(v)} />
                {hw && (
                  <span className="mono text-[11px] text-muted">
                    {Math.round(hw.hardware.ram / 1073741824)} GB RAM · {hw.hardware.gpu ? `${Math.round(hw.hardware.vram / 1073741824)} GB GPU` : 'no GPU'} · {hw.hardware.threads} threads · suggested: {hw.suggested}
                  </span>
                )}
              </div>
              <div className="label mb-1 mt-4">Models</div>
              
              <div className="border-t border-line">{allModels.map((r, k) => modelRow(r, k))}</div>

              <div className="label mb-2 mt-7">
                Optional tools · {check.tools.length - missingTools.length} of {check.tools.length} found
              </div>
              {missingTools.length === 0 ? (
                <p className="text-[12.5px] text-muted">Every optional tool is installed.</p>
              ) : (
                <>
                  <div>
                    {missingTools.map((t) => (
                      <div key={t.name} className="grid grid-cols-[110px_minmax(0,1fr)] gap-3 border-b border-line/60 py-1.5 text-[12.5px]">
                        <span className="mono text-accent">{t.name}</span>
                        <span className="text-muted">{t.adds}</span>
                      </div>
                    ))}
                  </div>
                  {check.install_all && (
                    <div className="mt-3">
                      <span className="text-[12.5px] text-muted">Install all of them:</span>
                      <Cmd text={check.install_all} />
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </Step>

        <Step n={2} k={6} title="Folders to index">
          {/* Home is the root of the tree; its folders branch from it. */}
          {folders.length > 0 && (
            <div>
              {(() => {
                const homeF = folders[0]
                const kids = folders.slice(1)
                const homeOn = sel.includes(homeF.path)
                return (
                  <>
                    <button onClick={() => toggle(homeF.path)} className="group flex w-full items-center gap-3 py-1.5 text-left">
                      <input type="checkbox" readOnly checked={homeOn} className="pointer-events-none" />
                      <span className="serif text-[14px] leading-none">Home</span>
                      <span className="mono text-[11.5px] text-muted">everything, slow on a large disk</span>
                    </button>
                    <div className="ml-[6px]">
                      {kids.map((f, k) => {
                        const on = sel.includes(f.path)
                        return (
                          <button key={f.path} onClick={() => toggle(f.path)} style={stagger(k)} className="anim-row group flex h-[36px] w-full items-stretch text-left">
                            <TreeGuides trail={[]} last={k === kids.length - 1} />
                            <span className="flex min-w-0 flex-1 items-center gap-3 pl-2">
                              <input type="checkbox" readOnly checked={on} className="pointer-events-none" />
                              <span className={`transition-all duration-200 group-hover:translate-x-0.5 ${on ? 'text-text' : 'text-muted'}`}>{f.label}</span>
                              <span className="mono text-[11.5px] text-muted/80">{tildify(f.path, home) || f.path}</span>
                            </span>
                          </button>
                        )
                      })}
                    </div>
                  </>
                )
              })()}
              {sel.filter((p) => !folders.some((f) => f.path === p)).map((p) => (
                <button key={p} onClick={() => toggle(p)} className="anim-pop mt-1 flex w-full items-center gap-3 py-1.5 text-left">
                  <input type="checkbox" readOnly checked className="pointer-events-none" />
                  <span className="mono text-[12.5px]">{tildify(p, home)}</span>
                </button>
              ))}
              <div className="mt-3">
                <Ghost onClick={() => void pickFolder('A folder to index').then((ps) => ps.length && setPicked((xs) => [...new Set([...(xs ?? []).filter((x) => x !== home), ...ps])]))}>Add another folder</Ghost>
              </div>
            </div>
          )}
        </Step>

        <Step n={3} k={7} title="AI mode">
          <div className="grid grid-cols-3 gap-4">
            {MODES.map((x) => {
              const on = mode === x.id
              return (
                <button key={x.id} onClick={() => setMode(x.id)} className="group relative pt-3 text-left">
                  <span className="absolute left-0 top-0 h-px w-full bg-line" />
                  <span className={`absolute left-0 top-0 h-[2px] w-full origin-left bg-accent transition-transform duration-500 ease-[cubic-bezier(0.3,0.8,0.2,1)] ${on ? 'scale-x-100' : 'scale-x-0 group-hover:scale-x-[0.18]'}`} />
                  <span className="flex items-center gap-2.5">
                    <span className={`serif text-[16px] leading-none transition-colors ${on ? 'text-text' : 'text-muted group-hover:text-text'}`}>{x.label}</span>
                    {on && <span className="anim-pop h-[7px] w-[7px] rounded-full bg-accent" />}
                  </span>
                  <span className="mt-1.5 block text-[12.5px] leading-snug text-muted">{x.text}</span>
                </button>
              )
            })}
          </div>
        </Step>

        {mode !== 'local' && (
          <Step n={4} k={8} title="Cloud keys">
            <div className="grid grid-cols-[150px_minmax(0,1fr)] items-center gap-y-4">
              <KeyField provider="groq" setting="groq_api_key" label="Groq API key" placeholder="gsk_… (free at console.groq.com)" stored={settings.groq_api_key as string} onSaved={loadSettings} />
              <KeyField provider="anthropic" setting="anthropic_api_key" label="Anthropic API key" placeholder="sk-ant-… (console.anthropic.com)" stored={settings.anthropic_api_key as string} onSaved={loadSettings} />
              <span className="text-muted">Claude Code</span>
              {check?.cloud.claude ? <ClaudeAccount /> : <span className="mono text-[11px] text-muted">not installed (optional)</span>}
            </div>
          </Step>
        )}

        {/* Start: always in view */}
        <div className="fixed inset-x-0 bottom-0 z-10 flex justify-center border-t border-line bg-bg">
          <div className="flex w-[800px] max-w-[calc(100vw-32px)] items-center gap-5 py-4">
            <button className="btn-primary !px-4 !py-1.5 disabled:opacity-40" disabled={busy || !sel.length} onClick={() => void start()}>
              {busy ? 'Starting…' : 'Start'}
            </button>
            <span className="text-[12.5px] text-muted">{sel.length ? `${sel.length} folder${sel.length === 1 ? '' : 's'} chosen. Indexing runs in the background, so you can browse at once.` : 'Pick at least one folder.'}</span>
          </div>
        </div>
      </div>
    </div>
  )
}
