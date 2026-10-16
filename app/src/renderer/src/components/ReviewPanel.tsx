import { ChevronDown, ChevronRight } from 'lucide-react'
import { useEffect, useState } from 'react'
import { size, tildify } from '../lib/format'
import { closeReview, useApp, type ReviewStep } from '../store'

// The shared review view (section 12.5). It opens on the right and pushes the content left.
export default function ReviewPanel() {
  const review = useApp((s) => s.review)
  const home = useApp((s) => s.places?.home ?? '')
  const [steps, setSteps] = useState<ReviewStep[]>([])
  const [open, setOpen] = useState<number | null>(0)
  const [armed, setArmed] = useState(false)
  const [running, setRunning] = useState(false)

  useEffect(() => {
    setSteps(review ? review.steps.map((s) => ({ ...s, files: s.files.map((f) => ({ ...f })) })) : [])
    setOpen(review && review.steps.length === 1 ? 0 : null)
    setArmed(false)
    setRunning(false)
  }, [review])

  const danger = steps.some((s) => s.lossy || s.permanent)
  // Trash and overwrite steps need a second Enter too, but they are reversible.
  const needsConfirm = danger || steps.some((s) => s.confirm && s.files.some((f) => f.checked))
  const checked = steps.flatMap((s) => s.files.filter((f) => f.checked))
  const bytes = checked.reduce((a, f) => a + f.size, 0)
  // The drive line in Space shows the free space after the plan.
  useEffect(() => {
    useApp.setState({ planFree: review?.frees ? bytes : 0, planPath: checked[0]?.path ?? '' })
  }, [bytes, review]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (): Promise<void> => {
    if (!review || !checked.length || running) return
    // Lossy or permanent steps need a second Enter.
    if (needsConfirm && !armed) {
      setArmed(true)
      return
    }
    setRunning(true)
    try {
      await review.run(steps)
    } finally {
      closeReview(true)
    }
  }

  useEffect(() => {
    if (!review) return
    const onKey = (e: KeyboardEvent): void => {
      const t = e.target as HTMLElement
      if (t.tagName === 'INPUT' && (t as HTMLInputElement).type !== 'checkbox') return
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopImmediatePropagation()
        closeReview()
      } else if (e.key === 'Enter') {
        e.preventDefault()
        e.stopImmediatePropagation()
        void run()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  })

  if (!review) return null

  const toggle = (si: number, fi: number | null, value: boolean): void => {
    setSteps((prev) =>
      prev.map((s, i) => (i !== si ? s : { ...s, files: s.files.map((f, j) => (fi === null || j === fi ? { ...f, checked: value } : f)) }))
    )
  }

  return (
    <aside data-tour="review" className="anim-slide flex w-[520px] shrink-0 flex-col border-l border-line">
      <div className="flex items-center justify-between border-b border-line px-5 py-3">
        <span>
          <span className="font-medium">Review</span>
          <span className="mono text-[12px] text-muted">
            {' '}
            · {steps.length} step{steps.length === 1 ? '' : 's'} · {checked.length} file{checked.length === 1 ? '' : 's'}
          </span>
        </span>
        <button className="btn-text mono text-[11px] text-muted" onClick={() => closeReview()}>
          Esc to cancel
        </button>
      </div>
      <div className="px-5 pt-3 text-[15px]">{review.title}</div>
      {review.note && <div className="mono px-5 pt-1 text-[11px] text-muted">{review.note}</div>}
      <div className="min-h-0 flex-1 overflow-y-auto py-2">
        {steps.map((s, si) => {
          const sel = s.files.filter((f) => f.checked)
          const all = sel.length === s.files.length
          return (
            <div key={si} className="border-b border-line">
              <div className="flex cursor-default items-center gap-3 px-5 py-2 hover:bg-hover" onClick={() => setOpen(open === si ? null : si)}>
                <span className="serif w-5 shrink-0 text-[14px]  leading-none text-accent">{si + 1}</span>
                <input
                  type="checkbox"
                  checked={all}
                  ref={(el) => {
                    if (el) el.indeterminate = !all && sel.length > 0
                  }}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => toggle(si, null, e.target.checked)}
                  className="h-3.5 w-3.5 accent-[var(--accent)]"
                />
                <span className="flex-1">
                  {s.label}
                  {(s.lossy || s.permanent) && <span className="mono ml-3 text-[10.5px] text-danger">{s.lossy ? 'lossy' : 'permanent'}</span>}
                  {s.confirm && !s.lossy && !s.permanent && <span className="mono ml-3 text-[10.5px] text-muted">confirm</span>}
                </span>
                <span className="mono text-[12px] text-muted">
                  {sel.length} file{sel.length === 1 ? '' : 's'} · {size(sel.reduce((a, f) => a + f.size, 0))}
                </span>
                {open === si ? <ChevronDown size={14} className="text-muted" /> : <ChevronRight size={14} className="text-muted" />}
              </div>
              {s.detail && <div className="mono px-5 pb-2 pl-12 text-[11px] text-muted">{s.detail}</div>}
              {open === si && (
                <div className="pb-2">
                  {s.files.map((f, fi) => (
                    <label key={f.path} className="flex items-start gap-3 px-5 py-1 pl-12 hover:bg-hover" title={f.path}>
                      <input
                        type="checkbox"
                        checked={f.checked}
                        onChange={(e) => toggle(si, fi, e.target.checked)}
                        className="mt-[3px] h-3.5 w-3.5 shrink-0 accent-[var(--accent)]"
                      />
                      <span className="min-w-0 flex-1">
                        {/* Cut long paths at the start, so the file name stays visible. */}
                        <span className="mono block truncate text-left text-[12px]" style={{ direction: 'rtl' }}>
                          <bdi>{tildify(f.path, home)}</bdi>
                        </span>
                        {f.note && <span className="block truncate text-[12px] text-muted">{f.note}</span>}
                      </span>
                      <span className="mono shrink-0 text-[12px] text-muted">{size(f.size)}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
      <div className="flex items-center gap-2 border-t border-line bg-bg px-5 py-3">
        <span className="mono text-[11px] text-muted">
          Uses {size(review.cloudBytes) || '0 B'} cloud ·{' '}
          <span className={review.reversible ? '' : 'text-danger'}>{review.reversible ? 'Reversible' : 'Not reversible'}</span>
          {review.frees && <> · Frees {size(bytes)}</>}
        </span>
        <span className="ml-auto" />
        {armed && <span className="mono text-[11px] text-danger">Press Enter again to confirm</span>}
        <button
          className="btn-primary disabled:opacity-40"
          style={danger ? { background: 'var(--danger)' } : undefined}
          disabled={!checked.length || running}
          onClick={() => void run()}
        >
          {running ? 'Running…' : 'Run'}
        </button>
      </div>
    </aside>
  )
}
