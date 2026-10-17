import { Fragment, useEffect, useRef, useState } from 'react'
import { EmptyState } from '../components/EmptyState'
import { FileIcon } from '../components/FileIcon'
import { Chip, Ghost, SectionTitle, stagger } from '../components/ui'
import { clock, size, tildify } from '../lib/format'
import type { AskSection, AskSource } from '../lib/types'
import { ask, providerName, reveal, setScreen, useApp } from '../store'

// An answer is a page, not a chat: the question is the headline, the answer is text with
// numbered citations, and the sources are footnotes that open the file at the right place.

function openSource(s: AskSource | undefined): void {
  if (s) reveal(s.path, true, s.page, s.at)
}

function Cited({ text, sources }: { text: string; sources: AskSource[] }) {
  const parts = text.split(/(\[\d+\])/g)
  return (
    <>
      {parts.map((p, i) => {
        const m = /^\[(\d+)\]$/.exec(p)
        if (!m) return <Fragment key={i}>{p}</Fragment>
        const n = Number(m[1])
        const src = sources.find((s) => s.n === n)
        if (!src) return <Fragment key={i}>{p}</Fragment>
        return (
          <sup key={i} className="ml-[1px]">
            <button
              title={`${src.name}${src.page ? `, page ${src.page}` : ''}`}
              onClick={() => openSource(src)}
              className="mono px-[3px] text-[11px] font-medium text-accent underline decoration-accent/40 underline-offset-2 transition-colors hover:bg-accent hover:text-on-accent"
            >
              {n}
            </button>
          </sup>
        )
      })}
    </>
  )
}

/** Where the answer ran, as one quiet line. */
function TierLine({ a }: { a: AskSection }) {
  if (a.status !== 'done' || a.notFound) return null
  const secs = a.ms != null ? `${(a.ms / 1000).toFixed(1)} s` : ''
  const first = a.firstMs != null ? `first word ${(a.firstMs / 1000).toFixed(1)} s` : ''
  const cloud = a.tier === 'T3'
  return (
    <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-1 text-[12px] text-muted">
      <Chip tone={cloud ? 'warn' : 'good'}>{cloud ? 'cloud' : 'local'}</Chip>
      {/* A cloud model label is "Provider · model" (PLAN4 4). */}
      {(cloud ? [a.model ?? providerName(a.provider)] : [a.model]).filter(Boolean).map((x) => (
        <span key={x}>{x}</span>
      ))}
      {cloud && <span className="text-ok">redacted {a.redacted ?? 0} items</span>}
      {first && <span>{first}</span>}
      {secs && <span>{secs}</span>}
    </div>
  )
}

/** What Raven is doing, so a wait is never a blank page. */
function Progress({ a, now }: { a: AskSection; now: number }) {
  const phase = a.status === 'retrieving' ? 0 : a.text ? 2 : 1
  const steps = [
    'Reading your files',
    a.tier === 'T3' ? `Asking ${providerName(a.provider) || 'the cloud'} with ${a.sources.length} redacted sources` : a.sources.length ? `Reading ${a.sources.length} passages` : 'Choosing the best passages',
    'Writing the answer'
  ]
  return (
    <ol className="anim-fade mb-4 border-l border-line pl-5">
      {steps.map((label, i) => (
        <li key={label} className={`relative py-1.5 transition-opacity duration-300 ${i > phase ? 'opacity-35' : ''}`}>
          <span className={`absolute -left-[26px] top-1/2 h-[9px] w-[9px] -translate-y-1/2 rounded-full border transition-all duration-500 ${i < phase ? 'border-accent bg-accent' : i === phase ? 'anim-breathe border-accent bg-bg' : 'border-muted bg-bg'}`} />
          <span className={i === phase ? 'serif text-[14px] ' : 'text-muted'}>{label}</span>
          {i === phase && <span className="mono ml-3 text-[11px] text-muted">{((now - a.started) / 1000).toFixed(1)} s</span>}
        </li>
      ))}
    </ol>
  )
}

function Footnote({ s, cited, dim, k }: { s: AskSource; cited: boolean; dim: boolean; k: number }) {
  const home = useApp((st) => st.places?.home ?? '')
  const ext = s.name.includes('.') ? s.name.slice(s.name.lastIndexOf('.') + 1).toLowerCase() : ''
  return (
    <li
      style={stagger(k)}
      onClick={() => openSource(s)}
      onDoubleClick={() => void window.raven?.openPath(s.path)}
      title="Click to open at this place. Double-click to open in the default app."
      className={`anim-row group relative grid cursor-default grid-cols-[40px_minmax(0,1fr)_24px] items-baseline gap-3 border-b border-line py-4 transition-opacity ${dim ? 'opacity-50 hover:opacity-100' : ''}`}
    >
      <span aria-hidden className="absolute -bottom-px left-0 h-px w-full origin-left scale-x-0 bg-accent transition-transform duration-500 group-hover:scale-x-100" />
      <span className={`serif text-[16px]  leading-none ${cited ? 'text-accent' : 'text-muted'}`}>{s.n}</span>
      <div className="min-w-0">
        <div className="flex items-center gap-2.5">
          <FileIcon ext={ext} isDir={false} size={14} />
          <span className="truncate font-medium transition-transform duration-200 group-hover:translate-x-0.5">{s.name}</span>
          <span className="mono shrink-0 text-[11px] text-muted">
            {s.page ? `page ${s.page}` : ''}
            {s.at != null ? `said at ${clock(s.at)}` : ''}
            {s.page || s.at != null ? ' · ' : ''}
            {s.why}
          </span>
        </div>
        <p className="mt-1 line-clamp-3 text-[12px] leading-snug text-muted">“{s.snippet}”</p>
        <div className="mono mt-1.5 truncate text-[10.5px] text-muted/80">{tildify(s.path.slice(0, s.path.lastIndexOf('/')), home)}</div>
      </div>
      <span className="justify-self-end text-muted opacity-0 transition-all duration-300 group-hover:translate-x-0.5 group-hover:text-accent group-hover:opacity-100">↗</span>
    </li>
  )
}

