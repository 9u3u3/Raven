// Electron main process: starts and watches the sidecar and Ollama, guards egress,
// and gives the renderer the OS integration it needs (section 7).

import { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, nativeTheme, session, shell, type IpcMainEvent, type IpcMainInvokeEvent } from 'electron'
import { ChildProcess, spawn } from 'node:child_process'
import { createHash, randomBytes } from 'node:crypto'
import { appendFileSync, closeSync, existsSync, mkdirSync, mkdtempSync, openSync, readSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import net from 'node:net'
import { dirname, extname, join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

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
  version: '',
  // A packaged app is a release build: no test hooks, no screenshot hooks, no developer tools (PLAN4 R1).
  dev: !app.isPackaged
}
// The screenshot hooks (RAVEN_SHOT*) work in development builds only.
const SHOT = cfg.dev ? process.env.RAVEN_SHOT || '' : ''
const SHOT_SIZE = cfg.dev ? process.env.RAVEN_SHOT_SIZE || '' : ''

// The app log (PLAN4 B5): main.log in the Electron logs folder, 5 MB, then one old copy. It holds process
// events and errors, never file content. The sidecar reads it for "Copy diagnostics" (RAVEN_MAIN_LOG).
const MAIN_LOG = join(app.getPath('logs'), 'main.log')
function openLog(): void {
  try {
    mkdirSync(dirname(MAIN_LOG), { recursive: true, mode: 0o700 })
    if (existsSync(MAIN_LOG) && statSync(MAIN_LOG).size > 5 * 1024 * 1024) renameSync(MAIN_LOG, `${MAIN_LOG}.1`)
  } catch {
    /* no log folder: the console still works */
  }
  for (const level of ['log', 'warn', 'error'] as const) {
    const orig = console[level].bind(console)
    console[level] = (...args: unknown[]): void => {
      orig(...args)
      try {
        const text = args.map((a) => (a instanceof Error ? a.stack ?? a.message : typeof a === 'string' ? a : JSON.stringify(a))).join(' ')
        appendFileSync(MAIN_LOG, `${new Date().toISOString()} ${level} ${text.slice(0, 2000)}\n`, { mode: 0o600 })
      } catch {
        /* a full disk must not stop the app */
      }
    }
  }
  process.on('uncaughtException', (err) => console.error('[raven] uncaught', err))
  process.on('unhandledRejection', (err) => console.error('[raven] unhandled rejection', err))
  console.log(`[raven] start ${app.getVersion()} ${process.platform} ${process.arch}${cfg.dev ? ' dev' : ''}`)
}
openLog()

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
      RAVEN_MEDIA_TOKEN: cfg.mediaToken,
      RAVEN_MAIN_LOG: MAIN_LOG
    },
    stdio: ['ignore', 'inherit', 'inherit'],
    // Windows: the sidecar is a console program. Without this, a black console window opens next to Raven.
    windowsHide: true
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

/** The Ollama program: a system install, or the copy that Raven installed in its data folder (PLAN4 5.3.1). */
async function ollamaBinary(): Promise<string | null> {
  if (!cfg.port || !cfg.token) return 'ollama'
  try {
    const r = await fetch(`http://127.0.0.1:${cfg.port}/ollama?quick=true`, { headers: { Authorization: `Bearer ${cfg.token}` }, signal: AbortSignal.timeout(4000) })
    if (!r.ok) return 'ollama'
    return ((await r.json()) as { path: string | null }).path
  } catch {
    return 'ollama'
  }
}

