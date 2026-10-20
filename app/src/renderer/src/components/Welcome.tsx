import { ArrowLeft, ArrowRight, Eye, FolderLock, HardDrive, MessageSquare, Search, ShieldCheck, Sparkles, SquareTerminal, Undo2, type LucideIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { post } from '../lib/api'
import { useApp } from '../store'
import { Mark } from './TopBar'

type Card = { icon: LucideIcon; kicker: string; title: string; lines: string[]; keys?: [string, string][] }

// The welcome cards: what Raven does, and what stays on this computer. They show at the first start and from
// Settings, Help. The user reads every card: Close and Escape work only on the last one.
const CARDS: Card[] = [
  {
    icon: Sparkles,
    kicker: 'Welcome',
    title: 'Raven is a file manager with AI that runs on this computer.',
    lines: [
      'Raven finds, organizes and shrinks your files.',
      'The index, the models and the AI run here. After the models download, Raven works with no network.',
      'These cards show what Raven does and how it keeps your data safe.'
    ]
  },
  {
    icon: Search,
    kicker: 'Find',
    title: 'Find a file by its name, its text or its meaning.',
    lines: [
      'Type a few words. Raven finds the files that match, also when the words are different.',
      'Find photos by what they show. Find audio and video by what people say.',
      'Use filter words, for example "pdf from last week" or "large videos".'
    ],
    keys: [['Ctrl+K', 'Open the command bar']]
  },
  {
    icon: MessageSquare,
    kicker: 'Ask and Act',
    title: 'Ask about your files. Tell Raven what to do.',
    lines: [
      'Ask a question. Raven reads the files that match and gives the answer with its sources.',
      'Tell Raven to move, rename or sort files. Raven makes a plan and shows each change first.',
      'Nothing changes until you approve the plan.'
    ],
    keys: [['Tab', 'In the command bar: Find, Ask, Act']]
  },
  {
    icon: HardDrive,
    kicker: 'Organize and Space',
    title: 'Clean a folder. Get disk space back.',
    lines: [
      'Organize sorts a folder into clear groups and gives generic files clear names.',
      'Space shows what fills the disk: duplicates, large files and old downloads.',
      'Raven can compress the files that you do not use, and move them to a drive or a share.'
    ]
  },
  {
    icon: ShieldCheck,
    kicker: 'Your data',
    title: 'Your files and your index stay on this computer.',
    lines: [
      'Raven never uploads your files or your index. Only a cloud request that you review can leave (see Cloud AI).',
      'In "Local only" mode, nothing leaves this computer. Set the mode in Settings, AI providers.',
      'You can encrypt the index with a passphrase. Settings, Privacy.'
    ]
  },
  {
    icon: FolderLock,
    kicker: 'You choose',
    title: 'Raven reads only the folders that you choose.',
    lines: [
      'You choose the folders to index. You can remove a folder at any time in Settings, General. Raven then deletes its data from the index.',
      'Make a folder private: no AI and no model reads it, local or cloud.',
      'You can turn off each AI feature in Settings, Features and models.'
    ]
  },
  {
    icon: Eye,
    kicker: 'Cloud AI',
    title: 'You see what goes to a cloud AI before it goes.',
    lines: [
      'Cloud AI is optional. Raven uses it only for hard tasks, and only with a provider that you add.',
      'Before each cloud request, Raven replaces names, numbers and keys with placeholders. You review the text and then send it or stop it.',
      'Photos, videos and audio never go to a cloud AI. The Privacy screen lists each request that went.'
    ]
  },
  {
    icon: Undo2,
    kicker: 'Undo',
    title: 'You can undo every change.',
    lines: [
      'Raven records each change that it makes. One undo reverts a full action, also a plan with many files.',
      'Delete moves files to the Trash. A permanent delete asks you again.'
    ],
    keys: [['Ctrl+Z', 'Undo']]
  },
  {
    icon: SquareTerminal,
    kicker: 'Terminal',
    title: 'Search from the terminal.',
    lines: [
      'The raven command gives the Ctrl+K search in a terminal, on Linux and macOS. Windows has no terminal command yet.',
      'Enter goes to the folder of the result. Ctrl+O opens the item.',
      'To install it, open Settings, Integrations, Terminal, and press Install.'
    ],
    keys: [['rv', 'Search from your shell']]
  },
  {
    icon: Sparkles,
    kicker: 'Ready',
    title: 'You are ready.',
    lines: ['Press ? on any screen for the guide and help for that screen.', 'To read these cards again, open Settings, Help, Welcome cards.'],
    keys: [
      ['Ctrl+K', 'Command bar'],
      ['?', 'Help'],
      ['Ctrl+,', 'Settings']
    ]
  }
]

export default function Welcome() {
  const [n, setN] = useState(0)
  const last = n === CARDS.length - 1
  const card = CARDS[n]
  const Icon = card.icon

  const done = (): void => {
    useApp.setState({ welcome: false })
    void post('/settings', { key: 'welcome_done', value: true }).catch(() => {})
  }
  const go = (d: number): void => setN((v) => Math.max(0, Math.min(CARDS.length - 1, v + d)))

  // The cards own the keyboard while they show, so the app shortcuts behind them do not run.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      e.stopPropagation()
      if (e.key === 'ArrowRight' || (e.key === 'Enter' && !last)) go(1)
      else if (e.key === 'ArrowLeft') go(-1)
      else if ((e.key === 'Escape' || e.key === 'Enter') && last) done()
      if (['ArrowRight', 'ArrowLeft', 'Enter', 'Escape'].includes(e.key)) e.preventDefault()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [last]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="anim-fade fixed inset-0 z-[60] flex items-center justify-center bg-black/55 p-4 backdrop-blur-md" role="dialog" aria-modal="true" aria-label="Welcome to Raven">
      <div className="float-panel flex max-h-[calc(100vh-32px)] w-[640px] max-w-full flex-col overflow-hidden">
        <div className="flex items-center gap-3 border-b border-line px-6 py-3">
          <Mark size={16} />
          <span className="label">Raven</span>
          <span className="mono ml-auto text-[11px] text-muted">
            {n + 1} / {CARDS.length}
          </span>
        </div>

        <div key={n} className="anim-slide min-h-[320px] overflow-y-auto px-8 pb-6 pt-7">
          <div className="mb-5 flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-full bg-accent-soft text-accent">
              <Icon size={19} strokeWidth={1.6} />
            </span>
            <span className="label text-accent">{card.kicker}</span>
          </div>
          <h2 className="serif mb-4 text-[24px] leading-[1.1]">{card.title}</h2>
          <ul className="space-y-2.5">
            {card.lines.map((l) => (
              <li key={l} className="flex gap-3 text-muted">
                <span className="mt-[9px] h-[5px] w-[5px] shrink-0 rounded-full bg-accent/70" />
                <span>{l}</span>
              </li>
            ))}
          </ul>
          {card.keys && (
            <div className="mt-5 flex flex-wrap gap-x-6 gap-y-2">
              {card.keys.map(([k, v]) => (
                <span key={k} className="flex items-center gap-2 text-[12.5px] text-muted">
                  <kbd className="kbd">{k}</kbd> {v}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center gap-4 border-t border-line px-6 py-3">
          <div className="flex gap-1.5" aria-hidden>
            {CARDS.map((c, k) => (
              <span key={c.kicker} className={`h-1.5 rounded-full transition-all duration-300 ${k === n ? 'w-5 bg-accent' : k < n ? 'w-1.5 bg-accent/50' : 'w-1.5 bg-line'}`} />
            ))}
          </div>
          <div className="ml-auto flex items-center gap-3">
            <button className="link flex items-center gap-1.5 text-muted hover:text-text disabled:opacity-0" disabled={n === 0} onClick={() => go(-1)}>
              <ArrowLeft size={14} /> Back
            </button>
            {last ? (
              <button className="btn-primary" autoFocus onClick={done}>
                Start using Raven
              </button>
            ) : (
              <button className="btn-primary flex items-center gap-1.5" autoFocus onClick={() => go(1)}>
                Next <ArrowRight size={14} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
