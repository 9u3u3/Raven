// File graph (PLAN2 P2-7): files are dots, links are lines, as in the Obsidian graph.
// Hover: a file's links stand out. Click: details. Double-click: open. Shift-drag: select, then move into a folder.

import Graph from 'graphology'
import forceAtlas2 from 'graphology-layout-forceatlas2'
import FA2Layout from 'graphology-layout-forceatlas2/worker'
import { useEffect, useMemo, useRef, useState } from 'react'
import Sigma from 'sigma'
import { get, post } from '../lib/api'
import { basename, tildify } from '../lib/format'
import type { Journal } from '../lib/types'
import { openReview, reveal, say, useApp } from '../store'
import { pickFolder } from './FolderPicker'

type GNode = { id: number | string; path: string; name: string; group: string; size: number | null; degree: number; folder: string; files?: number }
type GEdge = { src: number | string; dst: number | string; type: string; w: number }
type GData = { level: 'file' | 'folder'; root?: string; nodes: GNode[]; edges: GEdge[]; center?: number | null; building?: boolean; edges_total?: number; cut?: boolean; files?: number; indexed?: number }

const TYPES: [string, string][] = [
  ['folder', 'folders'],
  ['ref', 'links'],
  ['mention', 'mentions'],
  ['similar', 'similar'],
  ['same', 'same content']
]
const css = (v: string): string => getComputedStyle(document.documentElement).getPropertyValue(v).trim() || '#888'
const GROUP_VAR: Record<string, string> = {
  image: '--k-image', video: '--k-video', audio: '--k-audio', archive: '--k-archive', sheet: '--k-sheet', doc: '--k-doc',
  log: '--k-doc', code: '--k-code', folder: '--k-folder', data: '--k-sheet', app: '--k-archive'
}
const EDGE_VAR: Record<string, string> = { ref: '--accent', mention: '--muted', similar: '--line', same: '--ok', folder: '--line' }

/**
 * `path`: the graph of one folder that the user named (/graph/path). Raven reads only that folder.
 * `root` and `local` use the graph of the whole index (the Inspector uses `local`).
 */
