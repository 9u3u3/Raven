// Electron main process: starts and watches the sidecar and Ollama, guards egress,
// and gives the renderer the OS integration it needs (section 7).

import { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, nativeTheme, session, shell } from 'electron'
import { ChildProcess, spawn } from 'node:child_process'
import { createHash, randomBytes } from 'node:crypto'
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import net from 'node:net'
import { join, resolve } from 'node:path'

const OLLAMA_URL = 'http://127.0.0.1:11434'

type SidecarState = 'starting' | 'ok' | 'down'

let win: BrowserWindow | null = null
let sidecar: ChildProcess | null = null
let ollama: ChildProcess | null = null
let quitting = false
let restarts = 0
// mediaToken: for URLs in <img>, <video> and the PDF frame. It opens the media routes only, so a URL that
// leaks (a log, a copied link) cannot change files (PLAN3 P0-4). The main token stays in headers.
const cfg = {
  port: 0,
  token: randomBytes(24).toString('hex'),
  mediaToken: randomBytes(24).toString('hex'),
  state: 'starting' as SidecarState,
  version: ''
}

function coreDir(): string {
  if (process.env.RAVEN_CORE_DIR) return resolve(process.env.RAVEN_CORE_DIR)
  // Dev and preview: <repo>/app is the app path, the sidecar is <repo>/core.
  return resolve(app.getAppPath(), '..', 'core')
}

function freePort(): Promise<number> {
  return new Promise((res, rej) => {
    const s = net.createServer()
    s.unref()
    s.on('error', rej)
    s.listen(0, '127.0.0.1', () => {
      const addr = s.address()
      const port = typeof addr === 'object' && addr ? addr.port : 0
      s.close(() => res(port))
    })
  })
}

function broadcast(channel: string, payload: unknown): void {
  for (const w of BrowserWindow.getAllWindows()) w.webContents.send(channel, payload)
}

function setState(state: SidecarState): void {
  cfg.state = state
  broadcast('sidecar:state', { ...cfg })
}

async function waitHealth(timeoutMs: number, child?: ChildProcess): Promise<boolean> {
  const end = Date.now() + timeoutMs
  while (Date.now() < end) {
    // This process ended: the exit handler restarts the sidecar with a wait of its own.
    if (child && child.exitCode !== null) return false
    try {
      const r = await fetch(`http://127.0.0.1:${cfg.port}/health`, {
        headers: { Authorization: `Bearer ${cfg.token}` }
      })
      if (r.ok) {
        const j = (await r.json()) as { version: string }
        cfg.version = j.version
        return true
      }
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 80))
  }
  return false
}

/** The sidecar command: the frozen binary in a package, the venv Python in development. */
// The token goes in RAVEN_TOKEN (startSidecar), not in argv: every user can read argv with ps (PLAN3 P0-1).
function sidecarCommand(port: number): { cmd: string; argv: string[]; cwd: string } {
  const flags = ['--port', String(port)]
  if (app.isPackaged) {
    // electron-builder copies core/dist/raven-core to <resources>/raven-core (PyInstaller, its own Python).
    const dir = join(process.resourcesPath, 'raven-core')
    return { cmd: join(dir, process.platform === 'win32' ? 'raven-core.exe' : 'raven-core'), argv: flags, cwd: dir }
  }
  const dir = coreDir()
  const py = process.platform === 'win32' ? join(dir, '.venv', 'Scripts', 'python.exe') : join(dir, '.venv', 'bin', 'python')
  const args = ['-m', 'raven_core', ...flags]
  return existsSync(py) ? { cmd: py, argv: args, cwd: dir } : { cmd: 'uv', argv: ['run', '--project', dir, 'python', ...args], cwd: dir }
}

