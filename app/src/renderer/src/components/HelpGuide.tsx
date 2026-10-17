import { useEffect, useLayoutEffect, useState, type ReactNode } from 'react'
import { setScreen, useApp, type Screen } from '../store'
import { stagger } from './ui'

// The "?" guide: a floating button, a visual guide to what Raven does, and a spotlight tour of the real screen.
// It only reads the screen. It keeps its own small state and changes nothing in the app.

type Feature = { id: string; title: string; line: string; keys: string[][]; art: ReactNode; go: { label: string; run: () => void } }

const openBar = (): void => useApp.setState({ cmdOpen: true })

/** Small drawings, one per feature. They use the theme colors. */
const Art = {
  find: (
    <svg viewBox="0 0 160 84" fill="none" aria-hidden className="sketch">
      <rect x="10" y="8" width="140" height="20" rx="10" className="fill-surface-2" />
      <circle cx="24" cy="18" r="4.5" className="stroke-accent" strokeWidth="1.6" />
      <path d="m28 22 3.5 3.5" className="stroke-accent" strokeWidth="1.6" strokeLinecap="round" />
      <rect x="38" y="16" width="44" height="4" rx="2" className="fill-muted" opacity="0.6" />
      {[0, 1, 2].map((i) => (
        <g key={i} transform={`translate(0 ${i * 15})`}>
          <rect x="14" y="36" width="9" height="9" rx="2.5" className={['fill-k-doc', 'fill-k-image', 'fill-k-folder'][i]} opacity="0.85" />
          <rect x="30" y="38" width={[62, 48, 70][i]} height="4" rx="2" className="fill-text" opacity="0.55" />
          <rect x="118" y="37" width="28" height="7" rx="3.5" className="fill-accent-soft" />
        </g>
      ))}
    </svg>
  ),
  ask: (
    <svg viewBox="0 0 160 84" fill="none" aria-hidden className="sketch">
      <rect x="12" y="8" width="82" height="7" rx="3.5" className="fill-text" opacity="0.8" />
      {[0, 1, 2].map((i) => (
        <rect key={i} x="12" y={24 + i * 10} width={[132, 120, 78][i]} height="4.5" rx="2.2" className="fill-muted" opacity="0.55" />
      ))}
      <rect x="84" y="24" width="10" height="4.5" rx="2" className="fill-accent" />
      <path d="M12 62h136" className="stroke-line" strokeWidth="1" />
      {[0, 1].map((i) => (
        <g key={i} transform={`translate(${i * 70} 0)`}>
          <rect x="12" y="68" width="10" height="10" rx="3" className="fill-accent-soft" />
          <text x="17" y="76" textAnchor="middle" fontSize="7" className="fill-accent" fontFamily="monospace">{i + 1}</text>
          <rect x="27" y="71" width="38" height="4" rx="2" className="fill-muted" opacity="0.6" />
        </g>
      ))}
    </svg>
  ),
  act: (
    <svg viewBox="0 0 160 84" fill="none" aria-hidden className="sketch">
      <rect x="10" y="8" width="140" height="18" rx="9" className="fill-surface-2" />
      <rect x="22" y="15" width="70" height="4" rx="2" className="fill-text" opacity="0.7" />
      {[0, 1, 2].map((i) => (
        <g key={i} transform={`translate(0 ${i * 14})`}>
          <circle cx="22" cy="42" r="6" className="fill-accent-soft" />
          <text x="22" y="45" textAnchor="middle" fontSize="7.5" className="fill-accent" fontFamily="monospace">{i + 1}</text>
          <rect x="36" y="40" width={[58, 44, 66][i]} height="4" rx="2" className="fill-text" opacity="0.5" />
          <rect x="120" y="38" width="22" height="8" rx="4" className="fill-surface-2" />
        </g>
      ))}
      <rect x="108" y="70" width="40" height="11" rx="5.5" className="fill-accent" />
      <path d="M118 75.5h14m-4-3 3 3-3 3" className="stroke-bg" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  space: (
    <svg viewBox="0 0 160 84" fill="none" aria-hidden className="sketch">
      <rect x="10" y="8" width="70" height="46" rx="4" className="fill-k-folder" opacity="0.55" />
      <rect x="83" y="8" width="67" height="28" rx="4" className="fill-k-video" opacity="0.55" />
      <rect x="83" y="39" width="32" height="15" rx="4" className="fill-k-image" opacity="0.55" />
      <rect x="118" y="39" width="32" height="15" rx="4" className="fill-k-audio" opacity="0.55" />
      <rect x="10" y="62" width="140" height="6" rx="3" className="fill-line" />
      <rect x="10" y="62" width="82" height="6" rx="3" className="fill-accent" />
      <rect x="92" y="62" width="22" height="6" className="fill-accent" opacity="0.35" />
      <rect x="10" y="73" width="48" height="4" rx="2" className="fill-muted" opacity="0.5" />
    </svg>
  ),
  organize: (
    <svg viewBox="0 0 160 84" fill="none" aria-hidden className="sketch">
      <g opacity="0.9">
        {[0, 1, 2, 3].map((i) => (
          <g key={i} transform={`translate(0 ${i * 17})`}>
            <rect x="12" y="10" width="9" height="9" rx="2.5" className={['fill-k-doc', 'fill-k-image', 'fill-k-doc', 'fill-k-archive'][i]} />
            <rect x="26" y="12" width={[36, 28, 40, 24][i]} height="4" rx="2" className="fill-muted" opacity="0.6" />
          </g>
        ))}
      </g>
      <path d="M78 42h14m-4-4 4 4-4 4" className="stroke-accent" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      {[0, 1].map((i) => (
        <g key={i} transform={`translate(0 ${i * 32})`}>
          <rect x="102" y="10" width="11" height="9" rx="2.5" className="fill-k-folder" />
          <rect x="118" y="12" width="30" height="4" rx="2" className="fill-text" opacity="0.6" />
          <rect x="112" y="23" width="8" height="6" rx="2" className={i ? 'fill-k-archive' : 'fill-k-doc'} />
          <rect x="124" y="24.5" width="22" height="3" rx="1.5" className="fill-muted" opacity="0.5" />
        </g>
      ))}
    </svg>
  ),
  privacy: (
    <svg viewBox="0 0 160 84" fill="none" aria-hidden className="sketch">
      <path d="M40 10 62 18v20c0 14-9 24-22 30C27 62 18 52 18 38V18l22-8Z" className="fill-accent-soft stroke-accent" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="m31 38 7 7 13-14" className="stroke-accent" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      {[0, 1, 2, 3].map((i) => (
        <g key={i} transform={`translate(0 ${i * 14})`}>
          <rect x="82" y="14" width="22" height="4" rx="2" className="fill-muted" opacity="0.6" />
          <rect x="108" y="14" width={[34, 26, 38, 20][i]} height="4" rx="2" className="fill-text" opacity="0.4" />
        </g>
      ))}
      <rect x="82" y="68" width="66" height="10" rx="5" className="fill-surface-2" />
      <circle cx="90" cy="73" r="2.5" className="fill-accent" />
    </svg>
  ),
  files: (
    <svg viewBox="0 0 160 84" fill="none" aria-hidden className="sketch">
      <rect x="8" y="8" width="144" height="14" rx="5" className="fill-surface-2" />
      <rect x="12" y="11" width="30" height="8" rx="3" className="fill-accent-soft" />
      <rect x="8" y="28" width="68" height="48" rx="5" className="fill-surface-2" />
      <rect x="84" y="28" width="68" height="48" rx="5" className="fill-surface-2" />
      {[0, 1, 2].map((i) => (
        <g key={i}>
          <rect x="14" y={34 + i * 13} width="8" height="8" rx="2" className="fill-k-folder" />
          <rect x="27" y={36 + i * 13} width="34" height="4" rx="2" className="fill-muted" opacity="0.6" />
          <rect x="90" y={34 + i * 13} width="8" height="8" rx="2" className={['fill-k-image', 'fill-k-doc', 'fill-k-code'][i]} />
          <rect x="103" y={36 + i * 13} width="34" height="4" rx="2" className="fill-muted" opacity="0.6" />
        </g>
      ))}
    </svg>
  )
}

const FEATURES: Feature[] = [
  { id: 'find', title: 'Find', line: 'By name, words, meaning or look.', keys: [['Ctrl', 'K']], art: Art.find, go: { label: 'Try it', run: openBar } },
  { id: 'ask', title: 'Ask', line: 'Questions answered from your files.', keys: [['Ctrl', 'K'], ['Tab']], art: Art.ask, go: { label: 'Open the bar', run: openBar } },
  { id: 'act', title: 'Act', line: 'Say what to do. You review it first.', keys: [['Ctrl', 'K'], ['Ctrl', 'Z']], art: Art.act, go: { label: 'Open the bar', run: openBar } },
  { id: 'space', title: 'Space', line: 'What fills your disk, and how to free it.', keys: [['G'], ['S']], art: Art.space, go: { label: 'Open Space', run: () => setScreen('space') } },
  { id: 'organize', title: 'Organize', line: 'Tidy a folder as a tree, before and after.', keys: [['G'], ['O']], art: Art.organize, go: { label: 'Open Organize', run: () => setScreen('organize') } },
  { id: 'privacy', title: 'Privacy', line: 'What may leave this computer.', keys: [['G'], ['P']], art: Art.privacy, go: { label: 'Open Privacy', run: () => setScreen('privacy') } },
  { id: 'files', title: 'Files', line: 'Tabs, dual pane, rename, undo.', keys: [['Ctrl', 'T'], ['F3'], ['F2']], art: Art.files, go: { label: 'Open Files', run: () => setScreen('files') } }
]

const KEYS: [string[], string][] = [
  [['Ctrl', 'K'], 'Command bar'],
  [['Space'], 'Inspector'],
  [['Ctrl', 'T'], 'New tab'],
  [['F3'], 'Dual pane'],
  [['F2'], 'Rename'],
  [['Ctrl', 'Z'], 'Undo'],
  [['Ctrl', 'H'], 'Hidden files'],
  [['Ctrl', 'B'], 'Left column'],
  [['Ctrl', '1'], 'List view'],
  [['Ctrl', '2'], 'Grid view'],
  [['Alt', '←'], 'Back'],
  [['Ctrl', ','], 'Settings']
]

const KINDS: [string, string][] = [
  ['Folders', 'bg-k-folder'],
  ['Images', 'bg-k-image'],
  ['Video', 'bg-k-video'],
  ['Audio', 'bg-k-audio'],
  ['Archives', 'bg-k-archive'],
  ['Documents', 'bg-k-doc'],
  ['Code', 'bg-k-code'],
  ['Sheets', 'bg-k-sheet']
]

const CUES: { dot: string; pulse?: boolean; title: string; text: string }[] = [
  { dot: 'bg-ok', title: 'Green dot', text: 'The index is up to date. Search sees every file.' },
  { dot: 'bg-accent', pulse: true, title: 'Pulsing brass dot', text: 'Raven is reading your files in the background. You can keep working.' },
  { dot: 'bg-danger', title: 'Red dot', text: 'Something is off, such as meaning search being unavailable. Hover it for the reason.' }
]

type Step = { target: string; title: string; text: string }
const STEPS: Step[] = [
  { target: 'nav', title: 'Four places', text: 'Files is where you browse. Space shows what uses your disk, Organize tidies folders, and Privacy shows what leaves your computer.' },
  { target: 'search', title: 'The command bar', text: 'One box to find files, ask questions about them, or give commands. Press Ctrl+K from anywhere.' },
  { target: 'sidebar', title: 'Places', text: 'Jump to your folders, drives and network shares. The bar under a drive shows how full it is.' },
  { target: 'tabs', title: 'Tabs', text: 'Keep several folders open at once. Ctrl+T opens a tab, and you can drag tabs to reorder them.' },
  { target: 'pathline', title: 'Path and views', text: 'Click any part of the path to jump back. Double-click it to type a path. Switch between list and grid on the right.' },
  { target: 'views', title: 'List or grid', text: 'List shows sizes and dates. Grid shows thumbnails of your photos and PDFs. Try Ctrl+1 and Ctrl+2.' },
  { target: 'inspector', title: 'Inspector', text: 'Preview and details for the selected item. Press Space to open or close it.' },
  { target: 'status', title: 'The status line', text: 'The dot shows the index state. The green chip is your AI mode, then how much was sent to the cloud today and how much space is free. Undo appears here after every change.' }
]

function useRect(target: string | null): DOMRect | null {
  const [rect, setRect] = useState<DOMRect | null>(null)
  useLayoutEffect(() => {
    if (!target) return setRect(null)
    const read = (): void => {
      const el = document.querySelector(`[data-tour="${target}"]`)
      setRect(el ? el.getBoundingClientRect() : null)
    }
    read()
    window.addEventListener('resize', read)
    return () => window.removeEventListener('resize', read)
  }, [target])
  return rect
}

function Tour({ onClose }: { onClose: () => void }) {
  // Steps whose target is not on the screen now (the inspector, for example) are skipped.
  const [steps] = useState(() => STEPS.filter((s) => document.querySelector(`[data-tour="${s.target}"]`)))
  const [i, setI] = useState(0)
  const step = steps[i]
  const rect = useRect(step?.target ?? null)
  const last = i === steps.length - 1

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowRight' || e.key === 'Enter') (last ? onClose() : setI((x) => x + 1))
      else if (e.key === 'ArrowLeft') setI((x) => Math.max(0, x - 1))
      else return
      e.preventDefault()
      e.stopPropagation()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [last, onClose])

  if (!step || !rect) return null
  const pad = 6
  const W = 340
  const vw = window.innerWidth
  const vh = window.innerHeight
  // The card sits below the target when there is room, else above it.
  const below = rect.bottom + 16 + 190 < vh
  const top = below ? rect.bottom + pad + 12 : Math.max(12, rect.top - pad - 12 - 190)
  const left = Math.min(Math.max(12, rect.left + rect.width / 2 - W / 2), vw - W - 12)
  return (
    <div className="fixed inset-0 z-[70]" onClick={onClose}>
      <div
        className="pointer-events-none absolute transition-all duration-500 ease-[cubic-bezier(0.3,0.8,0.2,1)]"
        style={{
          top: rect.top - pad,
          left: rect.left - pad,
          width: rect.width + pad * 2,
          height: rect.height + pad * 2,
          boxShadow: '0 0 0 9999px rgb(0 0 0 / 0.66), 0 0 0 1.5px var(--accent)'
        }}
      />
      <div key={i} className="float-panel anim-pop absolute border-l-2 border-accent p-5" style={{ top, left, width: W, borderRadius: 3 }} onClick={(e) => e.stopPropagation()}>
        <div className="mb-1 flex items-center justify-between">
          <span className="serif text-[18px]  text-accent">
            {i + 1} <span className="text-muted">of {steps.length}</span>
          </span>
          <button className="link text-muted hover:text-text" onClick={onClose} aria-label="Close tour">
            skip
          </button>
        </div>
        <div className="serif mb-1 text-[16px] leading-none">{step.title}</div>
        <div className="mb-5 mt-2 text-muted">{step.text}</div>
        <div className="flex items-center gap-3">
          <div className="flex gap-1">
            {steps.map((_, k) => (
              <span key={k} className={`h-px transition-all duration-500 ${k === i ? 'w-6 bg-accent' : 'w-3 bg-muted/50'}`} />
            ))}
          </div>
          <span className="ml-auto" />
          {i > 0 && (
            <button className="link text-muted hover:text-text" onClick={() => setI(i - 1)}>
              ← Back
            </button>
          )}
          <button className="btn-primary !py-1" onClick={() => (last ? onClose() : setI(i + 1))}>
            {last ? 'Done' : 'Next →'}
          </button>
        </div>
      </div>
    </div>
  )
}

