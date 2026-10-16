import { useEffect, useState } from 'react'
import FileGrid from '../components/FileGrid'
import FilesHeader from '../components/FilesHeader'
import FileList from '../components/FileList'
import Inspector from '../components/Inspector'
import PassivePane from '../components/PassivePane'
import PathLine from '../components/PathLine'
import SelectionBar from '../components/SelectionBar'
import { get } from '../lib/api'
import { reveal, useApp } from '../store'

/** The wait for the sidecar, with the seconds, so a slow start does not look like a hang. */
function Starting() {
  const [s, setS] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setS((x) => x + 1), 1000)
    return () => clearInterval(t)
  }, [])
  return (
    <div className="mono p-4 text-[11px] text-muted">
      starting raven-core… {s > 3 && `${s} s`}
      {s > 15 && <div className="mt-1">A large index can take a minute.</div>}
    </div>
  )
}

/** Habits (PLAN2 P2-8.3): the files you probably want now. Only in your home folder. */
function Continue() {
  const atHome = useApp((s) => !!s.places && s.path === s.places.home)
  const [files, setFiles] = useState<{ path: string; name: string }[]>([])
  useEffect(() => {
    if (atHome) get<{ files: { path: string; name: string }[] }>('/habits/continue').then((r) => setFiles(r.files)).catch(() => setFiles([]))
  }, [atHome])
  if (!atHome || !files.length) return null
  return (
    <div className="flex items-center gap-3 overflow-hidden border-b border-line px-4 py-1 text-[12px]">
      <span className="shrink-0 text-muted">Continue</span>
      {files.map((f) => (
        <button key={f.path} title={f.path} className="truncate text-accent hover:underline" onClick={() => reveal(f.path)}>
          {f.name}
        </button>
      ))}
    </div>
  )
}

function Live() {
  const view = useApp((s) => s.view)
  const ready = useApp((s) => s.ready)
  const loading = useApp((s) => s.loading)
  return (
    <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
      <PathLine />
      {/* A thin bar sweeps under the path while a folder opens. */}
      <div className="relative -mt-px h-px shrink-0 overflow-hidden">
        {loading && <span className="absolute inset-y-0 w-1/3 bg-accent" style={{ animation: 'rv-scan 0.9s ease-in-out infinite' }} />}
      </div>
      <FilesHeader />
      <Continue />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {!ready ? <Starting /> : view === 'list' ? <FileList /> : <FileGrid />}
      </div>
      <SelectionBar />
    </div>
  )
}

export default function FilesScreen() {
  const inspector = useApp((s) => s.inspector)
  const dual = useApp((s) => s.dual)
  const active = useApp((s) => s.activeTab)
  // Dual pane (F3): the live tab and its partner, split by a 1 px line. The sides stay fixed.
  const partner = dual && (active === dual.left ? dual.right : active === dual.right ? dual.left : null)
  return (
    <div className="flex min-h-0 flex-1">
      {partner != null && active === dual!.right && (
        <>
          <PassivePane tabId={partner} />
          <div className="w-px shrink-0 bg-line" />
        </>
      )}
      <Live />
      {partner != null && active === dual!.left && (
        <>
          <div className="w-px shrink-0 bg-line" />
          <PassivePane tabId={partner} />
        </>
      )}
      {inspector && <Inspector />}
    </div>
  )
}
