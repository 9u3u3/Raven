import { useEffect } from 'react'
import { get, initApi, on, waitReady } from './lib/api'
import { loadNames } from './lib/fuzzy'
import type { CopyJob, IndexProgress, Journal, Places } from './lib/types'
import {
  back,
  closeTab,
  copySelection,
  cycleTab,
  createItem,
  cursorTo,
  deleteSelection,
  forward,
  invertSelection,
  moveCursor,
  navigate,
  onCopyJob,
  otherPane,
  toggleDual,
  openEntry,
  openFilesTab,
  paste,
  redo,
  refreshGhost,
  refreshPrivacy,
  reload,
  reopenTab,
  restoreTabs,
  say,
  selectAll,
  selectOnly,
  selectedPaths,
  setPref,
  setScreen,
  switchTab,
  trashSelection,
  undo,
  up,
  useApp,
  visibleEntries,
  type Font,
  type Screen
} from './store'
import TopBar from './components/TopBar'
import TabBar from './components/TabBar'
import Sidebar from './components/Sidebar'
import StatusLine from './components/StatusLine'
import ProgressCard from './components/ProgressCard'
import CommandBar from './components/CommandBar'
import ConfirmDialog from './components/ConfirmDialog'
import ConflictDialog from './components/ConflictDialog'
import CloudReview from './components/CloudReview'
import BatchRename from './components/BatchRename'
import Unlock from './components/Unlock'
import Boot from './components/Boot'
import FirstRun from './screens/FirstRun'
import ConnectDialog from './components/ConnectDialog'
import LatencyOverlay from './components/LatencyOverlay'
import HelpGuide from './components/HelpGuide'
import ReviewPanel from './components/ReviewPanel'
import SpaceScreen from './screens/SpaceScreen'
import PrivacyScreen from './screens/PrivacyScreen'
import AnswerScreen from './screens/AnswerScreen'
import AskScreen from './screens/AskScreen'
import FilesScreen from './screens/FilesScreen'
import ResultsScreen from './screens/ResultsScreen'
import SettingsScreen from './screens/SettingsScreen'
import OrganizeScreen from './screens/OrganizeScreen'
import { FolderPickerHost, pickerOpen } from './components/FolderPicker'
import Welcome from './components/Welcome'

function isTyping(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null
  if (!t) return false
  return t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable
}

let lastG = 0

