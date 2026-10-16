// Streaming Find (section 5.2 rule 3): fuzzy names first, then name + BM25, then the full hybrid list.
// Each key press cancels the requests of the previous one.
import { useEffect, useRef, useState } from 'react'
import { setLatency } from '../store'
import { get } from './api'
import { fuzzy, type FuzzyHit } from './fuzzy'
import { clock } from './format'
import type { FolderHit, FolderResponse, SearchHit, SearchResponse, VisualHit } from './types'

export type FindState = {
  q: string
  fuzzy: FuzzyHit[]
  keyword: SearchResponse | null
  hybrid: SearchResponse | null
  pending: boolean
}

const HYBRID_DEBOUNCE = 150

export function fuzzyToHit(f: FuzzyHit): SearchHit {
  const parent = f.path.slice(0, f.path.lastIndexOf('/')) || '/'
  return {
    id: f.id, path: f.path, name: f.name, parent, is_dir: f.dir, size: null, mtime: 0, kind: '', title: null,
    snippet: [], why: 'name', page: null, score: 0, ranks: { name: 1 }, positions: f.positions
  }
}

/** The best list we have now for this query. */
export function mergedHits(s: FindState, limit: number): SearchHit[] {
  if (s.hybrid && s.hybrid.query === s.q) return s.hybrid.results.slice(0, limit)
  const out: SearchHit[] = []
  const seen = new Set<string>()
  const fz = new Map(s.fuzzy.map((f) => [f.path, f]))
  if (s.keyword && s.keyword.query === s.q) {
    for (const h of s.keyword.results) {
      if (seen.has(h.path)) continue
      seen.add(h.path)
      out.push(fz.has(h.path) ? { ...h, positions: fz.get(h.path)!.positions } : h)
    }
  }
  for (const f of s.fuzzy) {
    if (seen.has(f.path)) continue
    seen.add(f.path)
    out.push(fuzzyToHit(f))
  }
  return out.slice(0, limit)
}

export function useFind(q: string, enabled: boolean, limit = 30): FindState {
  const [state, setState] = useState<FindState>({ q: '', fuzzy: [], keyword: null, hybrid: null, pending: false })
  const acs = useRef<AbortController[]>([])
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    acs.current.forEach((a) => a.abort())
    acs.current = []
    if (timer.current) clearTimeout(timer.current)
    const query = q.trim()
    if (!enabled || !query) {
      setState({ q: query, fuzzy: [], keyword: null, hybrid: null, pending: false })
      return
    }
    setState((s) => ({ ...s, q: query, pending: true }))

    let live = true
    void fuzzy(query).then((r) => {
      if (!live) return
      setLatency('fuzzy', r.ms)
      setState((s) => (s.q === query ? { ...s, fuzzy: r.items } : s))
    })

    const ak = new AbortController()
    acs.current.push(ak)
    const t0 = performance.now()
    get<SearchResponse>('/search', { q: query, mode: 'keyword', limit }, ak.signal)
      .then((r) => {
        setLatency('keyword', performance.now() - t0)
        setState((s) => (s.q === query ? { ...s, keyword: r } : s))
      })
      .catch(() => {})

    timer.current = setTimeout(() => {
      const ah = new AbortController()
      acs.current.push(ah)
      const t1 = performance.now()
      get<SearchResponse>('/search', { q: query, mode: 'hybrid', limit }, ah.signal)
        .then((r) => {
          setLatency('hybrid', performance.now() - t1)
          setLatency('hybrid_server', r.timings.total_ms ?? 0)
          if (r.timings.embed_ms !== undefined) setLatency('embed', r.timings.embed_ms)
          if (r.timings.dense_ms !== undefined) setLatency('dense', r.timings.dense_ms)
          setState((s) => (s.q === query ? { ...s, hybrid: r, pending: false } : s))
        })
        .catch(() => {})
    }, HYBRID_DEBOUNCE)

    return () => {
      live = false
    }
  }, [q, enabled, limit])

  return state
}

export function whyLabel(h: SearchHit): string {
  if (h.why === 'name') return 'name'
  if (h.why === 'filter') return 'filter'
  if (h.at != null) return `${h.why} · said at ${clock(h.at)}`
  return h.page ? `${h.why} · p.${h.page}` : h.why
}

const VISUAL_DEBOUNCE = 220

/** Search by appearance (photos, PDF pages, video moments). `similar:` + a path finds files that look alike. */
export function useVisualFind(q: string, enabled: boolean, limit = 24): { q: string; hits: VisualHit[]; error: string | null; pending: boolean } {
  const [state, setState] = useState<{ q: string; hits: VisualHit[]; error: string | null; pending: boolean }>({ q: '', hits: [], error: null, pending: false })
  useEffect(() => {
    const query = q.trim()
    if (!enabled || !query || PATHLIKE.test(query)) {
      setState({ q: query, hits: [], error: null, pending: false })
      return
    }
    const ac = new AbortController()
    setState((s) => ({ ...s, pending: true }))
    const similar = query.startsWith('similar:') ? query.slice(8).trim() : null
    const t = setTimeout(() => {
      const req = similar
        ? get<{ results: VisualHit[]; error?: string }>('/visual/similar', { path: similar, limit }, ac.signal)
        : get<{ results: VisualHit[]; error?: string }>('/visual/search', { q: query, limit }, ac.signal)
      req
        .then((r) => setState({ q: query, hits: r.results, error: r.error ?? null, pending: false }))
        .catch((e) => e.name !== 'AbortError' && setState({ q: query, hits: [], error: e.message, pending: false }))
    }, similar ? 0 : VISUAL_DEBOUNCE)
    return () => {
      clearTimeout(t)
      ac.abort()
    }
  }, [q, enabled, limit])
  return state
}

/** A query that names a place: a path, a home path or a server address. */
export const PATHLIKE = /^(~|\/|\\\\|[a-z]+:\/\/)/i

const FOLDER_DEBOUNCE = 120

/** Folder search (by name, path or description). Paths answer at once, descriptions after a short pause. */
export function useFolderFind(q: string, enabled: boolean, limit = 6): { q: string; hits: FolderHit[]; mode: 'path' | 'describe'; pending: boolean } {
  const [state, setState] = useState<{ q: string; hits: FolderHit[]; mode: 'path' | 'describe'; pending: boolean }>({ q: '', hits: [], mode: 'describe', pending: false })
  useEffect(() => {
    const query = q.trim()
    if (!enabled || !query) {
      setState({ q: query, hits: [], mode: 'describe', pending: false })
      return
    }
    setState((s) => ({ ...s, q: query, pending: true }))
    const ac = new AbortController()
    const t = setTimeout(
      () => {
        const t0 = performance.now()
        get<FolderResponse>('/search/folders', { q: query, limit }, ac.signal)
          .then((r) => {
            setLatency('folders', performance.now() - t0)
            setState((s) => (s.q === query ? { q: query, hits: r.results, mode: r.mode, pending: false } : s))
          })
          .catch(() => {})
      },
      PATHLIKE.test(query) ? 0 : FOLDER_DEBOUNCE
    )
    return () => {
      clearTimeout(t)
      ac.abort()
    }
  }, [q, enabled, limit])
  return state
}

export function folderWhy(h: FolderHit): string {
  if (h.why === 'summary') return 'description'
  if (h.why === 'content') return 'meaning'
  if (h.why === 'connect') return 'server'
  if (h.why === 'disk') return 'name'
  return h.why
}