function Section({ a }: { a: AskSection }) {
  const mode = useApp((s) => s.privacy?.ai_mode ?? 'hybrid')
  const [now, setNow] = useState(Date.now())
  const busy = a.status === 'retrieving' || a.status === 'streaming'

  useEffect(() => {
    if (!busy) return
    const t = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(t)
  }, [busy])

  const cited = new Set([...a.text.matchAll(/\[(\d+)\]/g)].map((m) => Number(m[1])))
  return (
    // Two columns (PLAN2 P2-10.3): the answer on the left, the sources on the right. One column when narrow.
    <section className="mb-6 grid gap-x-6 border-b border-line pb-4 min-[1100px]:grid-cols-[minmax(0,1fr)_420px]">
      <div className="min-w-0">
      <h1 className="serif anim-reveal mb-3 text-[16px] leading-tight">{a.question}</h1>

      {busy && !a.text && <Progress a={a} now={now} />}

      {a.text && (
        <p className={`selectable anim-fade mb-4 whitespace-pre-wrap text-[13.5px] leading-[1.7] ${a.notFound ? 'text-muted' : ''}`}>
          <Cited text={a.text} sources={a.sources} />
          {a.status === 'streaming' && <span className="ml-0.5 inline-block h-[1.1em] w-[2px] translate-y-[3px] animate-pulse bg-accent" />}
        </p>
      )}

      {a.status === 'error' && <div className="anim-pop mb-4 border-l-2 border-danger py-1 pl-3 text-danger">{a.error}</div>}

      {a.status === 'review' && (
        <p className="anim-pop mb-6 border-l-2 border-accent pl-5 text-muted">
          Waiting for your review. Raven hid {a.redacted ?? 0} item{a.redacted === 1 ? '' : 's'}. Check the payload in the review window, then send it or let the local model answer.
        </p>
      )}

      {a.status === 'done' && a.notFound && <p className="mb-3 text-muted">Not in your files.</p>}

      <TierLine a={a} />
      {a.status === 'done' && a.fallback && <p className="mb-3 text-[12px] text-muted">Local result: {a.fallback}.</p>}
      {a.status === 'done' && !a.notFound && (
        <div className="mb-3 flex gap-6">
          {a.tier !== 'T3' && mode !== 'local' && (
            <Ghost onClick={() => void ask(a.question, { think: true, replace: a.id, followUp: true })} title="Redact, then ask a stronger cloud model">
              Think harder
            </Ghost>
          )}
          {a.tier === 'T3' && (
            <Ghost tone="plain" onClick={() => setScreen('privacy')}>
              See what was sent
            </Ghost>
          )}
        </div>
      )}

      </div>
      {a.sources.length > 0 && (
        <div className="min-w-0 min-[1100px]:sticky min-[1100px]:top-2 min-[1100px]:self-start">
          <SectionTitle>Sources · {a.sources.length}</SectionTitle>
          <ol>
            {a.sources.map((s, k) => (
              <Footnote key={s.n} s={s} k={k} cited={cited.has(s.n)} dim={a.status === 'done' && !cited.has(s.n)} />
            ))}
          </ol>
        </div>
      )}
    </section>
  )
}

const EXAMPLES = ['What is the late fee in my lease?', 'When does my passport expire?', 'Summarize my latest meeting notes']

export default function AskScreen() {
  const asks = useApp((s) => s.asks)
  const [follow, setFollow] = useState('')
  const end = useRef<HTMLDivElement>(null)
  const lastLen = asks.length

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'nearest' })
  }, [lastLen])

  if (!asks.length) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="px-4 pt-4">
          <h1 className="serif anim-reveal text-[16px]">Ask about your files</h1>
          <ol className="mt-3">
            {EXAMPLES.map((q, k) => (
              <li key={q} style={stagger(k + 3)} className="anim-row border-t border-line last:border-b">
                <button onClick={() => void ask(q)} className="group flex w-full items-baseline gap-3 py-2 text-left">
                  <span className="text-muted transition-colors group-hover:text-accent">{k + 1}</span>
                  <span className="leading-tight transition-transform duration-300 group-hover:translate-x-1">{q}</span>
                  <span className="ml-auto text-muted transition-all duration-300 group-hover:translate-x-1 group-hover:text-accent">→</span>
                </button>
              </li>
            ))}
          </ol>
        </div>
      </div>
    )
  }
  const busy = asks.some((a) => a.status === 'retrieving' || a.status === 'streaming')
  const send = (): void => {
    if (follow.trim() && !busy) {
      void ask(follow, { followUp: true })
      setFollow('')
    }
  }
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <article className="px-4 pt-3">
        {asks.map((a) => (
          <Section key={a.id} a={a} />
        ))}
        <div ref={end} className="sticky bottom-0 -mx-4 flex items-baseline gap-3 border-t border-line bg-bg px-4 py-2">
          <span className="text-accent">Then</span>
          <input
            value={follow}
            disabled={busy}
            spellCheck={false}
            placeholder={busy ? 'Raven is answering…' : 'Ask a follow-up question'}
            onChange={(e) => setFollow(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && send()}
            className="field flex-1"
          />
          <button onClick={send} disabled={busy || !follow.trim()} className="link text-accent disabled:opacity-30">
            Ask →
          </button>
        </div>
      </article>
    </div>
  )
}