async function startSidecar(): Promise<void> {
  cfg.port = await freePort()
  const { cmd, argv, cwd } = sidecarCommand(cfg.port)
  setState('starting')
  sidecar = spawn(cmd, argv, {
    cwd,
    // Fewer malloc arenas: about 15 threads otherwise keep freed memory each (gigabytes on a large index).
    // RAVEN_PARENT_PID: the sidecar exits by itself when this process is gone (a kill, a crash, Ctrl+C).
    env: {
      ...process.env,
      PYTHONUNBUFFERED: '1',
      MALLOC_ARENA_MAX: process.env.MALLOC_ARENA_MAX ?? '2',
      RAVEN_PARENT_PID: String(process.pid),
      RAVEN_TOKEN: cfg.token,
      RAVEN_MEDIA_TOKEN: cfg.mediaToken
    },
    stdio: ['ignore', 'inherit', 'inherit']
  })
  sidecar.on('exit', (code) => {
    sidecar = null
    if (quitting) return
    setState('down')
    // Restart a crashed sidecar a few times, with a short back-off.
    if (restarts < 5) {
      restarts += 1
      console.error(`[raven] sidecar exited (${code}), restart ${restarts}`)
      setTimeout(() => void startSidecar(), 500 * restarts)
    }
  })
  // A large index can need a minute at start. A crash ends the wait early.
  const child = sidecar
  if (await waitHealth(180000, child)) {
    setState('ok')
    // A sidecar that runs well for a minute starts the restart count again.
    setTimeout(() => {
      if (sidecar === child) restarts = 0
    }, 60_000)
  } else if (sidecar === child) setState('down')
}

let ollamaStarts = 0

async function ollamaUp(): Promise<boolean> {
  try {
    const r = await fetch(`${OLLAMA_URL}/api/version`, { signal: AbortSignal.timeout(3000) })
    return r.ok
  } catch {
    return false
  }
}

/** Lite mode (PLAN2 P2-6) with the local model off and static embeddings: Raven does not start Ollama. */
async function ollamaNeeded(): Promise<boolean> {
  if (!cfg.port || !cfg.token) return true
  try {
    const r = await fetch(`http://127.0.0.1:${cfg.port}/lite`, { headers: { Authorization: `Bearer ${cfg.token}` }, signal: AbortSignal.timeout(3000) })
    if (!r.ok) return true
    const l = (await r.json()) as { features: Record<string, { on: boolean }>; embed_model: string }
    return l.features.llm?.on !== false || !l.embed_model.startsWith('potion')
  } catch {
    return true
  }
}

/** Start `ollama serve` when it does not run. Its own process group, so a quit also stops its model runners. */
async function ensureOllama(): Promise<void> {
  if (ollama || (await ollamaUp())) return
  if (ollamaStarts >= 5) return
  if (!(await ollamaNeeded())) return
  ollamaStarts += 1
  try {
    const child = spawn('ollama', ['serve'], { stdio: 'ignore', env: { ...process.env }, detached: process.platform !== 'win32' })
    ollama = child
    child.on('error', () => ollama === child && (ollama = null))
    child.on('exit', (code) => {
      if (ollama === child) ollama = null
      if (!quitting) console.error(`[raven] ollama serve exited (${code})`)
    })
  } catch {
    ollama = null
  }
}

/** Stop the Ollama that Raven started, with its runners. An Ollama that ran before Raven keeps running. */
function stopOllama(): void {
  const child = ollama
  if (!child?.pid) return
  try {
    if (process.platform === 'win32') child.kill()
    else process.kill(-child.pid, 'SIGTERM')
  } catch {
    /* already gone */
  }
}

// Egress guard (section 10.6): the renderer may reach the loopback address only.
function installEgressGuard(): void {
  session.defaultSession.webRequest.onBeforeRequest((details, cb) => {
    const url = details.url
    if (/^(devtools|file|data|blob|chrome|chrome-extension):/.test(url)) return cb({})
    try {
      const u = new URL(url)
      if (u.hostname === '127.0.0.1' || u.hostname === 'localhost' || u.hostname === '[::1]') return cb({})
    } catch {
      /* bad URL: block */
    }
    console.warn('[raven] egress blocked:', url)
    cb({ cancel: true })
  })
}

