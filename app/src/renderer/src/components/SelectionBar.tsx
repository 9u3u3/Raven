import { copySelection, selectOnly, selectedPaths, trashSelection, useApp } from '../store'

// A strip of ink that rises from the bottom when files are selected: the common actions as plain words.
// It calls the same actions as the keyboard shortcuts and the context menu.
function Act({ label, keys, run, danger }: { label: string; keys?: string; run: () => void; danger?: boolean }) {
  return (
    <button onClick={run} title={keys ? `${label} (${keys})` : label} className={`group relative px-3.5 py-2 transition-colors active:translate-y-px ${danger ? 'text-[#f0a090]' : 'text-console-text'}`}>
      <span className="link-g">{label}</span>
    </button>
  )
}

export default function SelectionBar() {
  const count = useApp((s) => s.selected.size)
  const readOnly = useApp((s) => !!s.listing?.archive)
  if (count === 0) return null
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-6 z-20 flex justify-center">
      <div key={count > 1 ? 'many' : 'one'} className="anim-pop pointer-events-auto flex items-center bg-console-bg pl-5 pr-2 text-console-text" style={{ borderRadius: 3, boxShadow: '0 18px 40px -16px rgb(0 0 0 / 0.7)' }}>
        <span className="mr-3 flex items-baseline gap-2">
          <span className="serif text-[16px] leading-none text-[#e9d9b2]">{count}</span>
          <span className="label !text-console-text/60">selected</span>
        </span>
        <span className="mr-1 h-5 w-px bg-white/15" />
        <Act label="Copy" keys="Ctrl+C" run={() => copySelection('copy')} />
        {!readOnly && <Act label="Cut" keys="Ctrl+X" run={() => copySelection('cut')} />}
        {!readOnly && count > 1 && <Act label="Rename" keys="F2" run={() => useApp.setState({ batchRename: selectedPaths() })} />}
        {!readOnly && count === 1 && <Act label="Rename" keys="F2" run={() => useApp.setState({ renaming: selectedPaths()[0] })} />}
        {!readOnly && <Act label="Trash" keys="Delete" danger run={() => void trashSelection()} />}
        <span className="mx-1 h-5 w-px bg-white/15" />
        <button className="px-3 py-2 text-console-text/60 transition-colors hover:text-console-text" onClick={() => selectOnly(null)} title="Clear selection (Esc)" aria-label="Clear selection">
          ✕
        </button>
      </div>
    </div>
  )
}