// A short help page for each screen (PLAN4 section 8): the guide opens with the page of the screen you are on.
const SCREEN_HELP: Record<Screen, { title: string; lines: string[] }> = {
  files: {
    title: 'Files',
    lines: [
      'Browse your folders as in any file manager. Double-click opens, Space opens the inspector.',
      'Every change (move, rename, trash) goes in the journal. Ctrl+Z undoes the last one.',
      'Right-click a folder for its rules, for example "Private: no AI".'
    ]
  },
  space: {
    title: 'Space',
    lines: [
      'What uses your disk, folder by folder. Click a block to go into it.',
      'Reclaim lists what you can remove or shrink: duplicates, caches, old downloads. You see each change before it runs, and you can undo it.'
    ]
  },
  organize: {
    title: 'Organize',
    lines: ['Pick a folder. Raven proposes new folders and names for its files.', 'Nothing moves until you approve the plan. One undo puts all the files back.']
  },
  privacy: {
    title: 'Privacy',
    lines: [
      'The AI mode: Local only sends nothing. Hybrid sends redacted text for hard tasks, after you review it.',
      'The ledger keeps each payload that went to the cloud. "Try redaction" shows what Raven hides in a text.'
    ]
  },
  settings: {
    title: 'Settings',
    lines: ['Features and models: what runs on this computer. AI providers: the cloud services and their order.', 'Help has "Copy diagnostics" for a bug report, and "What Raven sends".']
  },
  results: {
    title: 'Search results',
    lines: ['Raven finds files by name, by words and by meaning. The best matches come first.', 'Type words like "pdf", "last week" or "bigger than 2MB" to filter.']
  },
  answer: {
    title: 'Answer',
    lines: ['Raven answers from your files and shows the sources. Click a source to open the file.', 'Think harder can use a cloud model, after redaction and your review.']
  },
  ask: {
    title: 'Ask',
    lines: ['Ask a question about your files. Raven reads the files that match and answers with sources.', 'Files in private folders and media files never go to a cloud model.']
  }
}

