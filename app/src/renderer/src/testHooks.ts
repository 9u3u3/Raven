// Test hooks for automation scripts (testdata/*.py) and screenshots (RAVEN_SHOT in the main process).
// main.tsx installs them in a development build only (PLAN4 R1).
import { showInSpace } from './screens/SpaceScreen'
import { agentCommand, ask, navigate, setScreen, summarize, useApp, type Font } from './store'

export function installTestHooks(): void {
  const w = window as unknown as Record<string, unknown>
  w.__raven = useApp
  w.__space = showInSpace
  w.__ask = ask
  w.__agent = agentCommand
  w.__nav = navigate
  // Screenshots: open a screen or a folder, set the font and the theme.
  w.__ravenShot = ({ screen, path, font, theme, section, summary }: { screen: string; path: string; font?: string; theme?: string; section?: string; summary?: string }) => {
    if (path) void navigate(path)
    if (screen === 'graph') {
      setScreen('organize')
      useApp.setState({ organizeView: 'map' })
    } else if (screen) setScreen(screen as Parameters<typeof setScreen>[0])
    if (font) useApp.setState({ font: font as Font })
    if (theme === 'light' || theme === 'dark') {
      // A hidden window does not run transitions to their end, so colors would stay at the old theme.
      const st = document.createElement('style')
      st.textContent = '*, *::before, *::after { transition: none !important; }'
      document.head.appendChild(st)
      useApp.setState({ theme })
    }
    // A Settings page (RAVEN_SHOT_SECTION).
    if (summary) setTimeout(() => void summarize(summary, true), 1500)
    if (section) useApp.setState({ settingsTab: section })
  }
}
