import { Folder, Server } from 'lucide-react'
import { date, size, tildify } from '../lib/format'
import { folderWhy } from '../lib/search'
import type { FolderHit } from '../lib/types'
import { useApp } from '../store'

// One folder result: the name, what it holds (the summary or the best files) and where it is.
export default function FolderRow({ hit, active, full, onClick, onDoubleClick }: { hit: FolderHit; active: boolean; full?: boolean; onClick: () => void; onDoubleClick?: () => void }) {
  const home = useApp((s) => s.places?.home ?? '')
  const Icon = hit.why === 'connect' ? Server : Folder
  const facts = hit.files !== null && hit.files !== undefined ? `${hit.files} ${hit.files === 1 ? 'file' : 'files'}${hit.size ? ` · ${size(hit.size)}` : ''}` : ''
  const label = hit.why === 'connect' ? `Connect to ${hit.path}` : hit.why === 'path' ? tildify(hit.path, home) : hit.name
  return (
    <div
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      data-active={active || undefined}
      className={`grid cursor-default items-start gap-4 px-5 py-2 transition-colors duration-100 ${active ? 'bg-select' : 'hover:bg-hover'}`}
      style={{ gridTemplateColumns: full ? 'minmax(0,1fr) 220px 110px 110px 90px' : 'minmax(0,1fr) 260px 130px' }}
    >
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2">
          <Icon size={16} strokeWidth={1.5} className="shrink-0 text-k-folder" aria-hidden />
          <span className="mono truncate">{label}</span>
        </div>
        {(hit.summary || hit.project || hit.top.length > 0) && (
          <div className="truncate pl-6 text-muted">
            {hit.project && (
              <span className="mono mr-2 text-[11px] text-accent">project “{hit.project}”</span>
            )}
            {hit.summary && hit.why !== 'connect' ? hit.summary : ''}
            {hit.summary && hit.top.length > 0 && hit.why !== 'connect' ? ' · ' : ''}
            {!hit.summary && hit.files === null && hit.top.length > 0 ? 'not indexed · ' : ''}
            {hit.top.map((t, i) => (
              <span key={t.path}>
                {i > 0 && ', '}
                <span className="text-text">{t.name}</span>
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="mono truncate pt-px text-[12px] text-muted" title={hit.parent}>
        {hit.why === 'path' || hit.why === 'connect' ? '' : tildify(hit.parent, home)}
      </div>
      {full && <div className="mono truncate pt-px text-right text-[12px] text-muted">{facts}</div>}
      {full && <div className="mono truncate pt-px text-[12px] text-muted">{hit.mtime ? date(hit.mtime) : ''}</div>}
      <div className="truncate pt-px text-right text-[12px] text-muted">{full ? folderWhy(hit) : facts || folderWhy(hit)}</div>
    </div>
  )
}