function createWindow(): void {
  // Test hook: RAVEN_SHOT_SIZE=1280x800 sets the window size for screenshots.
  const shotSize = (process.env.RAVEN_SHOT_SIZE || '').split('x').map(Number)
  win = new BrowserWindow({
    width: shotSize[0] || 1360,
    height: shotSize[1] || 860,
    minWidth: 820,
    minHeight: 520,
    title: 'Raven',
    show: false,
    autoHideMenuBar: true,
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#0F100F' : '#FAFAF7',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false,
      plugins: true // the built-in PDF viewer for previews
    }
  })
  // A screenshot run (RAVEN_SHOT) keeps the window hidden: it paints anyway, and nothing pops up.
  win.on('ready-to-show', () => !process.env.RAVEN_SHOT && win?.show())
  // No app menu: its default accelerators (Ctrl+R, Ctrl+W) clash with the Raven keyboard map.
  // F12 still opens the developer tools.
  win.webContents.on('before-input-event', (_e, input) => {
    if (input.type === 'keyDown' && input.key === 'F12') win?.webContents.toggleDevTools()
  })
  // Links that try to open a new window go to the system browser only if they are web links.
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  win.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith('http://localhost') && !url.startsWith('http://127.0.0.1') && !url.startsWith('file:')) e.preventDefault()
  })

  if (process.env.ELECTRON_RENDERER_URL) void win.loadURL(process.env.ELECTRON_RENDERER_URL)
  else void win.loadFile(join(__dirname, '../renderer/index.html'))

  // Test hook: RAVEN_SHOT=<png path> saves a screenshot after RAVEN_SHOT_DELAY ms, then quits.
  const shot = process.env.RAVEN_SHOT
  if (shot) {
    win.webContents.once('did-finish-load', () => {
      // RAVEN_SHOT_SCREEN (files, space, organize, privacy, settings, ask) and RAVEN_SHOT_PATH pick the view.
      const view = JSON.stringify({ screen: process.env.RAVEN_SHOT_SCREEN || '', path: process.env.RAVEN_SHOT_PATH || '', font: process.env.RAVEN_SHOT_FONT || '' })
      setTimeout(() => void win?.webContents.executeJavaScript(`window.__ravenShot && window.__ravenShot(${view})`), 2500)
      setTimeout(async () => {
        const img = await win!.webContents.capturePage()
        writeFileSync(shot, img.toPNG())
        app.quit()
      }, Number(process.env.RAVEN_SHOT_DELAY || 4000))
    })
  }
}

function terminalCommand(dir: string): [string, string[]] | null {
  if (process.platform === 'win32') return ['cmd.exe', ['/c', 'start', 'cmd.exe', '/k', `cd /d "${dir}"`]]
  if (process.platform === 'darwin') return ['open', ['-a', 'Terminal', dir]]
  const candidates: [string, string[]][] = [
    [process.env.TERMINAL || '', []],
    ['kitty', ['--directory', dir]],
    ['alacritty', ['--working-directory', dir]],
    ['wezterm', ['start', '--cwd', dir]],
    ['foot', ['--working-directory', dir]],
    ['gnome-terminal', [`--working-directory=${dir}`]],
    ['konsole', ['--workdir', dir]],
    ['xfce4-terminal', [`--working-directory=${dir}`]],
    ['x-terminal-emulator', []]
  ]
  const paths = (process.env.PATH || '').split(':')
  for (const [bin, args] of candidates) {
    if (bin && paths.some((p) => existsSync(join(p, bin)))) return [bin, args]
  }
  return null
}