/** Start `ollama serve` when it does not run. Its own process group, so a quit also stops its model runners. */
async function ensureOllama(): Promise<void> {
  if (ollama || (await ollamaUp())) return
  if (ollamaStarts >= 5) return
  if (!(await ollamaNeeded())) return
  const bin = await ollamaBinary()
  if (!bin) return // not installed: the setup screen offers the install
  ollamaStarts += 1
  try {
    const child = spawn(bin, ['serve'], { stdio: 'ignore', env: { ...process.env }, detached: process.platform !== 'win32', windowsHide: true })
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

/** The app page: the Vite dev server in development, else the built index.html. */
const DEV_URL = process.env.ELECTRON_RENDERER_URL ? new URL(process.env.ELECTRON_RENDERER_URL) : null
const PAGE_URL = DEV_URL ? DEV_URL.href : pathToFileURL(join(__dirname, '../renderer/index.html')).href

function isAppPage(url: string): boolean {
  try {
    const u = new URL(url)
    if (DEV_URL) return u.origin === DEV_URL.origin
    return u.protocol === 'file:' && u.pathname === new URL(PAGE_URL).pathname
  } catch {
    return false
  }
}

// Egress guard (section 10.6, PLAN3 P1-3): the renderer may reach the sidecar port only, and in development the
// Vite dev server. Other local services (Ollama, databases, other apps) stay out of reach of the renderer.
function installEgressGuard(): void {
  session.defaultSession.webRequest.onBeforeRequest((details, cb) => {
    const url = details.url
    if (/^(devtools|data|blob|chrome|chrome-extension):/.test(url)) return cb({})
    try {
      const u = new URL(url)
      if (u.protocol === 'file:') return cb({}) // the app page and its files; previews use the sidecar
      const loop = u.hostname === '127.0.0.1' || u.hostname === 'localhost' || u.hostname === '[::1]'
      const port = Number(u.port || (u.protocol === 'https:' || u.protocol === 'wss:' ? 443 : 80))
      if (loop && cfg.port && port === cfg.port) return cb({})
      if (loop && DEV_URL && port === Number(DEV_URL.port)) return cb({})
    } catch {
      /* bad URL: block */
    }
    console.warn('[raven] egress blocked:', url)
    cb({ cancel: true })
  })
}

/** PLAN3 P1-2: IPC only from the main frame of the app page. A preview frame or a page that was navigated away
 * from the app cannot open files, start a terminal or write a PDF. */
function fromApp(e: IpcMainEvent | IpcMainInvokeEvent): boolean {
  const ok = !!win && e.sender === win.webContents && e.senderFrame === e.sender.mainFrame && isAppPage(e.senderFrame?.url ?? '')
  if (!ok) console.warn('[raven] IPC refused from', e.senderFrame?.url)
  return ok
}

// Programs and launchers: opening one runs it. Raven asks first (PLAN3 P1-2).
const RUNNABLE = new Set(['.desktop', '.sh', '.bash', '.zsh', '.run', '.bin', '.appimage', '.exe', '.msi', '.bat', '.cmd', '.com', '.scr', '.ps1', '.vbs', '.jar', '.app', '.command'])

function runnable(p: string): boolean {
  if (RUNNABLE.has(extname(p).toLowerCase())) return true
  // The execute bit alone says little: files on NTFS and FAT drives often have it. A program starts with an ELF
  // header or a #! line.
  try {
    const st = statSync(p)
    if (process.platform === 'win32' || !st.isFile() || (st.mode & 0o111) === 0) return false
    const fd = openSync(p, 'r')
    const head = Buffer.alloc(4)
    try {
      readSync(fd, head, 0, 4, 0)
    } finally {
      closeSync(fd)
    }
    return head.equals(Buffer.from([0x7f, 0x45, 0x4c, 0x46])) || head.subarray(0, 2).toString() === '#!'
  } catch {
    return false
  }
}

function createWindow(): void {
  // Test hook: RAVEN_SHOT_SIZE=1280x800 sets the window size for screenshots.
  const shotSize = SHOT_SIZE.split('x').map(Number)
  win = new BrowserWindow({
    width: shotSize[0] || 1360,
    height: shotSize[1] || 860,
    minWidth: 820,
    minHeight: 520,
    title: 'Raven',
    show: false,
    autoHideMenuBar: true,
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#131217' : '#f7f6fa',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: true, // PLAN3 P1-1: the preload uses only contextBridge, ipcRenderer and webUtils
      contextIsolation: true,
      nodeIntegration: false,
      plugins: true // the built-in PDF viewer for previews
    }
  })
  // A screenshot run (RAVEN_SHOT) keeps the window hidden: it paints anyway, and nothing pops up.
  win.on('ready-to-show', () => !SHOT && win?.show())
  // No app menu: its default accelerators (Ctrl+R, Ctrl+W) clash with the Raven keyboard map.
  // F12 opens the developer tools in a development build.
  win.webContents.on('before-input-event', (_e, input) => {
    if (cfg.dev && input.type === 'keyDown' && input.key === 'F12') win?.webContents.toggleDevTools()
  })
  if (!cfg.dev) win.webContents.on('devtools-opened', () => win?.webContents.closeDevTools())
  // Links that try to open a new window go to the system browser only if they are web links.
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  // PLAN3 P1-2: the window shows the app page only. A link to a file or to the sidecar does not replace it.
  win.webContents.on('will-navigate', (e, url) => {
    if (!isAppPage(url)) e.preventDefault()
  })
  win.webContents.on('will-attach-webview', (e) => e.preventDefault())

  void win.loadURL(PAGE_URL)

  // Test hook: RAVEN_SHOT=<png path> saves a screenshot after RAVEN_SHOT_DELAY ms, then quits.
  const shot = SHOT
  if (shot) {
    win.webContents.once('did-finish-load', () => {
      // RAVEN_SHOT_SCREEN (files, space, organize, privacy, settings, ask) and RAVEN_SHOT_PATH pick the view.
      // RAVEN_SHOT_FONT and RAVEN_SHOT_THEME (light, dark) set the look. RAVEN_SHOT_SECTION scrolls Settings.
      // RAVEN_SHOT_SUMMARY=<folder> starts a think-harder summary, so the cloud review shows (PLAN4 7.7).
      const view = JSON.stringify({ screen: process.env.RAVEN_SHOT_SCREEN || '', path: process.env.RAVEN_SHOT_PATH || '', font: process.env.RAVEN_SHOT_FONT || '', theme: process.env.RAVEN_SHOT_THEME || '', section: process.env.RAVEN_SHOT_SECTION || '', summary: process.env.RAVEN_SHOT_SUMMARY || '' })
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
  ipcMain.handle('raven:config', (e) => (fromApp(e) ? { ...cfg } : null))
  ipcMain.handle('raven:open-path', async (e, p: string) => {
    if (!fromApp(e) || typeof p !== 'string') return 'Refused'
    if (runnable(p)) {
      const r = await dialog.showMessageBox(win!, {
        type: 'warning',
        buttons: ['Cancel', 'Run it'],
        defaultId: 0,
        cancelId: 0,
        message: `Run the program ${p.slice(p.lastIndexOf('/') + 1)}?`,
        detail: 'This file is a program or a launcher. Opening it runs it with your rights. Run it only if you trust where it came from.'
      })
      if (r.response !== 1) return null
    }
    const err = await shell.openPath(p)
    return err || null
  })
  ipcMain.handle('raven:show-in-folder', (e, p: string) => {
    if (fromApp(e) && typeof p === 'string') shell.showItemInFolder(p)
  })
  ipcMain.handle('raven:open-terminal', (e, dir: string) => {
    if (!fromApp(e) || typeof dir !== 'string') return 'Refused'
    try {
      if (!statSync(dir).isDirectory()) return 'Not a folder'
    } catch {
      return 'Not a folder'
    }
    const t = terminalCommand(dir)
    if (!t) return 'No terminal app found. Set the TERMINAL variable.'
    spawn(t[0], t[1], { cwd: dir, detached: true, stdio: 'ignore' }).unref()
    return null
  })
  ipcMain.handle('raven:pick-folder', async (e) => {
    if (!fromApp(e)) return []
    const r = await dialog.showOpenDialog(win!, { properties: ['openDirectory', 'multiSelections'] })
    return r.canceled ? [] : r.filePaths
  })
  ipcMain.on('raven:start-drag', (e, paths: string[]) => {
    if (!fromApp(e) || !Array.isArray(paths) || !paths.length) return
    const icon = nativeImage.createFromDataURL(
      'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAGklEQVR42mNkYGD4z0ABYBw1YNSAUQNGDQAAWj0BEdk4m9AAAAAASUVORK5CYII='
    )
    e.sender.startDrag({ file: paths[0], files: paths, icon })
  })
  // MD and HTML to PDF (section 9.9): Chromium prints the HTML that pandoc made. No LaTeX needed.
  ipcMain.handle('raven:print-pdf', async (e, html: string, out: string) => {
    if (!fromApp(e) || typeof html !== 'string' || typeof out !== 'string') return 'Refused'
    if (extname(out).toLowerCase() !== '.pdf') return 'The output must be a .pdf file'
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
  ipcMain.handle('raven:start-ollama', async (e) => {
    if (!fromApp(e)) return false
    ollamaStarts = 0
    await ensureOllama()
    for (let i = 0; i < 30; i++) {
      if (await ollamaUp()) return true
      await new Promise((r) => setTimeout(r, 500))
    }
    return false
  })
  ipcMain.handle('raven:restart-sidecar', async (e) => {
    if (!fromApp(e)) return
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
