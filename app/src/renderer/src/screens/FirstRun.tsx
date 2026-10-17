import { useEffect, useState } from 'react'
import AiProviders from '../components/AiProviders'
import FeaturePicker, { DeviceLine, choiceSize, defaultChoice, useFeatures, type Choice } from '../components/FeaturePicker'
import OllamaSetup from '../components/OllamaSetup'
import { indexText } from '../components/StatusLine'
import { Mark } from '../components/TopBar'
import { Chip, Ghost, Track, TreeGuides, stagger } from '../components/ui'
import { get, post } from '../lib/api'
import { size, tildify } from '../lib/format'
import { navigate, refreshPrivacy, say, useApp } from '../store'
import { pickFolder } from '../components/FolderPicker'

type Check = {
  core: { version: string; data_dir: string; models_dir: string; platform: string; distro: string; frozen: boolean; dev?: boolean }
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

function pct(j?: { done?: number; total?: number } | null): number {
  return j && j.total ? Math.min(100, Math.floor(((j.done ?? 0) / j.total) * 100)) : 0
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
  const [busy, setBusy] = useState(false)
  const [phase, setPhase] = useState<'setup' | 'ready'>('setup')
  // The device check and the features (PLAN4 section 5.3): Raven ticks what runs well, the user may change it.
  const [fst, loadFeatures, setFst] = useFeatures()
  const [choice, setChoice] = useState<Choice | null>(null)
  const [testing, setTesting] = useState(false)
  useEffect(() => {
    if (fst && !choice) setChoice(defaultChoice(fst))
  }, [fst, choice])

  const load = (): void => void get<Check>('/setup/check').then(setCheck).catch(() => {})
  useEffect(() => {
    load()
  }, [])

  const folders = places ? [{ label: 'Home', path: home }, ...places.folders] : []
  useEffect(() => {
    if (picked === null && places) setPicked(places.folders.filter((f) => SUGGESTED.includes(f.label)).map((f) => f.path))
  }, [places, picked])

  const sel = picked ?? []
  const toggle = (p: string): void =>
    setPicked((xs) => {
      const cur = xs ?? []
      if (cur.includes(p)) return cur.filter((x) => x !== p)
      // Home holds the other folders: pick one or the other.
      return p === home ? [home] : [...cur.filter((x) => x !== home), p]
    })

  const bench = async (): Promise<void> => {
    setTesting(true)
    try {
      await post('/device/bench')
      await loadFeatures(true)
      setChoice(null) // the speed test can change the advice
    } finally {
      setTesting(false)
    }
  }

  const start = async (): Promise<void> => {
    setBusy(true)
    try {
      await post('/settings', { key: 'ai_mode', value: mode })
      if (sel.length) await post('/index/roots', { roots: sel })
      if (choice) setFst(await post('/features/apply', choice))
      await post('/settings', { key: 'first_run_done', value: true })
      void refreshPrivacy()
      setPhase('ready')
    } catch (e) {
      say((e as Error).message, 'error')
    } finally {
      setBusy(false)
    }
  }
  const open = async (): Promise<void> => {
    onDone()
    await navigate(home)
  }

  const sizes = fst && choice ? choiceSize(fst, choice) : null
  const needOllama = !!fst && !!sizes?.ollama && !fst.ollama.running
  const modelRows = (fst?.features ?? []).filter((f) => f.kind === 'model' && (f.always || f.on))

  const wrapCls = 'flex h-full justify-center overflow-y-auto bg-bg'

  if (phase === 'ready') {
    const pending = modelRows.filter((f) => f.state === 'downloading' || f.state === 'missing' || f.state === 'needs_ollama')
    const failed = modelRows.filter((f) => f.state === 'error')
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
            {row('Ollama, for the local AI models', fst?.ollama.running ?? null, fst?.ollama.running ? 'running' : fst?.ollama.job?.state === 'running' ? 'installing' : 'not running · AI features wait', 1)}
            {row(`Index of ${sel.map((p) => tildify(p, home)).join(', ') || 'no folder'}`, indexDone ? true : null, indexDone ? indexText(index) : indexText(index).toLowerCase(), 2)}
            {modelRows.map((f, k) => {
              const ok = f.state === 'ready' ? true : f.state === 'error' ? false : null
              const text = f.state === 'ready' ? 'ready' : f.state === 'downloading' ? `downloading ${pct(f.job)}%` : f.state === 'error' ? `failed: ${f.job?.error ?? ''}`.slice(0, 40) : f.state === 'needs_ollama' ? 'waits for Ollama' : 'waiting'
              return row(f.label, ok, text, k + 3, f.state === 'downloading' ? pct(f.job) : undefined)
            })}
          </div>
          {fst && !fst.ollama.running && modelRows.some((f) => f.state === 'needs_ollama') && <OllamaSetup status={fst.ollama} onChange={() => void loadFeatures()} />}
          {failed.length > 0 && (
            <p className="mb-4 border-l-2 border-danger py-1 pl-3 text-danger">
              {failed.length} download{failed.length === 1 ? '' : 's'} failed. Check the network, then retry in Settings, Features and models.
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

        <Step n={1} k={5} title="This computer" text="Raven checked what this computer can run, and ticked what runs well. You can change it now or later in Settings.">
          <div className="-mt-3 mb-2 flex justify-end gap-5">
            <Ghost tone="plain" disabled={testing} onClick={() => void bench()} title="A CPU test of about 1 s. It makes the advice more exact.">
              {testing ? 'Testing…' : fst?.device.bench ? `Speed ${fst.device.bench} GFLOPS · test again` : 'Test the speed'}
            </Ghost>
            <Ghost tone="plain" onClick={() => void loadFeatures(true)}>
              Check again
            </Ghost>
          </div>
          {!fst && (
            <div className="flex items-center gap-3 text-muted">
              <span className="relative h-px w-24 overflow-hidden bg-line">
                <span className="absolute inset-y-0 w-1/3 bg-accent" style={{ animation: 'rv-scan 1s ease-in-out infinite' }} />
              </span>
              checking this computer…
            </div>
          )}
          {fst && choice && (
            <>
              <div className="mb-3">
                <DeviceLine d={fst.device} />
              </div>
              {needOllama && <OllamaSetup status={fst.ollama} onChange={() => void loadFeatures()} />}
              <FeaturePicker st={fst} mode="pick" choice={choice} onChoice={setChoice} />
              <div className="mt-3 flex flex-wrap items-center gap-x-4 text-[12.5px] text-muted">
                <span>
                  <span className="mono text-text">{size(sizes?.download ?? 0)}</span> to download
                </span>
                <span>{size(fst.device.disk.free)} free on this disk</span>
                {needOllama && <span>The Ollama models download after Ollama runs.</span>}
              </div>
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
          <Step n={4} k={8} title="AI providers">
            {/* PLAN4 4: the same list as Settings, AI providers. Groq, Gemini and OpenRouter have free plans. */}
            <AiProviders />
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