function registerIpc(): void {
  ipcMain.handle('raven:config', () => ({ ...cfg }))
  ipcMain.handle('raven:open-path', async (_e, p: string) => {
    const err = await shell.openPath(p)
    return err || null
  })
  ipcMain.handle('raven:show-in-folder', (_e, p: string) => shell.showItemInFolder(p))
  ipcMain.handle('raven:open-terminal', (_e, dir: string) => {
    const t = terminalCommand(dir)
    if (!t) return 'No terminal app found. Set the TERMINAL variable.'
    spawn(t[0], t[1], { cwd: dir, detached: true, stdio: 'ignore' }).unref()
    return null
  })
  ipcMain.handle('raven:pick-folder', async () => {
    const r = await dialog.showOpenDialog(win!, { properties: ['openDirectory', 'multiSelections'] })
    return r.canceled ? [] : r.filePaths
  })
  ipcMain.on('raven:start-drag', (e, paths: string[]) => {
    if (!paths.length) return
    const icon = nativeImage.createFromDataURL(
      'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAGklEQVR42mNkYGD4z0ABYBw1YNSAUQNGDQAAWj0BEdk4m9AAAAAASUVORK5CYII='
    )
    e.sender.startDrag({ file: paths[0], files: paths, icon })
  })
  // MD and HTML to PDF (section 9.9): Chromium prints the HTML that pandoc made. No LaTeX needed.
  ipcMain.handle('raven:print-pdf', async (_e, html: string, out: string) => {
    if (existsSync(out)) return 'The output file exists already'
    const dir = mkdtempSync(join(tmpdir(), 'raven-print-'))
    const page = join(dir, 'page.html')
    writeFileSync(page, html)
    const w = new BrowserWindow({ show: false, webPreferences: { javascript: false, sandbox: true } })
    try {
      await w.loadFile(page)
      const pdf = await w.webContents.printToPDF({ pageSize: 'A4', printBackground: true, margins: { top: 0.6, bottom: 0.6, left: 0.6, right: 0.6 } })
      writeFileSync(out, pdf)
      return null
    } catch (err) {
      return String(err)
    } finally {
      w.destroy()
      rmSync(dir, { recursive: true, force: true })
    }
  })
  ipcMain.handle('raven:start-ollama', async () => {
    ollamaStarts = 0
    await ensureOllama()
    for (let i = 0; i < 30; i++) {
      if (await ollamaUp()) return true
      await new Promise((r) => setTimeout(r, 500))
    }
    return false
  })
  ipcMain.handle('raven:restart-sidecar', async () => {
    restarts = 0
    sidecar?.kill()
    if (!sidecar) await startSidecar()
  })
}

// One app for each data folder (PLAN3 P0-6): a second start focuses the first window. Electron ties the lock
// to the userData folder, so a run with its own RAVEN_DATA_DIR (tests, screenshots, demos) gets its own
// userData folder and runs next to the normal app.
if (process.env.RAVEN_DATA_DIR) {
  const key = createHash('sha256').update(resolve(process.env.RAVEN_DATA_DIR)).digest('hex').slice(0, 12)
  app.setPath('userData', join(app.getPath('userData'), 'instances', key))
}
if (!app.requestSingleInstanceLock()) {
  app.quit()
  process.exit(0)
}
app.on('second-instance', () => {
  if (!win) return
  if (win.isMinimized()) win.restore()
  win.show()
  win.focus()
})

app.whenReady().then(async () => {
  Menu.setApplicationMenu(null)
  installEgressGuard()
  registerIpc()
  // Sidecar and Ollama start in parallel with the window (section 5.1).
  void ensureOllama()
  // Ollama can stop later (a crash, or a terminal that closed). Raven starts it again.
  setInterval(() => void ensureOllama(), 15000)
  const started = startSidecar()
  createWindow()
  await started
})

app.on('before-quit', () => {
  quitting = true
  sidecar?.kill()
  stopOllama()
})

// Ctrl+C in the terminal and a plain kill skip before-quit: stop the children first, then quit.
for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP'] as const) {
  process.on(sig, () => {
    quitting = true
    sidecar?.kill()
    stopOllama()
    app.quit()
    setTimeout(() => process.exit(0), 1500).unref()
  })
}

app.on('window-all-closed', () => app.quit())
