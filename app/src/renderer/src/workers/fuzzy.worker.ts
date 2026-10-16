// Fuzzy file-name search in a Web Worker (section 5.1: < 30 ms per key press).
import { Fzf } from 'fzf'

type Item = { id: number; path: string; name: string; dir: boolean }

const byId = new Map<number, Item>()
let fzf: Fzf<Item[]> | null = null

function toItem([id, path, dir]: [number, string, number]): Item {
  return { id, path, name: path.slice(path.lastIndexOf('/') + 1), dir: !!dir }
}

function rebuild(): void {
  fzf = new Fzf([...byId.values()], { selector: (i) => i.name, limit: 40, casing: 'case-insensitive' })
  ;(self as unknown as Worker).postMessage({ type: 'loaded', count: byId.size })
}

self.onmessage = (e: MessageEvent) => {
  const msg = e.data
  if (msg.type === 'load') {
    byId.clear()
    for (const row of msg.items as [number, string, number][]) byId.set(row[0], toItem(row))
    rebuild()
    return
  }
  // The changes since the last list (PLAN3 P0-9): the sidecar no longer sends the whole list after each change.
  if (msg.type === 'patch') {
    for (const id of msg.remove as number[]) byId.delete(id)
    for (const row of msg.upsert as [number, string, number][]) byId.set(row[0], toItem(row))
    rebuild()
    return
  }
  if (msg.type === 'query') {
    const t0 = performance.now()
    const q = String(msg.q || '').trim()
    const res = q && fzf ? fzf.find(q) : []
    ;(self as unknown as Worker).postMessage({
      type: 'result',
      seq: msg.seq,
      q,
      ms: performance.now() - t0,
      items: res.map((r) => ({ id: r.item.id, path: r.item.path, name: r.item.name, dir: r.item.dir, positions: [...r.positions], score: r.score }))
    })
  }
}
