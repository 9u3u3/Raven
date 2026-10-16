// Client for the raven-core sidecar. HTTP for requests, one WebSocket for events.

import type { RavenApi, SidecarConfig } from '../../../preload'

declare global {
  interface Window {
    raven?: RavenApi
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public data: any = null
  ) {
    super(message)
  }
}

let base = ''
let token = ''
// The media-only token for URLs (PLAN3 P0-4). A plain browser has none, and uses the main token.
let mediaToken = ''

type Handler = (data: any) => void
const handlers = new Map<string, Set<Handler>>()

export function on(type: string, h: Handler): () => void {
  if (!handlers.has(type)) handlers.set(type, new Set())
  handlers.get(type)!.add(h)
  return () => handlers.get(type)!.delete(h)
}

function emit(type: string, data: unknown): void {
  handlers.get(type)?.forEach((h) => h(data))
}

/** Read the sidecar port and token from the preload bridge, or from ?port=&token= in a plain browser. */
export async function initApi(): Promise<SidecarConfig> {
  let cfg: SidecarConfig
  if (window.raven) {
    cfg = await window.raven.config()
    window.raven.onSidecar((c) => {
      apply(c)
      emit('sidecar', c)
    })
  } else {
    const q = new URLSearchParams(location.search)
    cfg = { port: Number(q.get('port')), token: q.get('token') || '', state: 'ok', version: '' }
  }
  apply(cfg)
  return cfg
}

function apply(c: SidecarConfig): void {
  if (c.port) {
    const changed = base !== `http://127.0.0.1:${c.port}`
    base = `http://127.0.0.1:${c.port}`
    token = c.token
    mediaToken = c.mediaToken || c.token
    if (c.state === 'ok' && (changed || !ws)) connectWs()
  }
}

export async function waitReady(timeoutMs = 180000): Promise<boolean> {
  const end = Date.now() + timeoutMs
  while (Date.now() < end) {
    try {
      await get('/health')
      return true
    } catch {
      await new Promise((r) => setTimeout(r, 100))
    }
  }
  return false
}

function qs(params?: Record<string, string | number | boolean | undefined>): string {
  if (!params) return ''
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined) u.set(k, String(v))
  const s = u.toString()
  return s ? `?${s}` : ''
}

async function handle<T>(r: Response): Promise<T> {
  if (r.ok) return (await r.json()) as T
  let msg = `${r.status} ${r.statusText}`
  let data: any = null
  try {
    data = await r.json()
    msg = data.error || data.detail || msg
  } catch {
    /* body is not JSON */
  }
  throw new ApiError(msg, r.status, data)
}

export async function get<T = any>(
  path: string,
  params?: Record<string, string | number | boolean | undefined>,
  signal?: AbortSignal
): Promise<T> {
  const r = await fetch(base + path + qs(params), { headers: { Authorization: `Bearer ${token}` }, signal })
  return handle<T>(r)
}

export async function post<T = any>(path: string, body: unknown = {}): Promise<T> {
  const r = await fetch(base + path, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
  return handle<T>(r)
}

export async function del<T = any>(path: string): Promise<T> {
  const r = await fetch(base + path, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } })
  return handle<T>(r)
}

export function rawUrl(path: string): string {
  return `${base}/fs/raw${qs({ path, token: mediaToken })}`
}

export function thumbUrl(path: string, size = 256, mtime = 0): string {
  return `${base}/fs/thumb${qs({ path, size, token: mediaToken, v: mtime })}`
}

/** A stored keyframe or PDF page image (media/visual.py). */
export function visualThumbUrl(p: string): string {
  return `${base}/visual/thumb${qs({ p, token: mediaToken })}`
}

let ws: WebSocket | null = null
let wsTimer: ReturnType<typeof setTimeout> | null = null

function connectWs(): void {
  if (ws) {
    ws.onclose = null
    ws.close()
  }
  const sock = new WebSocket(`${base.replace('http', 'ws')}/ws${qs({ token })}`)
  ws = sock
  sock.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data)
      emit(msg.type, msg)
    } catch {
      /* ignore bad frames */
    }
  }
  sock.onopen = () => emit('ws_open', null)
  sock.onclose = () => {
    if (wsTimer) clearTimeout(wsTimer)
    wsTimer = setTimeout(connectWs, 1000)
  }
}

export function send(msg: unknown): void {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg))
  else on('ws_open', () => ws?.send(JSON.stringify(msg)))
}

/** POST and read an NDJSON stream. Calls onEvent for each line. */
export async function stream(path: string, body: unknown, onEvent: (e: any) => void, signal?: AbortSignal): Promise<void> {
  const r = await fetch(base + path, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal
  })
  if (!r.ok || !r.body) return void (await handle(r))
  const reader = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let i: number
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim()
      buf = buf.slice(i + 1)
      if (line) onEvent(JSON.parse(line))
    }
  }
  if (buf.trim()) onEvent(JSON.parse(buf))
}