export default function FileGraph({ root, local, path, onPath, start, height = '100%', compact = false }: { root?: string; local?: string; path?: string; onPath?: (p: string) => void; start?: string[]; height?: string; compact?: boolean }) {
  const box = useRef<HTMLDivElement>(null)
  const sigmaRef = useRef<Sigma | null>(null)
  const theme = useApp((s) => s.theme)
  const home = useApp((s) => s.places?.home ?? '')
  const [types, setTypes] = useState<Set<string>>(new Set(start ?? ['ref', 'mention', 'same', 'similar']))
  const [data, setData] = useState<GData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [focus, setFocus] = useState<string | null>(root ?? null)
  const [hover, setHover] = useState<string | null>(null)
  const [picked, setPicked] = useState<GNode | null>(null)
  const [lasso, setLasso] = useState<Set<string>>(new Set())
  const [rect, setRect] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null)
  const typeKey = [...types].sort().join(',')

  useEffect(() => setFocus(root ?? null), [root])
  useEffect(() => {
    const ac = new AbortController()
    const q = path
      ? get<GData>('/graph/path', { path, types: typeKey }, ac.signal)
      : local
        ? get<GData>('/graph/local', { path: local, depth: 1, types: typeKey }, ac.signal)
        : get<GData>('/graph', { ...(focus ? { root: focus } : {}), types: typeKey }, ac.signal)
    setError(null)
    if (path) setData(null)
    q.then(setData).catch((e: Error) => {
      if (e.name !== 'AbortError') setError(e.message.includes('404') || e.message.toLowerCase().includes('not found') ? 'The sidecar is older than the app. Restart Raven.' : e.message)
    })
    return () => ac.abort()
  }, [focus, local, path, typeKey])

  const byKey = useMemo(() => new Map((data?.nodes ?? []).map((n) => [String(n.id), n])), [data])

  // Build the graph and the renderer each time the data changes.
  useEffect(() => {
    const el = box.current
    if (!el || !data || !data.nodes.length) return
    const g = new Graph({ multi: true, type: 'mixed' })
    const n = data.nodes.length
    data.nodes.forEach((d, i) => {
      const a = (2 * Math.PI * i) / n
      g.addNode(String(d.id), {
        x: Math.cos(a) * 100 + Math.random(),
        y: Math.sin(a) * 100 + Math.random(),
        size: Math.min(14, 2.5 + Math.sqrt(d.files ?? d.degree) * (data.level === 'folder' ? 1.6 : 1.4)),
        label: d.name,
        color: css(GROUP_VAR[d.group] ?? '--muted')
      })
    })
    for (const e of data.edges) {
      const s = String(e.src)
      const t = String(e.dst)
      if (g.hasNode(s) && g.hasNode(t)) g.addEdge(s, t, { type: 'line', size: e.type === 'similar' || e.type === 'folder' ? 0.5 : 1, color: css(EDGE_VAR[e.type] ?? '--line'), kind: e.type })
    }
    const renderer = new Sigma(g, el, {
      renderLabels: true,
      labelRenderedSizeThreshold: compact ? 2 : 3,
      labelDensity: 0.6, // sigma's label grid hides labels that would overlap
      labelGridCellSize: 90,
      labelColor: { color: css('--text') },
      labelFont: getComputedStyle(document.body).fontFamily,
      labelSize: 11,
      defaultEdgeType: 'line',
      zIndex: true
    })
    sigmaRef.current = renderer
    const fa2 = new FA2Layout(g, { settings: { ...forceAtlas2.inferSettings(g), slowDown: 4 } })
    fa2.start()
    // The nodes grow in from nothing while the layout untangles them.
    const sizes = new Map(g.mapNodes((k, a) => [k, a.size as number]))
    const born = performance.now()
    let grow = 0
    const tick = (t: number): void => {
      const p = Math.min(1, (t - born) / 900)
      const e = 1 - Math.pow(1 - p, 3)
      g.forEachNode((k) => g.setNodeAttribute(k, 'size', (sizes.get(k) ?? 3) * e))
      if (p < 1) grow = requestAnimationFrame(tick)
    }
    grow = requestAnimationFrame(tick)
    const stop = setTimeout(() => {
      fa2.stop()
      void renderer.getCamera().animatedReset({ duration: 600 })
    }, Math.min(6000, 1500 + n * 4))
    renderer.on('enterNode', ({ node }) => setHover(node))
    renderer.on('leaveNode', () => setHover(null))
    renderer.on('clickNode', ({ node }) => {
      const d = byKey.get(node)
      if (!d) return
      if (data.level === 'folder') setFocus(d.path)
      else setPicked(d)
    })
    renderer.on('doubleClickNode', (e) => {
      e.preventSigmaDefault()
      const d = byKey.get(e.node)
      // In a folder graph, a double-click on a folder maps that folder.
      if (d && path && d.group === 'folder') onPath?.(d.path)
      else if (d && data.level === 'file') void window.raven?.openPath(d.path)
    })
    renderer.on('clickStage', () => setPicked(null))
    // Shift-drag: a selection box instead of a pan.
    const captor = renderer.getMouseCaptor()
    let start: { x: number; y: number } | null = null
    captor.on('mousedown', (e) => {
      if (!e.original.shiftKey || data.level !== 'file') return
      start = { x: e.x, y: e.y }
      e.preventSigmaDefault()
      e.original.preventDefault()
    })
    captor.on('mousemovebody', (e) => {
      if (!start) return
      e.preventSigmaDefault()
      setRect({ x0: start.x, y0: start.y, x1: e.x, y1: e.y })
    })
    captor.on('mouseup', (e) => {
      if (!start) return
      const [x0, x1] = [Math.min(start.x, e.x), Math.max(start.x, e.x)]
      const [y0, y1] = [Math.min(start.y, e.y), Math.max(start.y, e.y)]
      const sel = new Set<string>()
      g.forEachNode((k, a) => {
        const v = renderer.graphToViewport({ x: a.x, y: a.y })
        if (v.x >= x0 && v.x <= x1 && v.y >= y0 && v.y <= y1) sel.add(k)
      })
      setLasso(sel)
      setRect(null)
      start = null
    })
    return () => {
      clearTimeout(stop)
      cancelAnimationFrame(grow)
      fa2.kill()
      renderer.kill()
      sigmaRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, theme, compact])

  // Hover and selection: the file's links stand out, the rest fades.
  useEffect(() => {
    const r = sigmaRef.current
    if (!r) return
    const g = r.getGraph()
    const center = hover ?? (picked ? String(picked.id) : null) ?? (data?.center != null ? String(data.center) : null)
    const near = new Set<string>()
    if (center && g.hasNode(center)) {
      near.add(center)
      g.forEachNeighbor(center, (k) => near.add(k))
    }
    const faded = css('--line')
    r.setSetting('nodeReducer', (k, a) => {
      if (lasso.has(k)) return { ...a, highlighted: true, zIndex: 2 }
      if (!near.size) return a
      return near.has(k) ? { ...a, zIndex: 1, highlighted: k === center } : { ...a, color: faded, label: '', zIndex: 0 }
    })
    r.setSetting('edgeReducer', (k, a) => {
      if (!near.size) return a
      const [s, t] = g.extremities(k)
      return near.has(s) && near.has(t) && (s === center || t === center) ? { ...a, size: 1.5 } : { ...a, hidden: true }
    })
  }, [hover, picked, lasso, data])

  const moveLasso = async (): Promise<void> => {
    const paths = [...lasso].map((k) => byKey.get(k)?.path).filter((p): p is string => !!p)
    const dst = (await pickFolder('Move the files into'))[0]
    if (!dst || !paths.length) return
    openReview({
      title: `Move ${paths.length} files to ${basename(dst)}`,
      reversible: true,
      cloudBytes: 0,
      frees: false,
      steps: [{ label: 'Move', files: paths.map((p) => ({ path: p, size: 0, checked: true })) }],
      run: async (steps) => {
        const keep = steps[0].files.filter((f) => f.checked).map((f) => f.path)
        try {
          const r = await post<{ journal: Journal }>('/fs/move', { paths: keep, dst })
          useApp.setState({ journal: r.journal })
          say(`Moved ${keep.length} files to ${basename(dst)}`, 'info', { undo: true })
          setLasso(new Set())
          setData(null)
          setFocus((f) => f)
        } catch (e) {
          say((e as Error).message, 'error')
        }
      }
    })
  }

  if (compact && (!data || data.nodes.length < 2)) return null
  if (error && !data) return <div className="py-6 text-center text-danger">{error}</div>  // the Inspector shows it only when the file has links
  if (data && !data.nodes.length && !path)
    return <div className="py-6 text-center text-muted">{data.building ? 'Raven is building the graph…' : 'No links between these files yet.'}</div>
  return (
    <div className={`relative flex min-h-0 flex-col ${compact ? 'mb-3 border border-line' : ''}`} style={{ height }}>
      {!compact && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-4 py-1.5 text-[12px]">
          {path && (
            <button className="link text-muted hover:text-accent" onClick={() => onPath?.(path.slice(0, path.lastIndexOf('/')) || '/')}>
              ↑ {tildify(path, home)}
            </button>
          )}
          {focus && !path && (
            <button className="link text-muted hover:text-accent" onClick={() => setFocus(focus === root ? null : focus.slice(0, focus.lastIndexOf('/')) || null)}>
              ↑ {tildify(focus, home)}
            </button>
          )}
          {TYPES.filter(([k]) => path || k !== 'folder').map(([k, l]) => (
            <label key={k} className="flex items-center gap-1 text-muted">
              <input
                type="checkbox"
                checked={types.has(k)}
                onChange={() =>
                  setTypes((t) => {
                    const n = new Set(t)
                    if (n.has(k)) n.delete(k)
                    else n.add(k)
                    return n
                  })
                }
                className="accent-[var(--accent)]"
              />
              <span style={{ color: css(EDGE_VAR[k]) }}>—</span> {l}
            </label>
          ))}
          <span className="mono ml-auto text-[11px] text-muted">
            {data
              ? `${data.nodes.length} ${data.level === 'folder' ? 'folders' : 'nodes'} · ${data.edges.filter((e) => e.type !== 'folder').length} links${data.cut ? ` · first ${data.files} files` : ''}`
              : 'reading the folder…'}{' '}
            · shift-drag selects{path ? ' · double-click a folder to open it' : ''}
          </span>
        </div>
      )}
      <div ref={box} className="min-h-0 flex-1" />
      {path && !data && !error && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <span className="anim-breathe h-3 w-3 rounded-full bg-accent" />
        </div>
      )}
      {path && error && <div className="absolute inset-x-0 top-1/2 text-center text-danger">{error}</div>}
      {path && data && data.edges.length === 0 && (
        <div className="anim-fade pointer-events-none absolute inset-x-0 top-1/2 text-center text-muted">No links in this folder.</div>
      )}
      {rect && (
        <div
          className="pointer-events-none absolute border border-accent bg-accent-soft"
          style={{ left: Math.min(rect.x0, rect.x1), top: Math.min(rect.y0, rect.y1) + (compact ? 0 : 33), width: Math.abs(rect.x1 - rect.x0), height: Math.abs(rect.y1 - rect.y0) }}
        />
      )}
      {(picked || lasso.size > 0) && !compact && (
        <div className="flex items-center gap-4 border-t border-line px-4 py-1.5 text-[12px]">
          {lasso.size > 0 ? (
            <>
              <span>{lasso.size} files selected</span>
              <button className="link text-accent" onClick={() => void moveLasso()}>
                Move into a folder…
              </button>
              <button className="link text-muted" onClick={() => setLasso(new Set())}>
                Clear
              </button>
            </>
          ) : (
            picked && (
              <>
                <span className="truncate">{picked.name}</span>
                <span className="mono truncate text-muted">{tildify(picked.folder, home)}</span>
                <span className="text-muted">{picked.degree} links</span>
                <button className="link ml-auto text-accent" onClick={() => void window.raven?.openPath(picked.path)}>
                  Open
                </button>
                <button className="link text-accent" onClick={() => reveal(picked.path)}>
                  Show in Files
                </button>
              </>
            )
          )}
        </div>
      )}
    </div>
  )
}