function onKey(e: KeyboardEvent): void {
  const s = useApp.getState()
  if (s.confirm || s.review || s.conflicts || s.batchRename || pickerOpen()) return
  const ctrl = e.ctrlKey || e.metaKey
  const key = e.key
  const typing = isTyping(e)

  if ((ctrl && key.toLowerCase() === 'k') || (!typing && key === '/' && !s.cmdOpen)) {
    e.preventDefault()
    useApp.setState({ cmdOpen: !s.cmdOpen })
    return
  }
  if (ctrl && e.shiftKey && key.toLowerCase() === 'd' && s.privacy?.dev) {
    e.preventDefault()
    useApp.setState({ overlay: !s.overlay })
    return
  }
  // Tabs: Ctrl+T new, Ctrl+W close, Ctrl+Shift+T reopen, Ctrl+Tab and Ctrl+PageDown next, Alt+1..9 go to.
  if (ctrl && !e.shiftKey && !e.altKey && key.toLowerCase() === 't') {
    e.preventDefault()
    openFilesTab()
    return
  }
  if (ctrl && e.shiftKey && key.toLowerCase() === 't') {
    e.preventDefault()
    reopenTab()
    return
  }
  if (ctrl && key.toLowerCase() === 'w') {
    e.preventDefault()
    closeTab(s.activeTab)
    return
  }
  if ((ctrl && key === 'Tab') || (ctrl && (key === 'PageDown' || key === 'PageUp'))) {
    e.preventDefault()
    cycleTab(key === 'PageUp' || (key === 'Tab' && e.shiftKey) ? -1 : 1)
    return
  }
  if (e.altKey && !ctrl && /^[1-9]$/.test(key)) {
    e.preventDefault()
    const t = key === '9' ? s.tabs[s.tabs.length - 1] : s.tabs[Number(key) - 1]
    if (t) switchTab(t.id)
    return
  }
  if (s.cmdOpen) return
  if (ctrl && key === ',') {
    e.preventDefault()
    setScreen('settings')
    return
  }
  if (ctrl && key.toLowerCase() === 'b') {
    e.preventDefault()
    setPref('leftOpen', !s.leftOpen)
    return
  }
  if (typing) return

  if (ctrl && key.toLowerCase() === 'z') {
    e.preventDefault()
    void (e.shiftKey ? redo() : undo())
    return
  }
  if (ctrl && key.toLowerCase() === 'y') {
    e.preventDefault()
    void redo()
    return
  }

  // G then F / S / O / P: go to a screen.
  if (!ctrl && !e.altKey && key.toLowerCase() === 'g') {
    lastG = Date.now()
    return
  }
  if (Date.now() - lastG < 900 && !ctrl) {
    const map: Record<string, 'files' | 'space' | 'organize' | 'privacy'> = { f: 'files', s: 'space', o: 'organize', p: 'privacy' }
    const target = map[key.toLowerCase()]
    lastG = 0
    if (target) {
      e.preventDefault()
      setScreen(target)
      return
    }
  }

  if (s.screen !== 'files') return

  const grid = s.view === 'grid'
  const cols = grid ? s.gridCols : 1
  switch (true) {
    case key === 'ArrowDown' && !e.altKey:
      e.preventDefault()
      moveCursor(1, e.shiftKey, cols)
      return
    case key === 'ArrowUp' && !e.altKey:
      e.preventDefault()
      moveCursor(-1, e.shiftKey, cols)
      return
    case key === 'ArrowRight' && grid && !e.altKey:
      e.preventDefault()
      moveCursor(1, e.shiftKey, 1)
      return
    case key === 'ArrowLeft' && grid && !e.altKey:
      e.preventDefault()
      moveCursor(-1, e.shiftKey, 1)
      return
    case key === 'Home':
      e.preventDefault()
      cursorTo('first')
      return
    case key === 'End':
      e.preventDefault()
      cursorTo('last')
      return
    case key === 'PageDown':
      e.preventDefault()
      moveCursor(15, e.shiftKey)
      return
    case key === 'PageUp':
      e.preventDefault()
      moveCursor(-15, e.shiftKey)
      return
    case key === 'Enter': {
      e.preventDefault()
      const list = visibleEntries()
      const paths = selectedPaths()
      const items = list.filter((x) => paths.includes(x.path))
      if (items.length === 1 || items.every((x) => !x.is_dir)) items.forEach((x) => void openEntry(x))
      else if (items.length) void openEntry(items[0])
      return
    }
    case key === 'Backspace' || (e.altKey && key === 'ArrowLeft'):
      e.preventDefault()
      back()
      return
    case e.altKey && key === 'ArrowRight':
      e.preventDefault()
      forward()
      return
    case e.altKey && key === 'ArrowUp':
      e.preventDefault()
      up()
      return
    case ctrl && key.toLowerCase() === 'l':
      e.preventDefault()
      useApp.setState({ editPath: true })
      return
    case key === 'F2': {
      e.preventDefault()
      const sel = selectedPaths()
      if (sel.length > 1) {
        useApp.setState({ batchRename: sel })
        return
      }
      const p = s.cursor && s.selected.has(s.cursor) ? s.cursor : sel[0]
      if (p) useApp.setState({ renaming: p })
      return
    }
    case key === 'F3':
      e.preventDefault()
      toggleDual()
      return
    case key === 'Tab' && !ctrl && !!s.dual:
      e.preventDefault()
      otherPane()
      return
    case key === 'F5' || (ctrl && key.toLowerCase() === 'r'):
      e.preventDefault()
      void reload()
      return
    case ctrl && key.toLowerCase() === 'c' && !e.shiftKey:
      e.preventDefault()
      copySelection('copy')
      return
    case ctrl && key.toLowerCase() === 'x':
      e.preventDefault()
      copySelection('cut')
      return
    case ctrl && key.toLowerCase() === 'v':
      e.preventDefault()
      void paste()
      return
    case key === 'Delete' && e.shiftKey:
      e.preventDefault()
      deleteSelection()
      return
    case key === 'Delete':
      e.preventDefault()
      void trashSelection()
      return
    case ctrl && e.shiftKey && key.toLowerCase() === 'i':
      e.preventDefault()
      invertSelection()
      return
    case ctrl && key.toLowerCase() === 'a':
      e.preventDefault()
      selectAll()
      return
    case ctrl && key.toLowerCase() === 'h':
      e.preventDefault()
      setPref('showHidden', !s.showHidden)
      say(s.showHidden ? 'Hidden files are off' : 'Hidden files are on')
      return
    case ctrl && key === '1':
      e.preventDefault()
      setPref('view', 'list')
      return
    case ctrl && key === '2':
      e.preventDefault()
      setPref('view', 'grid')
      return
    case ctrl && e.shiftKey && key.toLowerCase() === 'n':
      e.preventDefault()
      void createItem('mkdir')
      return
    case key === ' ' || (!ctrl && key.toLowerCase() === 'i'):
      e.preventDefault()
      setPref('inspector', !s.inspector)
      return
    case key === 'Escape':
      if (s.inspector) setPref('inspector', false)
      else selectOnly(null)
      return
  }
}

