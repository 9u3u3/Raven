import { FileText, Folder, FolderTree, MessageSquareText, PieChart, Plus, Search, Settings, ShieldCheck, X } from 'lucide-react'
import { useState } from 'react'
import { closeTab, moveTab, openFilesTab, switchTab, tabTitle, useApp, type Screen, type TabSnap } from '../store'

// Tabs (one workspace each): the screen, the folder and its history, the selection, Find results,
// the Ask document and the review. Text tabs on a 1 px line, the active one underlined in the accent.

const ICON: Record<Screen, typeof Folder> = {
  files: Folder,
  results: Search,
  ask: MessageSquareText,
  answer: FileText,
  space: PieChart,
  organize: FolderTree,
  privacy: ShieldCheck,
  settings: Settings
}

const IN_NAV = new Set<Screen>(['space', 'organize', 'privacy', 'settings'])

export default function TabBar() {
  const tabs = useApp((s) => s.tabs)
  const active = useApp((s) => s.activeTab)
  // The active tab's title comes from the live values.
  const live = useApp((s) => tabTitle(s))
  const liveScreen = useApp((s) => s.screen)
  const liveBusy = useApp((s) => s.asks.some((a) => a.status === 'retrieving' || a.status === 'streaming') || s.answer?.status === 'running')
  const [drag, setDrag] = useState<number | null>(null)

  // Space, Organize, Privacy and Settings already have a place in the top bar, so they get no tab here.
  // With a single folder tab open there is nothing to switch between, so the row stays out of the way.
  const hasTab = (t: (typeof tabs)[number]): boolean => {
    const sc = t.id === active ? liveScreen : (t.snap as TabSnap | null)!.screen
    return !IN_NAV.has(sc)
  }
  if (tabs.filter(hasTab).length <= 1) return null

  return (
    <div
      data-tour="tabs"
      className="flex h-9 shrink-0 items-stretch gap-1 overflow-x-auto border-b border-line px-5"
      onDoubleClick={(e) => e.target === e.currentTarget && openFilesTab()}
    >
      {tabs.map((t, i) => {
        if (!hasTab(t)) return null
        const isActive = t.id === active
        const snap = t.snap as TabSnap | null
        const screen = isActive ? liveScreen : snap!.screen
        const title = isActive ? live : tabTitle(snap!)
        const busy = isActive ? liveBusy : !!snap && (snap.asks.some((a) => a.status === 'retrieving' || a.status === 'streaming') || snap.answer?.status === 'running')
        const Icon = ICON[screen]
        return (
          <div
            key={t.id}
            role="tab"
            aria-selected={isActive}
            draggable
            onDragStart={() => setDrag(t.id)}
            onDragOver={(e) => e.preventDefault()}
            onDrop={() => {
              if (drag != null && drag !== t.id) moveTab(drag, i)
              setDrag(null)
            }}
            onMouseDown={(e) => {
              if (e.button === 0) switchTab(t.id)
            }}
            onAuxClick={(e) => {
              if (e.button === 1) {
                e.preventDefault()
                closeTab(t.id)
              }
            }}
            title={`${title}${i < 9 ? `  (Alt+${i + 1})` : ''}`}
            className={`group relative flex h-full max-w-[220px] shrink-0 cursor-default items-center gap-2 pl-3 pr-1.5 transition-colors duration-200 ${
              isActive ? 'text-text' : 'text-muted hover:text-text'
            }`}
          >
            <span className={`absolute inset-x-2 bottom-0 h-[1.5px] origin-left bg-accent transition-transform duration-300 ease-[cubic-bezier(0.3,0.9,0.3,1)] ${isActive ? 'scale-x-100' : 'scale-x-0 group-hover:scale-x-100 group-hover:bg-line'}`} />
            <Icon size={13} className={`shrink-0 ${busy ? 'anim-breathe text-accent' : isActive ? 'text-accent' : ''}`} />
            <span className="truncate text-[13px]">{title}</span>
            <button
              aria-label={`Close ${title}`}
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation()
                closeTab(t.id)
              }}
              className={`ml-0.5 shrink-0 rounded-sm p-0.5 transition-colors hover:bg-hover ${isActive ? 'opacity-70' : 'opacity-0 group-hover:opacity-70'}`}
            >
              <X size={12} />
            </button>
          </div>
        )
      })}
      <button aria-label="New tab" title="New tab (Ctrl+T)" onClick={() => openFilesTab()} className="icon-btn my-auto shrink-0">
        <Plus size={14} />
      </button>
    </div>
  )
}
