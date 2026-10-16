// Bridge to the fuzzy-name worker. The name list comes from the sidecar index.
import { get, on } from './api'
import FuzzyWorker from '../workers/fuzzy.worker?worker'

export type FuzzyHit = { id: number; path: string; name: string; dir: boolean; positions: number[]; score: number }

const worker = new FuzzyWorker()
let seq = 0
const waiting = new Map<number, (r: { items: FuzzyHit[]; ms: number }) => void>()

worker.onmessage = (e: MessageEvent) => {
  if (e.data.type === 'result') {
    const cb = waiting.get(e.data.seq)
    waiting.delete(e.data.seq)
    cb?.({ items: e.data.items, ms: e.data.ms })
  }
}

let loadTimer: ReturnType<typeof setTimeout> | null = null

type Row = [number, string, number]
type Names = { version: number; full: true; items: Row[] } | { version: number; full: false; upsert: Row[]; remove: number[] }

// The version of the list in the worker. After the first full list, Raven asks only for the changes (PLAN3 P0-9).
let version: number | null = null

export async function loadNames(): Promise<void> {
  try {
    const r = await get<Names>('/index/names', version === null ? undefined : { since: version })
    if (r.full) worker.postMessage({ type: 'load', items: r.items })
    else if (r.upsert.length || r.remove.length) worker.postMessage({ type: 'patch', upsert: r.upsert, remove: r.remove })
    version = r.version
  } catch {
    version = null // the sidecar restarted or is not ready: the next try loads the full list
  }
}

on('names_changed', () => {
  if (loadTimer) clearTimeout(loadTimer)
  loadTimer = setTimeout(() => void loadNames(), 400)
})

export function fuzzy(q: string): Promise<{ items: FuzzyHit[]; ms: number }> {
  const id = ++seq
  return new Promise((res) => {
    waiting.set(id, res)
    worker.postMessage({ type: 'query', q, seq: id })
  })
}
