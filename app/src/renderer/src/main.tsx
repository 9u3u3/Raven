import { createRoot } from 'react-dom/client'
// Mono fonts in the style of Berkeley Mono (PLAN2 P2-10.1). Bundled: the egress guard blocks web fonts.
import '@fontsource/commit-mono/400.css'
import '@fontsource/commit-mono/500.css'
import '@fontsource/commit-mono/600.css'
import '@fontsource/geist-mono/400.css'
import '@fontsource/geist-mono/500.css'
import '@fontsource/geist-mono/600.css'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import '@fontsource/jetbrains-mono/600.css'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'
import '@fontsource/ibm-plex-mono/600.css'
import './styles.css'
import App from './App'

createRoot(document.getElementById('root')!).render(<App />)

// Test hook: lets an automation script read the app state.
import { useApp } from './store'
;(window as unknown as { __raven: typeof useApp }).__raven = useApp
import { showInSpace } from './screens/SpaceScreen'
;(window as unknown as { __space: typeof showInSpace }).__space = showInSpace
import { agentCommand, ask, navigate } from './store'
;(window as unknown as { __ask: typeof ask }).__ask = ask
;(window as unknown as { __agent: typeof agentCommand }).__agent = agentCommand
;(window as unknown as { __nav: typeof navigate }).__nav = navigate
