import { EmptyState } from './EmptyState'
import { Chip, Ghost } from './ui'
import { useEffect, useMemo, useRef, useState } from 'react'
import { get, on, post } from '../lib/api'
import { tildify } from '../lib/format'
import type { Journal } from '../lib/types'
import { reveal, say, useApp } from '../store'

type Point = { id: number; path: string; name: string; category: string | null; x: number; y: number; c: number }
type Cluster = { id: number; name: string; count: number; x: number; y: number }
type Map = { points: Point[]; clusters: Cluster[]; stale: boolean; building: boolean; progress: string; error: string | null; ms?: number }
type Plan = { cluster: number; name: string; folder: string; moves: { src: string; dst: string }[] }

const css = (v: string): string => getComputedStyle(document.documentElement).getPropertyValue(v).trim()

// The category tints of the Space chart are for large areas. Small points need the tints of the other
// theme: light tints on the dark background, dark tints on the light one.
const LIGHT_TINTS = ['#c9bddb', '#e3cdb0', '#bccfe0', '#d9d2a6', '#e2bcbc', '#bcd6c6']
const DARK_TINTS = ['#6b5d8c', '#8a6d4e', '#4d6a87', '#7a7444', '#855252', '#477a5e']
function darkBg(): boolean {
  const m = /#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})/i.exec(css('--bg'))
  return !!m && parseInt(m[1], 16) + parseInt(m[2], 16) + parseInt(m[3], 16) < 384
}

/**
 * File Galaxy (section 13.12 item 2): every indexed file is a point, placed by meaning (UMAP).
 * Groups have names from the local model. Drag to pan, wheel to zoom. A click on a point opens the file.
 * A click on a group name lists its files, and the group can become a folder (one undo).
 */
