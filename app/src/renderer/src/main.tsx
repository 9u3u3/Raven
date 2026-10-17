import { createRoot } from 'react-dom/client'
// Fonts are bundled: the egress guard blocks web fonts. Inter and Archivo as on the site, and mono fonts
// in the style of Berkeley Mono (PLAN2 P2-10.1).
import '@fontsource-variable/inter'
import '@fontsource-variable/archivo'
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

// Test hooks for automation scripts and screenshots: development builds only (PLAN4 R1).
import { installTestHooks } from './testHooks'
void (window.raven ? window.raven.config() : Promise.resolve(null)).then((c) => {
  if (!c || c.dev !== false) installTestHooks()
})
