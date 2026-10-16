import { basename } from '../lib/format'
import { showInSpace } from '../screens/SpaceScreen'
import { openOrganize, summarize, useApp } from '../store'
import { kindVar } from './FileIcon'
import { stagger } from './ui'

// One line of actions for the folder you are in. It only calls actions that already exist.

const openBar = (query = ''): void => useApp.setState({ cmdOpen: true, cmdQuery: query })

function Mix() {
  const entries = useApp((s) => s.listing?.entries)
  const showHidden = useApp((s) => s.showHidden)
  if (!entries?.length) return null
  const list = showHidden ? entries : entries.filter((e) => !e.is_hidden)
  const count = new Map<string, number>()
  for (const e of list) {
    const c = kindVar(e.ext, e.is_dir, e.kind_group)
    count.set(c, (count.get(c) ?? 0) + 1)
  }
  const total = list.length || 1
  return (
    <span className="ml-auto flex items-center gap-3 text-[12px] text-muted" title={`${list.length} items`}>
      {list.length} item{list.length === 1 ? '' : 's'}
      <span className="flex h-[4px] w-40 gap-px overflow-hidden">
        {[...count.entries()].map(([c, n]) => (
          <span key={c} className="h-full origin-left animate-[rv-rule_700ms_ease-out_both]" style={{ width: `${(n / total) * 100}%`, background: c }} />
        ))}
      </span>
    </span>
  )
}

function Strip() {
  const path = useApp((s) => s.path)
  const archive = useApp((s) => !!s.listing?.archive)
  if (archive) return null
  const name = basename(path) || path
  const chips: { label: string; run: () => void }[] = [
    { label: 'Find in this folder', run: () => openBar(`in:${name} `) },
    { label: 'Organize this folder', run: () => openOrganize(path) },
    { label: 'Summarize', run: () => void summarize(path) },
    { label: 'Show in Space', run: () => void showInSpace(path) }
  ]
  return (
    <div className="flex items-center gap-7 border-b border-line px-6 py-1.5 text-[12.5px]">
      {chips.map((c, k) => (
        <button key={c.label} onClick={c.run} style={stagger(k)} className="anim-row group flex items-center gap-1.5 text-muted transition-colors hover:text-text">
          <span className="link-g">{c.label}</span>
          <span aria-hidden className="-translate-x-1 text-accent opacity-0 transition-all duration-200 group-hover:translate-x-0 group-hover:opacity-100">
            →
          </span>
        </button>
      ))}
      <Mix />
    </div>
  )
}

export default function FilesHeader() {
  const ready = useApp((s) => s.ready)
  const home = useApp((s) => s.places?.home)
  if (!ready || !home) return null
  return <div data-tour="overview"><Strip /></div>
}