function ScreenHelp() {
  const screen = useApp((s) => s.screen)
  const h = SCREEN_HELP[screen]
  if (!h) return null
  return (
    <div className="anim-row mt-6 max-w-[760px] border-l-2 border-accent pl-4" style={stagger(3)}>
      <div className="label mb-1">This screen: {h.title}</div>
      {h.lines.map((l) => (
        <p key={l} className="text-[13px] leading-snug text-muted">
          {l}
        </p>
      ))}
    </div>
  )
}

function Guide({ onClose, onTour }: { onClose: () => void; onTour: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onClose])
  return (
    <div className="anim-fade fixed inset-0 z-[60] overflow-y-auto bg-bg">
      <div className="px-4 pb-24 pt-4">
        <div className="flex items-start gap-4">
          <div className="flex-1">
            <h1 className="serif anim-reveal text-[28px] leading-[0.9]">
              What Raven <em className="text-accent">can do</em>.
            </h1>
            <div className="anim-row mt-6 flex items-center gap-4" style={stagger(4)}>
              <button className="btn-primary !px-6 !py-2" onClick={onTour}>
                Take the tour
              </button>
              <span className="text-[12.5px] text-muted">
                Press <kbd className="kbd">?</kbd> any time to open this guide.
              </span>
            </div>
            <ScreenHelp />
          </div>
          <button className="link mt-2 text-muted hover:text-text" onClick={onClose} aria-label="Close guide">
            Close  ✕
          </button>
        </div>

        <div className="mt-4 grid grid-cols-[minmax(0,1fr)_300px] gap-4">
          <ol className="border-t border-line">
            {FEATURES.map((f, k) => (
              <li key={f.id} style={stagger(k + 5)} className="anim-row group relative grid grid-cols-[48px_150px_minmax(0,1fr)] items-center gap-x-5 border-b border-line py-6">
                <span aria-hidden className="absolute -bottom-px left-0 h-px w-full origin-left scale-x-0 bg-accent transition-transform duration-700 ease-[cubic-bezier(0.3,0.8,0.2,1)] group-hover:scale-x-100" />
                <span className="serif text-[16px]  leading-none text-muted transition-colors duration-300 group-hover:text-accent">{String(k + 1).padStart(2, '0')}</span>
                <div className="w-[150px] transition-transform duration-500 group-hover:-translate-y-0.5 group-hover:scale-[1.04]">{f.art}</div>
                <div>
                  <div className="flex items-baseline gap-4">
                    <span className="serif text-[18px] leading-none transition-transform duration-300 group-hover:translate-x-1">{f.title}</span>
                    <span className="flex items-center gap-1">
                      {f.keys.map((combo, ci) => (
                        <span key={ci} className="flex items-center gap-0.5">
                          {combo.map((kk) => (
                            <kbd key={kk} className="kbd">
                              {kk}
                            </kbd>
                          ))}
                        </span>
                      ))}
                    </span>
                  </div>
                  <p className="mb-2 mt-1.5 max-w-[480px] text-muted">{f.line}</p>
                  <button
                    className="group/b flex items-center gap-1.5 text-accent"
                    onClick={() => {
                      onClose()
                      f.go.run()
                    }}
                  >
                    <span className="link-g">{f.go.label}</span>
                    <span aria-hidden className="transition-transform duration-200 group-hover/b:translate-x-1">
                      →
                    </span>
                  </button>
                </div>
              </li>
            ))}
          </ol>

          <aside className="anim-row border-t border-line pt-5" style={stagger(8)}>
            <div className="label mb-3">Reading the status line</div>
            <div className="flex flex-col gap-3">
              {CUES.map((c) => (
                <div key={c.title} className="grid grid-cols-[14px_1fr] gap-2">
                  <span className={`mt-[7px] inline-block h-[7px] w-[7px] rounded-full ${c.dot} ${c.pulse ? 'anim-breathe' : ''}`} />
                  <span>
                    <span className="font-medium">{c.title}.</span> <span className="text-muted">{c.text}</span>
                  </span>
                </div>
              ))}
              <div className="grid grid-cols-[14px_1fr] gap-2">
                <span className="mt-[7px] inline-block h-px w-[7px] bg-accent" />
                <span className="text-muted">
                  <span className="text-accent">Hybrid</span> is your AI mode. Click it to change what may leave this computer. Nothing is sent before redaction.
                </span>
              </div>
              <div className="grid grid-cols-[14px_1fr] gap-2">
                <span className="mt-[7px] inline-block h-px w-[7px] bg-accent" />
                <span className="text-muted">
                  <span className="text-accent">Undo</span> appears after any change. One click, or Ctrl+Z, puts everything back.
                </span>
              </div>
            </div>

            <div className="label mb-3 mt-4">File colors</div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-2">
              {KINDS.map(([name, cls]) => (
                <span key={name} className="flex items-center gap-2 text-muted">
                  <span className={`inline-block h-[8px] w-[8px] ${cls}`} />
                  {name}
                </span>
              ))}
            </div>

            <div className="label mb-3 mt-4">Shortcuts</div>
            <div className="flex flex-col gap-2">
              {KEYS.map(([keys, name]) => (
                <div key={name} className="flex items-center justify-between gap-2 border-b border-line/60 pb-1.5">
                  <span className="text-muted">{name}</span>
                  <span className="flex gap-0.5">
                    {keys.map((kk) => (
                      <kbd key={kk} className="kbd">
                        {kk}
                      </kbd>
                    ))}
                  </span>
                </div>
              ))}
            </div>
          </aside>
        </div>
      </div>
    </div>
  )
}

