import * as Dialog from '@radix-ui/react-dialog'
import { useEffect, useState } from 'react'
import { get, on, post } from '../lib/api'
import { size } from '../lib/format'
import type { CloudReview as Review } from '../lib/types'
import Payload from './Payload'

/** Review before send (PLAN4 7.7). Every cloud payload waits here: what Raven hid, the words that may still be
 * private, and the exact text. Send sends this text and nothing else. The values show on this computer only. */
export default function CloudReview() {
  const [queue, setQueue] = useState<Review[]>([])
  const [word, setWord] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [now, setNow] = useState(Date.now())
  const r = queue[0]

  useEffect(() => {
    const load = (): void => void get<{ reviews: Review[] }>('/privacy/reviews').then((x) => setQueue(x.reviews)).catch(() => {})
    load()
    const offs = [
      on('ws_open', load),
      on('cloud_review', (m: { review: Review }) =>
        setQueue((q) => (q.some((x) => x.id === m.review.id) ? q.map((x) => (x.id === m.review.id ? m.review : x)) : [...q, m.review]))
      ),
      on('cloud_review_closed', (m: { id: string }) => setQueue((q) => q.filter((x) => x.id !== m.id)))
    ]
    return () => offs.forEach((off) => off())
  }, [])

  useEffect(() => {
    setErr('')
    setWord('')
  }, [r?.id])

  useEffect(() => {
    if (!r) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [r])

  if (!r) return null

  const change = async (body: { hide?: string[]; show?: string[] }): Promise<void> => {
    setBusy(true)
    setErr('')
    try {
      const next = await post<Review>('/privacy/review/update', { id: r.id, ...body })
      setQueue((q) => q.map((x) => (x.id === next.id ? next : x)))
      setWord('')
    } catch (e) {
      setErr((e as Error).message)
    }
    setBusy(false)
  }
  const alwaysHide = async (w: string): Promise<void> => {
    try {
      const cur = await get<{ words: string[] }>('/privacy/words')
      await post('/privacy/words', { words: [...cur.words, w] })
    } catch {
      /* the hide below still works for this payload */
    }
    await change({ hide: [w] })
  }
  const decide = (send: boolean): void => {
    void post('/privacy/review/decide', { id: r.id, send }).catch(() => {})
    setQueue((q) => q.filter((x) => x.id !== r.id))
  }
  const left = Math.max(0, Math.round(r.created + r.wait_s - now / 1000))
  const hidden = r.items.length
  const llm = { off: 'The local AI does not run, so only the quick check ran.', running: 'The local AI is reading the payload…', done: '', error: 'The local AI check failed. The quick check ran.' }[r.llm]

  return (
    <Dialog.Root open onOpenChange={(o) => !o && decide(false)}>
      <Dialog.Portal>
        <Dialog.Overlay className="anim-fade fixed inset-0 bg-black/45 backdrop-blur-md" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 flex max-h-[88vh] w-[min(820px,94vw)] -translate-x-1/2 -translate-y-1/2 flex-col float-panel anim-pop p-6"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.target as HTMLElement).tagName !== 'INPUT' && !busy) {
              e.preventDefault()
              decide(true)
            }
          }}
        >
          <Dialog.Title className="serif mb-1 text-[18px] leading-none">Review before sending</Dialog.Title>
          <Dialog.Description className="mb-4 text-[12.5px] text-muted">
            {r.feature} wants to send this to <b className="text-text">{r.providers.join(', then ')}</b> · {size(r.bytes)} · {hidden} item{hidden === 1 ? '' : 's'} hidden.
            {queue.length > 1 && ` ${queue.length - 1} more wait${queue.length === 2 ? 's' : ''}.`} Nothing is sent until you press Send ({Math.floor(left / 60)}:{String(left % 60).padStart(2, '0')} left).
          </Dialog.Description>

          <div className="min-h-0 flex-1 overflow-auto pr-1">
            <Section title={`Suspicious words (${r.suspects.length})`} note="Still visible. A local check thinks they may be private. Hide what you do not want to share.">
              {llm && <div className="py-1 text-[12px] text-muted">{llm}</div>}
              {r.suspects.length === 0 && r.llm !== 'running' && <div className="py-1 text-[12px] text-muted">None found.</div>}
              {r.suspects.map((s) => (
                <div key={s.text} className="flex items-center gap-3 py-1.5 text-[12.5px]">
                  <span className="mono min-w-0 flex-1 truncate">{s.text}</span>
                  <span className="w-56 shrink-0 truncate text-[11.5px] text-muted">{s.why}</span>
                  <span className="mono w-16 shrink-0 text-[10.5px] text-faint">{s.source}</span>
                  <button className="link" disabled={busy} onClick={() => void change({ hide: [s.text] })}>Hide</button>
                  <button className="link text-muted" disabled={busy} title="Hide it now and in every later cloud request" onClick={() => void alwaysHide(s.text)}>
                    Always hide
                  </button>
                </div>
              ))}
              <form
                className="mt-1 flex items-center gap-3"
                onSubmit={(e) => {
                  e.preventDefault()
                  if (word.trim()) void change({ hide: [word.trim()] })
                }}
              >
                <input className="field flex-1" placeholder="Hide another word or name" value={word} onChange={(e) => setWord(e.target.value)} />
                <button className="link" disabled={busy || !word.trim()}>Hide</button>
              </form>
            </Section>

            <Section title={`Hidden (${hidden})`} note="The cloud sees only the placeholder. The value stays on this computer and comes back into the answer.">
              {hidden === 0 && <div className="py-1 text-[12px] text-muted">Nothing private was found.</div>}
              {r.items.map((i) => (
                <div key={i.placeholder} className="flex items-center gap-3 py-1 text-[12.5px]">
                  <mark className="mono w-36 shrink-0 rounded-sm bg-accent-soft px-1 text-[11.5px] text-accent">{i.placeholder}</mark>
                  <span className="mono min-w-0 flex-1 truncate">{i.value}</span>
                  {i.can_show ? (
                    <button className="link text-muted" disabled={busy} title="Send the real value: use this only when Raven hid a word that is not private" onClick={() => void change({ show: [i.placeholder] })}>
                      Show
                    </button>
                  ) : (
                    <span className="text-[11px] text-faint" title="Raven never sends this type">always hidden</span>
                  )}
                </div>
              ))}
            </Section>

            <Section title="The exact text" note="This is all that leaves this computer.">
              <details className="mb-2 text-[12px] text-muted">
                <summary className="cursor-pointer">Instructions for the cloud AI</summary>
                <pre className="selectable mt-1 whitespace-pre-wrap text-[11.5px]">{r.system}</pre>
              </details>
              <Payload text={r.payload} className="max-h-[34vh] overflow-auto rounded-[3px] bg-console-bg p-4 font-mono text-[11.5px] text-console-text" />
            </Section>
          </div>

          {err && <div className="mt-3 text-[12px] text-danger">{err}</div>}
          <div className="mt-4 flex items-center justify-end gap-5">
            <button className="link text-muted hover:text-text" onClick={() => decide(false)}>
              Don't send: the local model answers (Esc)
            </button>
            <button autoFocus className="btn-primary" disabled={busy} onClick={() => decide(true)}>
              Send {size(r.bytes)} (Enter)
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function Section({ title, note, children }: { title: string; note: string; children: React.ReactNode }) {
  return (
    <div className="mb-4 border-t border-line pt-3">
      <div className="mb-1 flex items-baseline gap-3">
        <span className="text-[13px] font-medium">{title}</span>
        <span className="text-[11.5px] text-muted">{note}</span>
      </div>
      {children}
    </div>
  )
}
