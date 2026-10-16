// Fuzzy file-name search in a Web Worker (section 5.1: < 30 ms per key press).
import { Fzf } from 'fzf'

type Item = { id: number; path: string; name: string; dir: boolean }

let items: Item[] = []
let fzf: Fzf<Item[]> | null = null

self.onmessage = (e: MessageEvent) => {
  const msg = e.data
  if (msg.type === 'load') {
    items = (msg.items as [number, string, number][]).map(([id, path, dir]) => ({
      id,
      path,
      name: path.slice(path.lastIndexOf('/') + 1),
      dir: !!dir
    }))
    fzf = new Fzf(items, { selector: (i) => i.name, limit: 40, casing: 'case-insensitive' })
    ;(self as unknown as Worker).postMessage({ type: 'loaded', count: items.length })
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
