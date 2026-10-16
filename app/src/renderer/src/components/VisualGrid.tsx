import { thumbUrl, visualThumbUrl } from '../lib/api'
import { clock } from '../lib/format'
import type { VisualHit } from '../lib/types'
import { reveal } from '../store'

/** What a match by appearance shows under its picture: the page, the moment or the photo kind. */
export function visualLabel(h: VisualHit): string {
  if (h.match === 'page') return `page ${h.at ?? 1}`
  if (h.match === 'frame') return h.moments.length > 1 ? h.moments.map(clock).join(' · ') : `at ${clock(h.at ?? 0)}`
  return h.kind
}

export function openVisual(h: VisualHit): void {
  reveal(h.path, true, h.match === 'page' ? h.at : null, h.match === 'frame' ? h.at : null)
}

/** Photos, PDF pages and video moments as a plain thumbnail grid with no frames (section 12.3). */
export default function VisualGrid({ hits, active, onActive, compact }: { hits: VisualHit[]; active?: string | null; onActive?: (path: string) => void; compact?: boolean }) {
  const px = compact ? 96 : 150
  return (
    <div className="grid gap-x-3 gap-y-2 px-4" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${px}px, 1fr))` }}>
      {hits.map((h) => (
        <div
          key={`${h.path}:${h.at ?? ''}`}
          data-active={active === h.path || undefined}
          title={`${h.name}\n${h.parent}`}
          onClick={() => (onActive ? onActive(h.path) : openVisual(h))}
          onDoubleClick={() => openVisual(h)}
          className={`group min-w-0 cursor-default p-1.5 transition-colors duration-100 ${active === h.path ? 'bg-select' : 'hover:bg-hover'}`}
        >
          <div className="flex items-center justify-center overflow-hidden bg-hover" style={{ height: compact ? 64 : 104 }}>
            <img
              src={h.thumb ? visualThumbUrl(h.thumb) : thumbUrl(h.path, 256, h.mtime)}
              className="max-h-full max-w-full object-contain transition-transform duration-300 ease-out group-hover:scale-105"
              loading="lazy"
              draggable={false}
              alt=""
            />
          </div>
          <div className="truncate pt-1 text-[12px]">{h.name}</div>
          {!compact && <div className="mono truncate text-[11px] text-muted">{visualLabel(h)}</div>}
        </div>
      ))}
    </div>
  )
}
