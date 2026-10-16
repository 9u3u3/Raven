import { contextBridge, ipcRenderer, webUtils } from 'electron'

export type SidecarConfig = { port: number; token: string; state: 'starting' | 'ok' | 'down'; version: string }

const api = {
  config: (): Promise<SidecarConfig> => ipcRenderer.invoke('raven:config'),
  onSidecar: (cb: (c: SidecarConfig) => void): (() => void) => {
    const h = (_: unknown, c: SidecarConfig): void => cb(c)
    ipcRenderer.on('sidecar:state', h)
    return () => ipcRenderer.off('sidecar:state', h)
  },
  openPath: (p: string): Promise<string | null> => ipcRenderer.invoke('raven:open-path', p),
  showInFolder: (p: string): Promise<void> => ipcRenderer.invoke('raven:show-in-folder', p),
  openTerminal: (dir: string): Promise<string | null> => ipcRenderer.invoke('raven:open-terminal', dir),
  pickFolder: (): Promise<string[]> => ipcRenderer.invoke('raven:pick-folder'),
  startDrag: (paths: string[]): void => ipcRenderer.send('raven:start-drag', paths),
  pathForFile: (f: File): string => webUtils.getPathForFile(f),
  restartSidecar: (): Promise<void> => ipcRenderer.invoke('raven:restart-sidecar'),
  /** Start `ollama serve` (setup screen). True when Ollama answers within 15 s. */
  startOllama: (): Promise<boolean> => ipcRenderer.invoke('raven:start-ollama'),
  printPdf: (html: string, out: string): Promise<string | null> => ipcRenderer.invoke('raven:print-pdf', html, out),
  platform: process.platform
}

contextBridge.exposeInMainWorld('raven', api)

export type RavenApi = typeof api
