import { Search, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { get } from '../lib/api'
import FolderRow from '../components/FolderRow'
import HitRow from '../components/HitRow'
import VisualGrid, { openVisual } from '../components/VisualGrid'
import { EmptyState } from '../components/EmptyState'
import { Chip as Pill } from '../components/ui'
import { mergedHits, PATHLIKE, useFind, useFolderFind, useVisualFind } from '../lib/search'
import type { Chip, FolderHit, SearchHit, VisualHit } from '../lib/types'
import { activity, navigate, openConnect, reveal, useApp } from '../store'

type Item = { kind: 'folder'; hit: FolderHit } | { kind: 'file'; hit: SearchHit } | { kind: 'visual'; hit: VisualHit }

// The full results screen (Ctrl+Enter): a full-width list, grouped as Folders, Files, Content and Images.
// "similar:<path>" shows the files that look like that file.
export default function ResultsScreen() {
  const initial = useApp((s) => s.cmdQuery)
  const [q, setQ] = useState(initial)
  const [active, setActive] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const pathMode = PATHLIKE.test(q.trim())
  const similar = q.trim().startsWith('similar:')
  // Section 9.3: the local model reads filter words that the rules do not know ("two weeks ago", "my DBMS notes").
  // Its answer is the query with filter tokens. The chips below show them, and each one can be removed.
  const [nl, setNl] = useState<{ for: string; query: string; chips: Chip[]; ms: number } | null>(null)
  useEffect(() => {
    const query = q.trim()
    setNl(null)
    if (!query || pathMode || similar) return
    const ac = new AbortController()
    const t = setTimeout(() => {
      get<{ query: string; chips: Chip[]; used: boolean; ms: number }>('/search/parse', { q: query }, ac.signal)
        .then((r) => r.used && r.query !== query && setNl({ for: query, query: r.query, chips: r.chips, ms: r.ms }))
        .catch(() => {})
    }, 600)
    return () => (clearTimeout(t), ac.abort())
  }, [q, pathMode, similar])
  const effective = nl && nl.for === q.trim() ? nl.query : q
  const dropChip = (c: Chip): void => {
    if (!nl) return
    const query = nl.query.replace(c.token, ' ').replace(/\s+/g, ' ').trim()
    setNl({ ...nl, query, chips: nl.chips.filter((x) => x.token !== c.token) })
  }
  const find = useFind(effective, !pathMode && !similar, 100)
  const folderFind = useFolderFind(q, !similar, 20)
  const visual = useVisualFind(q, true, similar ? 48 : 24)
  const visualHits = visual.q === q.trim() ? visual.hits : []
  const folderHits = folderFind.q === q.trim() ? folderFind.hits : []
  const folderPaths = new Set(folderHits.map((f) => f.path))
  const hits = useMemo(() => mergedHits(find, 100), [find]).filter((h) => !folderPaths.has(h.path) && !(h.is_dir && folderHits.length))
  const files = hits.filter((h) => h.why === 'name')
  const content = hits.filter((h) => h.why !== 'name')
  const flat: Item[] = [
    ...folderHits.map((hit) => ({ kind: 'folder', hit }) as Item),
    ...files.map((hit) => ({ kind: 'file', hit }) as Item),
    ...content.map((hit) => ({ kind: 'file', hit }) as Item),
    ...visualHits.map((hit) => ({ kind: 'visual', hit }) as Item)
  ]
  const indexOf = (h: FolderHit | SearchHit | VisualHit): number => flat.findIndex((i) => i.hit === h)

  useEffect(() => input.current?.focus(), [])
  useEffect(() => setActive(0), [q])
  // Habits: a search counts when its results stay on screen for a second.
  useEffect(() => {
    if (q.trim().length < 3) return
    const t = setTimeout(() => activity('search', undefined, q), 1000)
    return () => clearTimeout(t)
  }, [q])

  const open = (item: Item | undefined, newTab = false): void => {
    if (!item) return
    useApp.setState({ cmdQuery: q })
    activity('result_click', item.hit.path, q)
    if (item.kind === 'visual') {
      if (newTab) void window.raven?.openPath(item.hit.path)
      else openVisual(item.hit)
    } else if (item.kind === 'folder') {
      if (item.hit.why === 'connect') openConnect(item.hit.path)
      else void navigate(item.hit.path, { newTab })
    } else if (item.hit.is_dir) void navigate(item.hit.path, { newTab })
    else if (newTab) void window.raven?.openPath(item.hit.path)
    else reveal(item.hit.path, true, item.hit.page, item.hit.at)
  }

  const group = (title: string, list: SearchHit[]) =>
    list.length > 0 && (
      <section className="mb-5">
        <div className="mb-1 flex items-center gap-4 px-6 pt-2">
          <span className="serif text-[16px] leading-none">{title}</span>
          <span className="mono text-[11px] text-muted">{list.length}</span>
          <span className="anim-rule h-px flex-1 bg-line" />
        </div>
        {list.map((h) => (
          <HitRow
            key={h.path}
            hit={h}
            full
            active={indexOf(h) === active}
            onClick={() => setActive(indexOf(h))}
            onDoubleClick={() => open(flat[indexOf(h)])}
          />
        ))}
      </section>
    )

  const t = find.hybrid?.timings
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-4 border-b border-line px-6 py-5">
        <Search size={16} strokeWidth={1.5} className="shrink-0 text-accent" />
        <input
          ref={input}
          value={q}
          spellCheck={false}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            e.stopPropagation()
            if (e.key === 'ArrowDown') (e.preventDefault(), setActive((a) => Math.min(flat.length - 1, a + 1)))
            else if (e.key === 'ArrowUp') (e.preventDefault(), setActive((a) => Math.max(0, a - 1)))
            else if (e.key === 'Enter') open(flat[active], e.shiftKey)
            else if (e.key === 'Escape') useApp.setState({ screen: 'files' })
          }}
          placeholder="Search by name, path, words or meaning"
          className="flex-1 border-0 text-[18px] placeholder:text-muted/70 hover:bg-transparent focus:bg-transparent"
          style={{ fontFamily: 'var(--font-serif)', letterSpacing: '-0.01em' }}
        />
        {(find.pending || folderFind.pending) && (
          <span className="relative h-px w-14 overflow-hidden bg-line">
            <span className="absolute inset-y-0 w-1/3 bg-accent" style={{ animation: 'rv-scan 1s ease-in-out infinite' }} />
          </span>
        )}
        {(t || folderHits.length > 0) && (
          <span className="flex items-center gap-5">
            <Pill tone="good">{flat.length} results</Pill>
            {t && <Pill>{t.total_ms?.toFixed(0)} ms</Pill>}
          </span>
        )}
      </div>
      {nl && nl.for === q.trim() ? (
        <div className="anim-fade flex flex-wrap items-center gap-2.5 border-b border-line px-6 py-2">
          <span className="label">filters</span>
          {nl.chips.map((c) => (
            <span key={c.token} className="mono flex items-center gap-1 text-[12px] text-accent underline decoration-accent/40 underline-offset-4">
              {c.label}
              <button aria-label={`Remove ${c.label}`} onClick={() => dropChip(c)} className="p-1.5 transition-colors hover:text-danger">
                <X size={11} />
              </button>
            </span>
          ))}
          <span className="mono text-[11px] text-muted">
            local model read “{q.trim()}” as “{find.hybrid?.text ?? ''}” · {(nl.ms / 1000).toFixed(1)} s
          </span>
        </div>
      ) : (
        find.hybrid &&
        find.hybrid.chips.length > 0 && (
          <div className="anim-fade flex items-center gap-2.5 border-b border-line px-6 py-2">
            <span className="label">filters</span>
            {find.hybrid.chips.map((c) => (
              <span key={c.token} className="mono text-[12px] text-accent underline decoration-accent/40 underline-offset-4">
                {c.label}
              </span>
            ))}
          </div>
        )
      )}
      <div className="min-h-0 flex-1 overflow-y-auto py-2">
        {!q.trim() && (
          <EmptyState art="search" title="Search everything" />
        )}
        {q.trim() && !flat.length && !find.pending && !folderFind.pending && !visual.pending && (
          <EmptyState
            art="search"
            title={similar ? 'Nothing looks like this' : 'No matches'}
          />
        )}
        {folderHits.length > 0 && (
          <section className="mb-5">
            <div className="mb-1 flex items-center gap-4 px-6 pt-2">
              <span className="serif text-[16px] leading-none">Folders</span>
              <span className="mono text-[11px] text-muted">{folderHits.length}</span>
              <span className="anim-rule h-px flex-1 bg-line" />
            </div>
            {folderHits.map((h) => (
              <FolderRow
                key={`d:${h.path}`}
                hit={h}
                full
                active={indexOf(h) === active}
                onClick={() => setActive(indexOf(h))}
                onDoubleClick={() => open(flat[indexOf(h)])}
              />
            ))}
          </section>
        )}
        {group('Files', files)}
        {group('Content', content)}
        {visualHits.length > 0 && (
          <section className="mb-5">
            <div className="mb-2 flex items-center gap-4 px-6 pt-2">
              <span className="serif text-[16px] leading-none">{similar ? 'Looks like this file' : 'Images, pages and video moments'}</span>
              <span className="mono text-[11px] text-muted">{visualHits.length}</span>
              <span className="anim-rule h-px flex-1 bg-line" />
            </div>
            <VisualGrid
              hits={visualHits}
              active={flat[active]?.kind === 'visual' ? flat[active].hit.path : null}
              onActive={(path) => setActive(flat.findIndex((i) => i.kind === 'visual' && i.hit.path === path))}
            />
          </section>
        )}
      </div>
    </div>
  )
}
