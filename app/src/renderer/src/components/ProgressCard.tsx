import { useApp } from '../store'

/** A long change with a known item count (a permanent delete): a card above the status line with the count,
 * the current item and a bar. The sidecar sends the progress (delete_progress). */
export default function ProgressCard() {
  const p = useApp((s) => s.progress)
  if (!p) return null
  const pct = p.total ? Math.floor((p.done / p.total) * 100) : 0
  return (
    <div role="status" aria-live="polite" className="fixed bottom-12 right-6 z-40 w-[340px] float-panel anim-pop p-4">
      <div className="mb-1 flex items-baseline justify-between gap-3">
        <span className="font-medium">{p.label}</span>
        <span className="mono text-[11px] text-muted">{pct}%</span>
      </div>
      <div className="mono mb-3 truncate text-[11px] text-muted" title={p.name}>
        {p.done} of {p.total} items{p.name ? ` · ${p.name}` : ''}
      </div>
      <div className="h-[3px] w-full bg-line">
        <div className="h-full bg-accent transition-[width] duration-300" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}