const SINGLE: [Screen, () => React.JSX.Element][] = [
  ['space', SpaceScreen],
  ['organize', OrganizeScreen],
  ['privacy', PrivacyScreen],
  ['settings', SettingsScreen]
]

async function boot(): Promise<void> {
  const cfg = await initApi()
  useApp.setState({ version: cfg.version })
  if (!(await waitReady())) {
    useApp.setState({ bootError: 'Raven core did not answer within 3 minutes.' })
    say('The Raven sidecar did not start. Check the terminal for errors.', 'error', {
      action: { label: 'Restart', run: () => void window.raven?.restartSidecar() }
    })
    return
  }
  // An encrypted index starts locked (secure.py): only the unlock screen shows.
  const h0 = await get<{ locked?: boolean }>('/health')
  if (h0.locked) {
    useApp.setState({ locked: true })
    return
  }
  const [places, journal, index, health] = await Promise.all([
    get<Places>('/places'),
    get<Journal>('/fs/journal'),
    get<IndexProgress>('/index/status'),
    get<{ version: string }>('/health')
  ])
  useApp.setState({ places, journal, index, ready: true, version: health.version })
  const settings = await get<Record<string, unknown>>('/settings').catch(() => ({}) as Record<string, unknown>)
  // First run (section 12.7): no folders and no first-run mark. An older install with folders skips it.
  useApp.setState({ powerUser: !!settings.power_user, firstRun: !settings.first_run_done && !index.roots.length, welcome: !settings.welcome_done })
  void refreshGhost()
  void refreshPrivacy()
  // Tabs from the last session come back. The active one lists its folder now, the others when you open them.
  const restored = await restoreTabs()
  if (!restored || restored.screen === 'files') {
    await navigate(restored?.path || places.home, { push: !restored?.history.length })
    if (!useApp.getState().listing) await navigate(places.home)
  }
  void loadNames()
}

