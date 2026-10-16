import { date, size, tildify } from '../lib/format'
import { whyLabel } from '../lib/search'
import type { SearchHit } from '../lib/types'
import { useApp } from '../store'
import { FileIcon } from './FileIcon'

export function Name({ name, positions }: { name: string; positions?: number[] }) {
  if (!positions?.length) return <>{name}</>
  const set = new Set(positions)
  return (
    <>
      {[...name].map((ch, i) =>
        set.has(i) ? (
          <span key={i} className="text-accent">
            {ch}
          </span>
        ) : (
          <span key={i}>{ch}</span>
        )
      )}
    </>
  )
}

export function Snippet({ hit }: { hit: SearchHit }) {
  if (!hit.snippet?.length) return null
  return (
    <div className="truncate text-muted">
      {hit.snippet.map((s, i) =>
        s.hit ? (
          <b key={i} className="font-semibold text-text">
            {s.t}
          </b>
        ) : (
          <span key={i}>{s.t}</span>
        )
      )}
    </div>
  )
}

export default function HitRow({ hit, active, full, onClick, onDoubleClick }: { hit: SearchHit; active: boolean; full?: boolean; onClick: () => void; onDoubleClick?: () => void }) {
  const home = useApp((s) => s.places?.home ?? '')
  return (
    <div
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      data-active={active || undefined}
      className={`grid cursor-default items-start gap-4 px-5 py-2 transition-colors duration-100 ${active ? 'bg-select' : 'hover:bg-hover'}`}
      style={{ gridTemplateColumns: full ? 'minmax(0,1fr) 220px 90px 110px 130px' : 'minmax(0,1fr) 260px 130px' }}
    >
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2">
          <FileIcon ext={hit.name.includes('.') ? hit.name.slice(hit.name.lastIndexOf('.') + 1).toLowerCase() : ''} isDir={hit.is_dir} />
          <span className="truncate">
            <Name name={hit.name} positions={hit.positions} />
          </span>
        </div>
        {hit.snippet.length > 0 && (
          <div className="pl-6">
            <Snippet hit={hit} />
          </div>
        )}
      </div>
      <div className="mono truncate pt-px text-[12px] text-muted" title={hit.parent}>
        {tildify(hit.parent, home)}
      </div>
      {full && <div className="mono truncate pt-px text-right text-[12px] text-muted">{hit.is_dir ? '' : size(hit.size)}</div>}
      {full && <div className="mono truncate pt-px text-[12px] text-muted">{date(hit.mtime)}</div>}
      <div className="truncate pt-px text-right text-[12px] text-muted">{whyLabel(hit)}</div>
    </div>
  )
}
