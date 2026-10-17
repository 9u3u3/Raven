import { useEffect, useState } from 'react'
import { EmptyState } from '../components/EmptyState'
import { Chip, Ghost, SectionTitle, stagger } from '../components/ui'
import { tildify } from '../lib/format'
import { setScreen, summarize, useApp } from '../store'

// A summary is a page too: the folder as the headline, a short summary, key points, and where it ran.
export default function AnswerScreen() {
  const a = useApp((s) => s.answer)
  const home = useApp((s) => s.places?.home ?? '')
  const mode = useApp((s) => s.privacy?.ai_mode ?? 'hybrid')
  const [now, setNow] = useState(Date.now())

  useEffect(() => {
    if (a?.status !== 'running') return
    const t = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(t)
  }, [a?.status])

  if (!a) {
    return (
      <EmptyState art="folder" title="Summarize a folder">
        <Ghost onClick={() => setScreen('files')}>Go to Files</Ghost>
      </EmptyState>
    )
  }
  const r = a.result
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <article className="px-4 py-4">
        <h1 className="serif anim-reveal mb-3 text-[22px] leading-[1.02]">{a.question}</h1>
        <div className="mono mb-4 text-[12px] text-muted">{tildify(a.path, home)}</div>

        {a.status === 'running' && (
          <div className="anim-fade flex items-center gap-4">
            <span className="relative h-px w-28 overflow-hidden bg-line">
              <span className="absolute inset-y-0 w-1/3 bg-accent" style={{ animation: 'rv-scan 1s ease-in-out infinite' }} />
            </span>
            <span className="serif text-[14px] ">{a.think && mode !== 'local' ? 'Thinking harder: redacting, then asking the cloud' : 'Thinking locally'}</span>
            <span className="mono text-[11px] text-muted">{Math.round((now - a.started) / 1000)} s</span>
          </div>
        )}

        {a.status === 'error' && <div className="anim-pop border-l-2 border-danger py-1 pl-3 text-danger">{a.error}</div>}

        {a.status === 'done' && r && (
          <>
            <p className="selectable anim-fade mb-4 max-w-[680px] text-[16.5px] leading-[1.85]">{r.summary}</p>
            <SectionTitle>Key points</SectionTitle>
            <ul className="selectable mb-4">
              {r.points.map((p, i) => (
                <li key={i} style={stagger(i + 1)} className="anim-row grid grid-cols-[40px_1fr] items-baseline border-b border-line py-3.5">
                  <span className="serif text-[16px]  leading-none text-accent">{i + 1}</span>
                  <span className="text-[15px] leading-snug">{p}</span>
                </li>
              ))}
            </ul>
            <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-1 text-[12px] text-muted">
              <Chip tone={r.tier === 'T3' ? 'warn' : 'good'}>{r.tier === 'T3' ? 'cloud' : 'local'}</Chip>
              <span>{r.model}</span>
              <span>{(r.ms / 1000).toFixed(1)} s</span>
              <span>{r.files} files</span>
              {r.tier === 'T3' && <span className="text-ok">redacted {r.redacted} items</span>}
            </div>
            {r.fallback && <p className="mb-4 text-[12px] text-muted">Local result: {r.fallback}.</p>}
            <div className="flex gap-4">
              {r.tier === 'T2' && <Ghost onClick={() => void summarize(a.path, true)}>Think harder</Ghost>}
              {r.tier === 'T3' && (
                <Ghost tone="plain" onClick={() => setScreen('privacy')}>
                  See what was sent
                </Ghost>
              )}
            </div>
          </>
        )}
      </article>
    </div>
  )
}
