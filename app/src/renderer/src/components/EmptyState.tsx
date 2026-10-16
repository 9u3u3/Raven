import type { ReactNode } from 'react'

/** Line art for empty places. A small wobble makes the strokes look drawn by hand. */
function FolderArt() {
  return (
    <svg width="190" height="124" viewBox="0 0 190 124" fill="none" aria-hidden className="sketch">
      <path d="M20 112h150" className="stroke-line" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M30 42c0-5 4-9 9-9h32l10 12h62c5 0 9 4 9 9v46c0 5-4 9-9 9H39c-5 0-9-4-9-9V42Z" className="stroke-k-folder fill-k-folder" fillOpacity="0.08" strokeWidth="1.6" strokeDasharray="5 6" strokeLinecap="round" />
      <g className="anim-float">
        <path d="M80 22h30l9 9v32H80V22Z" className="fill-bg stroke-text" strokeWidth="1.4" strokeLinejoin="round" />
        <path d="M110 22v9h9M88 42h22M88 50h22M88 58h14" className="stroke-text" strokeWidth="1.3" strokeLinecap="round" />
      </g>
      <path d="M146 24l8 0M150 20l0 8" className="stroke-accent" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M34 22c3-2 6-2 9 0" className="stroke-muted" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  )
}

function SearchArt() {
  return (
    <svg width="190" height="124" viewBox="0 0 190 124" fill="none" aria-hidden className="sketch">
      <path d="M30 112h130" className="stroke-line" strokeWidth="1.3" strokeLinecap="round" />
      <g className="anim-float">
        <circle cx="82" cy="54" r="30" className="stroke-text" strokeWidth="1.6" />
        <path d="m104 76 26 28" className="stroke-accent" strokeWidth="3" strokeLinecap="round" />
        <path d="M66 54h32M68 44h20M68 64h12" className="stroke-muted" strokeWidth="1.4" strokeLinecap="round" />
      </g>
      <path d="M146 30l8 0M150 26l0 8" className="stroke-accent" strokeWidth="1.3" strokeLinecap="round" />
      <circle cx="40" cy="36" r="2" className="fill-muted" />
    </svg>
  )
}

function ChartArt() {
  return (
    <svg width="190" height="124" viewBox="0 0 190 124" fill="none" aria-hidden className="sketch">
      <path d="M22 106h146" className="stroke-text" strokeWidth="1.4" strokeLinecap="round" />
      <rect x="34" y="50" width="24" height="56" className="fill-k-image stroke-k-image" fillOpacity="0.2" strokeWidth="1.5" />
      <rect x="68" y="24" width="24" height="82" className="fill-accent stroke-accent" fillOpacity="0.2" strokeWidth="1.5" />
      <rect x="102" y="40" width="24" height="66" className="fill-k-video stroke-k-video" fillOpacity="0.2" strokeWidth="1.5" />
      <rect x="136" y="68" width="24" height="38" className="fill-k-folder stroke-k-folder" fillOpacity="0.2" strokeWidth="1.5" />
      <path d="M30 30l6 5M158 40l-5 6" className="stroke-muted" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  )
}

const ART = { folder: FolderArt, search: SearchArt, chart: ChartArt }

export function EmptyState({ art = 'folder', title, hint, children }: { art?: keyof typeof ART; title: string; hint?: string; children?: ReactNode }) {
  return (
    // One line, no picture (PLAN2 P2-10.2). The hint is a tooltip.
    <div className="anim-fade flex items-center justify-center gap-3 px-4 py-6 text-muted" title={hint}>
      <span>{title}</span>
      {children && <span className="flex items-center gap-2">{children}</span>}
    </div>
  )
}

export function EmptyFolder() {
  return (
    <EmptyState title="This folder is empty">
      <kbd className="kbd">Ctrl</kbd>
      <kbd className="kbd">Shift</kbd>
      <kbd className="kbd">N</kbd>
    </EmptyState>
  )
}
