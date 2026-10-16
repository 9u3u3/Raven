import { Moon, MonitorSmartphone, Redo2, Search, Settings, Sun, Undo2 } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { redo, setPref, setScreen, undo, useApp, type Screen } from '../store'

const SCREENS: [Screen, string][] = [
  ['files', 'Files'],
  ['space', 'Space'],
  ['organize', 'Organize'],
  ['privacy', 'Privacy']
]

/** The Raven mark, the same as on the site (raven-site/assets/mark.svg): three facets and an eye. */
export function Mark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden className="shrink-0">
      <path fill="var(--mark-1)" d="M21 11 34 10 44 20 57 25 61 33 40 31 21 31Z" />
      <path fill="var(--mark-2)" d="M21 31 40 31 61 33 46 37 37 46 31 45 27 53 22 46 12 53Z" />
      <path fill="var(--mark-3)" d="M21 11 21 31 12 53 6 33 11 17Z" />
      <path fill="var(--bg)" d="M28 17h5v5h-5Z" />
    </svg>
  )
}

/** The word "raven" as on the site lockup: Archivo, heavy, tight. */
export function Wordmark({ size = 19 }: { size?: number }) {
  return (
    <span className="leading-none" style={{ fontFamily: 'var(--font-disp)', fontWeight: 900, fontSize: size, letterSpacing: '-0.045em' }}>
      raven
    </span>
  )
}

// The search line tells what the command bar can do, one sentence at a time.
const HINTS = ['Find a file by what it says or shows', 'Ask a question about your files', 'Tell Raven what to do, like “organize my downloads”']

function RotatingHint() {
  const [i, setI] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setI((x) => (x + 1) % HINTS.length), 3800)
    return () => clearInterval(t)
  }, [])
  return (
    <span key={i} className="anim-row min-w-0 flex-1 truncate " style={{ fontFamily: 'var(--font-disp)', fontSize: '1.08rem' }}>
      {HINTS[i]}
    </span>
  )
}

function Tip({ children, label }: { children: React.ReactNode; label: string }) {
  return (
    <span className="group/tip relative inline-flex">
      {children}
      <span className="pointer-events-none absolute right-0 top-full z-50 mt-2 whitespace-nowrap bg-console-bg px-2.5 py-1 text-[11px] text-console-text opacity-0 transition-opacity duration-150 group-hover/tip:opacity-100" style={{ borderRadius: 2 }}>
        {label}
      </span>
    </span>
  )
}

export default function TopBar() {
  const screen = useApp((s) => s.screen)
  const journal = useApp((s) => s.journal)
  const theme = useApp((s) => s.theme)
  const nav = useRef<HTMLElement>(null)
  const [ink, setInk] = useState<{ left: number; width: number } | null>(null)
  const activeId: Screen | null = screen === 'results' ? 'files' : SCREENS.some(([id]) => id === screen) ? screen : null

  // One ink line under the screen you are on. It slides when you change screen.
  useLayoutEffect(() => {
    const el = nav.current?.querySelector<HTMLElement>(`[data-nav="${activeId}"]`)
    setInk(el ? { left: el.offsetLeft, width: el.offsetWidth } : null)
  }, [activeId])

  const nextTheme = theme === 'system' ? 'light' : theme === 'light' ? 'dark' : 'system'
  const ThemeIcon = theme === 'dark' ? Moon : theme === 'light' ? Sun : MonitorSmartphone

  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-line px-6">
      <div className="flex items-center gap-2.5">
        <Mark size={24} />
        <Wordmark />
      </div>

      <nav ref={nav} data-tour="nav" className="relative flex h-full items-center gap-7">
        {SCREENS.map(([id, label]) => {
          const on = activeId === id
          return (
            <button
              key={id}
              data-nav={id}
              onClick={() => setScreen(id)}
              className={`group relative h-full text-[14px] transition-colors duration-200 ${on ? 'text-text' : 'text-muted hover:text-text'}`}
            >
              <span className="inline-block transition-transform duration-200 group-hover:-translate-y-px">{label}</span>
            </button>
          )
        })}
        {ink && <span aria-hidden className="absolute bottom-0 h-[2px] bg-accent transition-all duration-[420ms] ease-[cubic-bezier(0.3,0.9,0.3,1)]" style={{ left: ink.left, width: ink.width }} />}
      </nav>

      <button
        data-tour="search"
        onClick={() => useApp.setState({ cmdOpen: true })}
        className="group ml-auto flex h-9 w-[min(480px,36vw)] items-center gap-3 border-b border-line text-left text-muted transition-colors duration-200 hover:border-accent hover:text-text"
      >
        <Search size={16} strokeWidth={1.5} className="shrink-0 transition-transform duration-300 group-hover:scale-110 group-hover:text-accent" />
        <RotatingHint />
        <span className="flex shrink-0 items-center gap-1 pb-0.5">
          <kbd className="kbd">Ctrl</kbd>
          <kbd className="kbd">K</kbd>
        </span>
      </button>

      <div className="flex items-center">
        <Tip label={journal.undo ? `Undo: ${journal.undo} (Ctrl+Z)` : 'Nothing to undo'}>
          <button className="icon-btn" disabled={!journal.undo} onClick={() => void undo()} aria-label="Undo">
            <Undo2 size={16} strokeWidth={1.5} />
          </button>
        </Tip>
        <Tip label={journal.redo ? `Redo: ${journal.redo} (Ctrl+Shift+Z)` : 'Nothing to redo'}>
          <button className="icon-btn" disabled={!journal.redo} onClick={() => void redo()} aria-label="Redo">
            <Redo2 size={16} strokeWidth={1.5} />
          </button>
        </Tip>
        <span className="mx-2 h-4 w-px bg-line" />
        <Tip label={`Theme: ${theme}. Click for ${nextTheme}`}>
          <button className="icon-btn" onClick={() => setPref('theme', nextTheme)} aria-label="Change theme">
            <ThemeIcon size={16} strokeWidth={1.5} />
          </button>
        </Tip>
        <Tip label="Settings (Ctrl+,)">
          <button className="icon-btn" data-active={screen === 'settings' ? 'true' : undefined} onClick={() => setScreen('settings')} aria-label="Settings">
            <Settings size={16} strokeWidth={1.5} />
          </button>
        </Tip>
      </div>
    </header>
  )
}
