import { ChevronDown, type LucideIcon } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'

// Shared building blocks. The language: hairlines, a serif for headings, text instead of boxes.

/** A number that counts up to its value, so a change is felt and not just shown. */
export function useCountUp(target: number, ms = 900): number {
  const [v, setV] = useState(0)
  const from = useRef(0)
  useEffect(() => {
    const start = performance.now()
    const a = from.current
    let raf = 0
    const tick = (t: number): void => {
      const p = Math.min(1, (t - start) / ms)
      const cur = a + (target - a) * (1 - Math.pow(1 - p, 3))
      from.current = cur
      setV(cur)
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return v
}

/** True one frame after mount, so lines grow from zero when they appear. */
export function useGrown(): boolean {
  const [on, setOn] = useState(false)
  useEffect(() => {
    const r = requestAnimationFrame(() => setOn(true))
    return () => cancelAnimationFrame(r)
  }, [])
  return on
}

/** The delay of one item in a staggered entrance (see .anim-row, .anim-reveal). */
export const stagger = (i: number): CSSProperties => ({ ['--i' as string]: Math.min(i, 14) }) as CSSProperties

/** A thin measuring line with a tick where the value ends, like a ruler. */
export function Track({ pct, color, h = 2, delay = 0 }: { pct: number; color: string; h?: number; delay?: number }) {
  const on = useGrown()
  const w = on ? Math.max(0, Math.min(100, pct)) : 0
  return (
    <div className="relative w-full bg-line" style={{ height: h }}>
      <div className="h-full transition-[width] duration-[900ms] ease-[cubic-bezier(0.3,0.7,0.2,1)]" style={{ width: `${w}%`, background: color, transitionDelay: `${delay}ms` }} />
      <span className="absolute top-1/2 h-[7px] w-px -translate-y-1/2 transition-[left] duration-[900ms] ease-[cubic-bezier(0.3,0.7,0.2,1)]" style={{ left: `${w}%`, background: color, transitionDelay: `${delay}ms` }} />
    </div>
  )
}

/** Tabs with one ink line under the chosen word. The line slides from word to word. */
export function Seg<T extends string>({ options, value, onChange, size = 'md' }: { options: [T, string][]; value: T; onChange: (v: T) => void; size?: 'sm' | 'md' }) {
  const wrap = useRef<HTMLDivElement>(null)
  const [ink, setInk] = useState<{ left: number; width: number } | null>(null)
  const found = options.findIndex(([id]) => id === value)
  useLayoutEffect(() => {
    const el = wrap.current?.querySelector<HTMLElement>(`[data-seg="${found}"]`)
    setInk(el && found >= 0 ? { left: el.offsetLeft, width: el.offsetWidth } : null)
  }, [found, options.length, options.map((o) => o[1]).join('|')]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div ref={wrap} className="relative inline-flex items-center gap-5">
      {options.map(([id, label], k) => (
        <button
          key={id}
          data-seg={k}
          onClick={() => onChange(id)}
          className={`whitespace-nowrap py-1.5 transition-colors duration-200 ${size === 'sm' ? 'text-[12px]' : ''} ${value === id ? 'text-text' : 'text-muted hover:text-text'}`}
        >
          {label}
        </button>
      ))}
      {ink && <span aria-hidden className="absolute bottom-0 h-[1.5px] bg-accent transition-all duration-[380ms] ease-[cubic-bezier(0.3,0.9,0.3,1)]" style={{ left: ink.left, width: ink.width }} />}
    </div>
  )
}

/** A small mono label. No box. The tone is the color of the word, with a dot for the good and bad kinds. */
export function Chip({ tone = 'plain', children }: { tone?: 'good' | 'bad' | 'warn' | 'plain'; children: ReactNode }) {
  const cls = tone === 'good' ? 'text-ok' : tone === 'bad' ? 'text-danger' : tone === 'warn' ? 'text-accent' : 'text-muted'
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap text-[12px] ${cls}`}>
      {tone !== 'plain' && <span className="inline-block h-[5px] w-[5px] rounded-full bg-current" />}
      {children}
    </span>
  )
}

/** The top of a screen: a serif title that rises into place, one line of purpose, and a hairline that draws itself. */
export function PageHeader({ title, text, children, compact }: { icon?: LucideIcon; title: string; text: string; children?: ReactNode; compact?: boolean }) {
  return (
    <div>
      <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
        <div className="min-w-[240px] flex-1">
          <h1 className={`serif anim-reveal leading-[1.02] transition-all ${compact ? 'text-[18px]' : 'text-[22px]'}`} style={stagger(0)}>
            {title}
          </h1>
          {!compact && text && (
            <p className="anim-row mt-1.5 max-w-[620px] text-muted" style={stagger(2)}>
              {text}
            </p>
          )}
        </div>
        <div className="anim-row pb-1" style={stagger(3)}>
          {children}
        </div>
      </div>
      <div className={`anim-rule h-px bg-line ${compact ? "mt-3" : "mt-5"}`} style={stagger(4)} />
    </div>
  )
}

/** A block of content set off by a rule above it, not a box around it. */
export function Card({ children, className = '', i }: { children: ReactNode; className?: string; i?: number }) {
  return (
    <div className={`${i !== undefined ? 'anim-row' : ''} border-t border-line ${className}`} style={i !== undefined ? stagger(i) : undefined}>
      {children}
    </div>
  )
}

/** A small heading above a group of things: the word, a rule that runs to the right, and a note. */
export function SectionTitle({ children, note }: { children: ReactNode; note?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center gap-4">
      <span className="label shrink-0">{children}</span>
      <span className="anim-rule h-px flex-1 bg-line" />
      {note && <span className="shrink-0 text-[12px] text-muted">{note}</span>}
    </div>
  )
}

/** A text action with an arrow. The underline wipes in, and the arrow steps forward. */
export function Ghost({ children, onClick, disabled, tone = 'accent', title }: { children: ReactNode; onClick: () => void; disabled?: boolean; tone?: 'accent' | 'plain' | 'danger'; title?: string }) {
  const cls = tone === 'accent' ? 'text-accent' : tone === 'danger' ? 'text-danger' : 'text-text'
  return (
    <button title={title} disabled={disabled} onClick={onClick} className={`group inline-flex items-center gap-1.5 whitespace-nowrap transition-opacity active:translate-y-px disabled:opacity-40 ${cls}`}>
      <span className="link-g inline-flex items-center gap-1.5">{children}</span>
      <span aria-hidden className="inline-block -translate-x-1 opacity-60 transition-all duration-200 group-hover:translate-x-0 group-hover:opacity-100">
        →
      </span>
    </button>
  )
}

/** An on and off switch: a thin track and a small square knob. */
export function Switch({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button
      role="switch"
      aria-checked={on}
      aria-label={label}
      title={label}
      onClick={() => onChange(!on)}
      className={`relative h-[18px] w-[34px] shrink-0 border transition-colors duration-200 ${on ? 'border-accent bg-accent-soft' : 'border-muted/60'}`}
      style={{ borderRadius: 9 }}
    >
      <span className={`absolute top-[2px] h-[12px] w-[12px] rounded-full transition-all duration-300 ease-[cubic-bezier(0.3,0.9,0.3,1)] ${on ? 'left-[18px] bg-accent' : 'left-[2px] bg-muted'}`} />
    </button>
  )
}

/** A hairline that draws itself in. */
export function Rule({ i = 0, className = '' }: { i?: number; className?: string }) {
  return <div className={`anim-rule h-px bg-line ${className}`} style={stagger(i)} />
}

/**
 * The branch lines of a tree. `trail[k]` says whether the ancestor at depth k has more siblings below, so its vertical
 * line continues. `last` says this item is the last child, so its own branch turns into an elbow.
 */
export function TreeGuides({ trail, last, step = 18, draw = true }: { trail: boolean[]; last: boolean; step?: number; draw?: boolean }) {
  const grow = draw ? { animation: 'rv-grow-y 420ms cubic-bezier(0.3,0.7,0.2,1) both', transformOrigin: 'top' } : undefined
  const stub = draw ? { animation: 'rv-rule 360ms cubic-bezier(0.3,0.7,0.2,1) 160ms both', transformOrigin: 'left' } : undefined
  return (
    <span aria-hidden className="flex shrink-0 self-stretch">
      {trail.map((more, k) => (
        <span key={k} className="relative block" style={{ width: step }}>
          {more && <span className="absolute bottom-0 top-0 w-px bg-muted/60" style={{ left: step / 2, ...grow }} />}
        </span>
      ))}
      <span className="relative block" style={{ width: step }}>
        <span className="absolute top-0 w-px bg-muted/60" style={{ left: step / 2, height: last ? '50%' : '100%', ...grow }} />
        <span className="absolute top-1/2 h-px bg-muted/60" style={{ left: step / 2, width: step / 2 - 1, ...stub }} />
      </span>
    </span>
  )
}

/**
 * A drag edge for a side column. `side` is the edge it sits on: "right" for a left column, "left" for a right
 * column. Dragging calls onWidth live; onDone saves the width. A double-click resets it.
 */
export function ResizeEdge({ side, get, onWidth, onDone, min, max, reset }: {
  side: 'left' | 'right'
  get: () => number
  onWidth: (w: number) => void
  onDone: (w: number) => void
  min: number
  max: number
  reset: number
}) {
  const start = (e: React.PointerEvent<HTMLDivElement>): void => {
    e.preventDefault()
    const x0 = e.clientX
    const w0 = get()
    let w = w0
    const move = (ev: PointerEvent): void => {
      const dx = ev.clientX - x0
      w = Math.round(Math.min(max, Math.max(min, side === 'right' ? w0 + dx : w0 - dx)))
      onWidth(w)
    }
    const up = (): void => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      onDone(w)
    }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      title="Drag to resize · double-click to reset"
      onPointerDown={start}
      onDoubleClick={() => onDone(reset)}
      className={`group absolute top-0 z-20 h-full w-[6px] cursor-col-resize ${side === 'right' ? '-right-[3px]' : '-left-[3px]'}`}
    >
      <span className="absolute left-[2px] top-0 h-full w-[2px] bg-accent opacity-0 transition-opacity group-hover:opacity-100" />
    </div>
  )
}

/** A label with its description hidden. The chevron after the label shows the description and hides it again. */
export function Explained({ label, text, className = '' }: { label: ReactNode; text?: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false)
  if (!text) return <div className={className}>{label}</div>
  return (
    <div className={className}>
      <button
        type="button"
        aria-expanded={open}
        title={open ? 'Hide the description' : 'Show the description'}
        onClick={() => setOpen((v) => !v)}
        className="group inline-flex max-w-full items-center gap-1.5 text-left"
      >
        {label}
        <ChevronDown size={13} strokeWidth={1.8} className={`shrink-0 text-muted transition-transform duration-200 group-hover:text-text ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && <div className="anim-fade mt-1 max-w-[560px] text-[12.5px] leading-snug text-muted">{text}</div>}
    </div>
  )
}