export default function Galaxy() {
  const home = useApp((s) => s.places?.home ?? '')
  const theme = useApp((s) => s.theme)
  const [map, setMap] = useState<Map | null>(null)
  const [hover, setHover] = useState<Point | null>(null)
  const [picked, setPicked] = useState<number | null>(null)
  const [plan, setPlan] = useState<Plan | null>(null)
  const [view, setView] = useState({ x: 0, y: 0, k: 1 })
  const canvas = useRef<HTMLCanvasElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean } | null>(null)
  const [size, setSize] = useState({ w: 800, h: 600 })

  const load = (): void => void get<Map>('/galaxy').then(setMap).catch((e) => say(e.message, 'error'))
  useEffect(() => {
    load()
    return on('galaxy_ready', load)
  }, [])
  useEffect(() => {
    if (map && !map.building && (map.stale || !map.points.length) && !map.error) void post('/galaxy/build').then(() => setMap({ ...map, building: true }))
  }, [map?.stale, map?.points.length]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!map?.building) return
    const t = setInterval(load, 1500)
    return () => clearInterval(t)
  }, [map?.building])
  useEffect(() => {
    const el = box.current
    if (!el) return
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Map space is [-1, 1]. Screen = center + (p - view) * scale.
  const scale = Math.min(size.w, size.h) * 0.45 * view.k
  const toScreen = (x: number, y: number): [number, number] => [size.w / 2 + (x - view.x) * scale, size.h / 2 + (y - view.y) * scale]
  const colors = useMemo(() => (darkBg() ? LIGHT_TINTS : DARK_TINTS), [theme]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const cv = canvas.current
    if (!cv || !map) return
    const dpr = window.devicePixelRatio || 1
    cv.width = size.w * dpr
    cv.height = size.h * dpr
    const g = cv.getContext('2d')!
    g.setTransform(dpr, 0, 0, dpr, 0, 0)
    g.clearRect(0, 0, size.w, size.h)
    const muted = css('--muted')
    const accent = css('--accent')
    const text = css('--text')
    const r = Math.max(5, Math.min(10, 5 * Math.sqrt(view.k)))
    for (const p of map.points) {
      const [sx, sy] = toScreen(p.x, p.y)
      g.globalAlpha = picked === null || p.c === picked ? 1 : 0.25
      g.fillStyle = p.c < 0 ? muted : colors[p.c % colors.length]
      g.fillRect(sx - r / 2, sy - r / 2, r, r)
    }
    g.globalAlpha = 1
    if (hover) {
      const [sx, sy] = toScreen(hover.x, hover.y)
      g.strokeStyle = accent
      g.lineWidth = 2
      g.strokeRect(sx - r, sy - r, r * 2, r * 2)
    }
    g.font = '12px "IBM Plex Sans", sans-serif'
    g.textAlign = 'center'
    // Larger groups first. A label that would cover a drawn label waits for a closer zoom.
    const drawn: [number, number, number, number][] = []
    for (const c of map.clusters) {
      const [sx, sy] = toScreen(c.x, c.y)
      const label = `${c.name} · ${c.count}`
      const w = g.measureText(label).width + 8
      const b: [number, number, number, number] = [sx - w / 2, sy - 24, sx + w / 2, sy - 6]
      if (c.id !== picked && drawn.some((d) => b[0] < d[2] && b[2] > d[0] && b[1] < d[3] && b[3] > d[1])) continue
      drawn.push(b)
      g.fillStyle = c.id === picked ? accent : text
      g.fillText(label, sx, sy - 10)
    }
  }, [map, size, view, hover, picked, colors]) // eslint-disable-line react-hooks/exhaustive-deps

  const nearest = (mx: number, my: number): Point | null => {
    if (!map) return null
    let best: Point | null = null
    let bd = 64
    for (const p of map.points) {
      const [sx, sy] = toScreen(p.x, p.y)
      const d = (sx - mx) ** 2 + (sy - my) ** 2
      if (d < bd) (bd = d), (best = p)
    }
    return best
  }
  const clusterAt = (mx: number, my: number): Cluster | null => {
    if (!map) return null
    for (const c of map.clusters) {
      const [sx, sy] = toScreen(c.x, c.y)
      if (Math.abs(sx - mx) < 70 && Math.abs(sy - 10 - my) < 9) return c
    }
    return null
  }

  const makePlan = async (cluster: number): Promise<void> => {
    try {
      setPlan(await post<Plan>('/galaxy/plan', { cluster }))
    } catch (e) {
      say((e as Error).message, 'error')
    }
  }
  const applyPlan = async (): Promise<void> => {
    if (!plan) return
    try {
      const r = await post<{ moved: number; label: string; journal: Journal }>('/galaxy/apply', { folder: plan.folder, moves: plan.moves })
      useApp.setState({ journal: r.journal })
      say(`${r.label}: ${r.moved} files`, 'info', { undo: true })
      setPlan(null)
      void post('/galaxy/build').then(load)
    } catch (e) {
      say((e as Error).message, 'error')
    }
  }

  const members = picked !== null && map ? map.points.filter((p) => p.c === picked) : []
  const pickedCluster = map?.clusters.find((c) => c.id === picked)
  return (
    <div className="flex min-h-0 flex-1">
      <div ref={box} className="relative min-w-0 flex-1 overflow-hidden">
        <canvas
          ref={canvas}
          style={{ width: size.w, height: size.h, cursor: hover ? 'pointer' : drag.current ? 'grabbing' : 'grab' }}
          onMouseDown={(e) => (drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false })}
          onMouseMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect()
            const d = drag.current
            if (d) {
              const dx = e.clientX - d.x
              const dy = e.clientY - d.y
              if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true
              setView({ ...view, x: d.vx - dx / scale, y: d.vy - dy / scale })
            } else setHover(nearest(e.clientX - rect.left, e.clientY - rect.top))
          }}
          onMouseUp={(e) => {
            const d = drag.current
            drag.current = null
            if (d?.moved) return
            const rect = e.currentTarget.getBoundingClientRect()
            const mx = e.clientX - rect.left
            const my = e.clientY - rect.top
            const c = clusterAt(mx, my)
            if (c) return setPicked(c.id === picked ? null : c.id)
            const p = nearest(mx, my)
            if (p) reveal(p.path)
            else setPicked(null)
          }}
          onMouseLeave={() => ((drag.current = null), setHover(null))}
          onWheel={(e) => setView({ ...view, k: Math.min(40, Math.max(0.5, view.k * (e.deltaY < 0 ? 1.2 : 1 / 1.2))) })}
        />
        <div className="mono pointer-events-none absolute bottom-3 left-6 max-w-[80%] truncate bg-bg/90 px-3 py-1.5 text-[11px] text-muted">
          {hover
            ? `${hover.name} · ${tildify(hover.path.slice(0, hover.path.lastIndexOf('/')), home)}${hover.category ? ` · ${hover.category}` : ''}`
            : map?.building
              ? `building the map: ${map.progress}…`
              : map
                ? `${map.points.length} files · ${map.clusters.length} groups · drag to move, wheel to zoom, click a group name`
                : 'loading'}
        </div>
        {map && !map.points.length && !map.building && (
          <div className="absolute inset-0 flex items-center justify-center">
            <EmptyState art="search" title={map.error ? 'The map failed' : 'The map is not ready yet'}>
              {map.error && <span className="mono text-[12px] text-danger">{map.error}</span>}
            </EmptyState>
          </div>
        )}
        <div className="absolute right-6 top-3 flex items-center gap-5 bg-bg/90 px-3 py-1.5">
          <button className="link text-muted transition-colors hover:text-text" onClick={() => setView({ x: 0, y: 0, k: 1 })}>
            Fit
          </button>
          <button className="link text-accent disabled:opacity-50" disabled={map?.building} onClick={() => void post('/galaxy/build').then(load)}>
            {map?.building ? 'Building…' : 'Build again'}
          </button>
        </div>
      </div>
      {pickedCluster && (
        <aside className="anim-slide flex w-[340px] shrink-0 flex-col border-l border-line">
          <div className="border-b border-line p-4">
            <div className="serif text-[16px] leading-none">{pickedCluster.name}</div>
            <div className="mt-1"><Chip>{members.length} files</Chip></div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {members.map((m) => (
              <div key={m.path} onClick={() => reveal(m.path)} className="cursor-default px-4 py-1.5 transition-colors hover:bg-hover" title={m.path}>
                <div className="truncate">{m.name}</div>
                <div className="mono truncate text-[11px] text-muted">{tildify(m.path.slice(0, m.path.lastIndexOf('/')), home)}</div>
              </div>
            ))}
          </div>
          <div className="border-t border-line p-4">
            {!plan || plan.cluster !== pickedCluster.id ? (
              <button className="btn-primary" onClick={() => void makePlan(pickedCluster.id)}>
                Put these in one folder…
              </button>
            ) : (
              <div>
                <p className="mb-2 text-[12.5px]">
                  Move {plan.moves.length} files into <span className="mono">{tildify(plan.folder, home)}</span>. One undo reverses it.
                </p>
                <div className="flex gap-4">
                  <button className="btn-primary" disabled={!plan.moves.length} onClick={() => void applyPlan()}>
                    Move {plan.moves.length}
                  </button>
                  <Ghost tone="plain" onClick={() => setPlan(null)}>
                    Cancel
                  </Ghost>
                </div>
              </div>
            )}
          </div>
        </aside>
      )}
    </div>
  )
}
