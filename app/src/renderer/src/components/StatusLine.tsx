import { size } from '../lib/format'
import IndexMeter from './IndexMeter'
import { controlJob, setScreen, undo, useApp } from '../store'
import type { CopyJob } from '../lib/types'

/** The running copy or move: progress, speed and time left, with Pause and Cancel (section 6.1). */
function JobLine({ job, queued }: { job: CopyJob; queued: number }) {
  const pct = job.bytes_total ? Math.floor((job.bytes_done / job.bytes_total) * 100) : 0
  const eta = job.eta == null ? '' : job.eta < 60 ? `${job.eta} s left` : `${Math.ceil(job.eta / 60)} min left`
  return (
    <span className="flex shrink-0 items-center gap-2 text-text">
      <span>
        {job.status === 'paused' ? 'Paused' : job.kind === 'copy' ? 'Copying' : 'Moving'} {job.count} item{job.count === 1 ? '' : 's'} {pct}%
      </span>
      <span className="inline-block h-[2px] w-24 bg-line">
        <span className="block h-full bg-accent transition-[width] duration-300" style={{ width: `${pct}%` }} />
      </span>
      {job.status === 'running' && job.speed > 0 && <span className="text-muted">{size(job.speed)}/s{eta ? ` · ${eta}` : ''}</span>}
      {queued > 0 && <span className="text-muted">· {queued} more queued</span>}
      <button className="link text-accent" onClick={() => controlJob(job.id, job.status === 'paused' ? 'resume' : 'pause')}>
        {job.status === 'paused' ? 'Resume' : 'Pause'}
      </button>
      <button className="link text-danger" onClick={() => controlJob(job.id, 'cancel')}>
        Cancel
      </button>
    </span>
  )
}

export function indexText(i: ReturnType<typeof useApp.getState>['index']): string {
  if (!i) return 'Index: starting'
  if (!i.roots.length) return 'No folders are indexed'
  if (i.stage === 'metadata') return `indexing names ${i.scanned.toLocaleString()}`
  if (i.text_pending > 0) {
    const pct = Math.floor(((i.files - i.text_pending) / Math.max(i.files, 1)) * 100)
    return `indexing text ${pct}%`
  }
  if (i.embed_pending > 0 && i.embed_ready) {
    const pct = Math.floor(((i.chunks - i.embed_pending) / Math.max(i.chunks, 1)) * 100)
    return `indexing meaning ${pct}%`
  }
  // The last stage: images, PDF pages and video frames for search by appearance.
  if ((i.visual_pending ?? 0) > 0) return `indexing images · ${i.visual_pending!.toLocaleString()} left`
  return `Indexed ${i.files.toLocaleString()} files`
}

export default function StatusLine() {
  const index = useApp((s) => s.index)
  const free = useApp((s) => s.free)
  const status = useApp((s) => s.status)
  const journal = useApp((s) => s.journal)
  const ghost = useApp((s) => s.ghost)
  const privacy = useApp((s) => s.privacy)
  // Section 12.7: offline, the mode line says so, and heavy tasks run on the local model.
  const modeLabel =
    privacy?.ai_mode === 'local' ? 'Local only' : privacy?.online === false ? 'LOCAL ONLY (offline)' : privacy?.ai_mode === 'ask' ? 'Ask each time' : 'Hybrid'
  const noRoots = index && !index.roots.length
  const jobs = useApp((s) => s.jobs)
  const live = Object.values(jobs).filter((j) => j.status === 'running' || j.status === 'paused')
  const queued = Object.values(jobs).filter((j) => j.status === 'queued').length
  return (
    <footer data-tour="status" className="mono flex h-8 shrink-0 items-center gap-3 overflow-hidden whitespace-nowrap border-t border-line px-6 pr-16 text-[11px] text-muted">
      <span className={`flex items-center gap-1.5 ${index?.embed_error ? 'text-danger' : ''}`} title={index?.embed_error ?? ''}>
        <span className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${index?.embed_error ? 'bg-danger' : index && (index.text_pending > 0 || index.embed_pending > 0 || (index.visual_pending ?? 0) > 0 || index.stage === 'metadata') ? 'anim-breathe bg-accent' : 'bg-ok'}`} />
        {index ? <IndexMeter index={index} error={index.embed_error} /> : indexText(index)}
      </span>
      {noRoots && (
        <button className="link text-accent" onClick={() => setScreen('settings')}>
          Add folder
        </button>
      )}
      <span className="text-muted/50">·</span>
      <button className="link text-accent" onClick={() => setScreen('privacy')} title="AI mode. Open Privacy to change it">
        {modeLabel}
      </button>
      <span className="text-muted/50">·</span>
      <button className="link min-w-0 truncate text-muted transition-colors hover:text-text" onClick={() => setScreen('privacy')} title="See every payload in the privacy ledger">
        {privacy?.today.calls ?? 0} cloud call{privacy?.today.calls === 1 ? '' : 's'} today · {size(privacy?.today.bytes ?? 0) || '0 B'} sent
        {(privacy?.today.pii_sent ?? 0) > 0 && ` · ${privacy?.today.pii_sent} private items sent`}
        {(privacy?.today.mcp_calls ?? 0) > 0 && ` · ${privacy?.today.mcp_calls} MCP answers`}
      </button>
      {free && (
        <>
          <span className="text-muted/50">·</span>
          <span>
            {size(free.free)} free
            {ghost && ghost.ghost > 0 && <span title="Space that all Raven archives need if you restore them"> (+{size(ghost.ghost)} ghost)</span>}
          </span>
        </>
      )}
      <span className="ml-auto flex min-w-0 items-center gap-3">
        {live.length > 0 && <JobLine job={live[0]} queued={queued + live.length - 1} />}
        {status && !live.length && (
          <span key={status.text} className={`anim-pop truncate ${status.kind === 'error' ? 'text-danger' : 'text-text'}`}>
            {status.kind === 'busy' && <span className="anim-breathe mr-2 inline-block h-[6px] w-[6px] rounded-full bg-accent" />}
            {status.text}
          </span>
        )}
        {status?.action && (
          <button className="link text-accent" onClick={status.action.run}>
            {status.action.label}
          </button>
        )}
        {journal.undo && status?.undo && (
          <button className="group flex shrink-0 items-center gap-1.5 text-accent" onClick={() => void undo()}>
            <span className="link-g">Undo (Ctrl+Z)</span>
            <span aria-hidden className="inline-block -translate-x-1 transition-transform duration-200 group-hover:translate-x-0">←</span>
          </button>
        )}
      </span>
    </footer>
  )
}