export default function App() {
  const screen = useApp((s) => s.screen)
  const tabs = useApp((s) => s.tabs)
  const activeTab = useApp((s) => s.activeTab)
  const leftOpen = useApp((s) => s.leftOpen)
  const theme = useApp((s) => s.theme)
  const scale = useApp((s) => s.scale)
  const font = useApp((s) => s.font)
  const locked = useApp((s) => s.locked)
  const firstRun = useApp((s) => s.firstRun)
  const ready = useApp((s) => s.ready)
  const welcome = useApp((s) => s.welcome)

  useEffect(() => {
    void boot()
    window.addEventListener('keydown', onKey)
    let dirTimer: ReturnType<typeof setTimeout> | null = null
    const offs = [
      on('dir_changed', (m: { path: string }) => {
        if (m.path !== useApp.getState().path) return
        if (dirTimer) clearTimeout(dirTimer)
        dirTimer = setTimeout(() => void reload(), 150)
      }),
      on('index_progress', (m: IndexProgress) => useApp.setState({ index: m })),
      on('compress_changed', () => void refreshGhost()),
      on('ledger', () => void refreshPrivacy()),
      // Auto compression ran in the background: say what it did, with Undo (section 9.8).
      on('auto_compress', (e) => say(`${e.label}: ${e.jobs} job${e.jobs === 1 ? '' : 's'}, saved ${Math.round(e.saved / 1048576)} MB`, 'info', { undo: true })),
      on('copy_job', (m: { job: CopyJob }) => onCopyJob(m.job)),
      on('delete_progress', (m: { done: number; total: number; name: string }) => {
        const p = useApp.getState().progress
        if (p) useApp.setState({ progress: { ...p, done: m.done, total: m.total, name: m.name } })
      }),
      // A watch rule acted (rules.py): say what it did, with Undo.
      on('rule_applied', (e) => say(`${e.label}: ${e.count} file${e.count === 1 ? '' : 's'}`, 'info', { undo: true })),
      on('journal', () => {
        void get<Journal>('/fs/journal').then((journal) => useApp.setState({ journal }))
        void refreshGhost() // undo and redo can bring back or remove archives
      })
    ]
    // The network can go away and come back: read the mode line state again each minute (section 12.7).
    const privacyTimer = setInterval(() => void refreshPrivacy(), 60_000)
    return () => {
      window.removeEventListener('keydown', onKey)
      offs.forEach((f) => f())
      clearInterval(privacyTimer)
    }
  }, [])

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    root.style.setProperty('--ui-scale', String(scale))
    root.setAttribute('data-font', font)
  }, [theme, scale, font])

  if (locked) return <Unlock />
  if (!ready && !firstRun) return <Boot />
  // The welcome cards cover the first-run screen too: they come first at the first start.
  if (firstRun) return (
    <>
      <FirstRun onDone={() => useApp.setState({ firstRun: false })} />
      {welcome && <Welcome />}
    </>
  )
  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <TabBar />
      <div className="relative flex min-h-0 flex-1">
        {leftOpen && <Sidebar />}
        <main className="flex min-w-0 flex-1 flex-col">
          {screen === 'files' && (
            <div key="files" className="anim-fade flex min-h-0 flex-1 flex-col">
              <FilesScreen />
            </div>
          )}
          {screen === 'results' && (
            <div key={`results-${activeTab}`} className="anim-fade flex min-h-0 flex-1 flex-col">
              <ResultsScreen />
            </div>
          )}
          {screen === 'answer' && (
            <div key="answer" className="anim-fade flex min-h-0 flex-1 flex-col">
              <AnswerScreen />
            </div>
          )}
          {screen === 'ask' && (
            <div key={`ask-${activeTab}`} className="anim-fade flex min-h-0 flex-1 flex-col">
              <AskScreen />
            </div>
          )}
          {/* Space, Organize, Privacy and Settings have one tab each. They stay mounted while their tab
              is open, so a plan, a typed text or a scroll position is still there when you come back. */}
          {SINGLE.map(([id, Comp]) => {
            const t = tabs.find((x) => (x.id === activeTab ? screen : x.snap?.screen) === id)
            if (!t) return null
            return (
              <div key={`${id}-${t.id}`} className={screen === id && t.id === activeTab ? 'anim-fade flex min-h-0 flex-1 flex-col' : 'hidden'}>
                <Comp />
              </div>
            )
          })}
        </main>
        <ReviewPanel />
        <CommandBar />
      </div>
      <StatusLine />
      <ProgressCard />
      <ConfirmDialog />
      <ConflictDialog />
      <CloudReview />
      <FolderPickerHost />
      {welcome && <Welcome />}
      <BatchRename />
      <ConnectDialog />
      <LatencyOverlay />
      <HelpGuide />
    </div>
  )
}
