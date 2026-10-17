import { useEffect, useState } from 'react'
import { get, on, post } from '../lib/api'
import { size } from '../lib/format'
import type { OllamaStatus } from './FeaturePicker'
import { CopyCmd } from './FeaturePicker'
import { Ghost, Seg, Track } from './ui'

/**
 * Ollama runs the local models (PLAN4 section 5.3.1). When it is missing, Raven offers two ways:
 * "Install for me" downloads the official build into the Raven data folder (no administrator rights), and
 * "Show me the steps" gives the official install steps of this system. After the install, Raven starts it.
 */
export default function OllamaSetup({ status, onChange }: { status: OllamaStatus; onChange: () => void }) {
  const [way, setWay] = useState<'auto' | 'steps'>('auto')
  const [full, setFull] = useState<OllamaStatus | null>(null)
  const [starting, setStarting] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  // The parent's status says installed and running. The full status adds the download size and the job.
  const st: OllamaStatus = { ...status, download: full?.download ?? status.download, job: full?.job ?? status.job }
  const job = st.job

  // The full status asks GitHub for the download size once.
  useEffect(() => {
    if (!status.installed) void get<OllamaStatus>('/ollama').then(setFull).catch(() => {})
  }, [status.installed])
  useEffect(
    () =>
      on('ollama_install', (e) => {
        const j = (e as { job: OllamaStatus['job'] }).job
        setFull((s) => ({ ...(s ?? status), job: j }))
        if (j?.state === 'done') void start()
      }),
    []
  )

  const start = async (): Promise<void> => {
    setStarting(true)
    setErr(null)
    try {
      const ok = await window.raven?.startOllama()
      if (ok === false) setErr('Ollama does not start. Check again in a moment, or use the steps.')
    } finally {
      setStarting(false)
      setFull(await get<OllamaStatus>('/ollama?quick=true').catch(() => null))
      onChange()
    }
  }

  if (st.running) return null
  const running = job?.state === 'running'
  const pct = job && job.total ? Math.floor((job.done / job.total) * 100) : 0

  if (st.installed) {
    return (
      <div className="anim-pop mb-4 border-l-2 border-warn py-1 pl-4">
        <div className="flex flex-wrap items-center gap-4">
          <span>Ollama is installed, but it does not run.</span>
          <Ghost disabled={starting} onClick={() => void start()}>
            {starting ? 'Starting…' : 'Start Ollama'}
          </Ghost>
        </div>
        {err && <p className="mt-1 text-[12.5px] text-danger">{err}</p>}
      </div>
    )
  }

  return (
    <div className="anim-pop mb-4 border-l-2 border-warn py-1 pl-4">
      <p>The local models need Ollama. It is free, and it runs on this computer.</p>
      <div className="mb-2 mt-2">
        <Seg options={[['auto', 'Install for me'], ['steps', 'Show me the steps']]} value={way} onChange={setWay} size="sm" />
      </div>
      {way === 'auto' ? (
        <>
          <p className="text-[12.5px] text-muted">
            Raven downloads the official Ollama {st.download ? `${st.download.version} (${size(st.download.bytes)})` : ''} from GitHub, checks it, and puts it in the Raven
            data folder. It needs no administrator rights.
          </p>
          {running ? (
            <div className="mt-2">
              <div className="mb-1 flex items-center gap-3 text-[12.5px]">
                <span className="text-muted">{job?.step}</span>
                <span className="mono">{pct}%</span>
                <Ghost tone="plain" onClick={() => void post('/ollama/cancel')}>
                  Cancel
                </Ghost>
              </div>
              <Track pct={pct} color="var(--accent)" />
            </div>
          ) : (
            <div className="mt-2 flex items-center gap-4">
              <button className="btn-primary !px-3 !py-1" disabled={!st.can_install || starting} onClick={() => void post<OllamaStatus>('/ollama/install').then(setFull)}>
                {starting ? 'Starting Ollama…' : job?.state === 'error' ? 'Try again' : 'Install Ollama'}
              </button>
            </div>
          )}
          {job?.state === 'error' && <p className="mt-2 text-[12.5px] text-danger">{job.error}</p>}
        </>
      ) : (
        <>
          <ol className="ml-5 list-decimal text-[12.5px] leading-relaxed text-muted">
            {st.steps.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ol>
          <CopyCmd text={st.command} />
          <div className="mt-2">
            <Ghost onClick={onChange}>Check again</Ghost>
          </div>
        </>
      )}
      {err && <p className="mt-1 text-[12.5px] text-danger">{err}</p>}
    </div>
  )
}