export default function HelpGuide() {
  const [mode, setMode] = useState<'closed' | 'guide' | 'tour'>('closed')
  // A quiet ring on the button until the first time it is used.
  const [seen, setSeen] = useState(() => {
    try {
      return localStorage.getItem('raven.helpSeen') === '1'
    } catch {
      return true
    }
  })
  const open = (m: 'guide' | 'tour'): void => {
    setMode(m)
    if (!seen) {
      setSeen(true)
      try {
        localStorage.setItem('raven.helpSeen', '1')
      } catch {
        /* storage blocked */
      }
    }
  }

  // "?" opens the guide when you are not typing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const t = e.target as HTMLElement | null
      const typing = !!t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)
      if (e.key === '?' && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault()
        setMode((m) => (m === 'closed' ? 'guide' : 'closed'))
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <>
      <button
        onClick={() => (mode === 'closed' ? open('guide') : setMode('closed'))}
        title="Guide to Raven (?)"
        aria-label="Open the guide"
        className="fixed bottom-1 right-4 z-[70] grid h-6 w-6 place-content-center rounded-full border border-muted/50 text-muted transition-all duration-200 hover:scale-110 hover:border-accent hover:text-accent"
        style={!seen ? { animation: 'rv-ring 1.8s ease-out infinite' } : undefined}
      >
        <span className="serif text-[17px]  leading-none">?</span>
      </button>
      {mode === 'guide' && <Guide onClose={() => setMode('closed')} onTour={() => setMode('tour')} />}
      {mode === 'tour' && <Tour onClose={() => setMode('closed')} />}
    </>
  )
}
