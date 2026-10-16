import { useApp } from '../store'

// Section 5.2 rule 10: real numbers on screen during development and in the demo. Ctrl+Shift+D.
const ROWS: [string, string, number][] = [
  ['folder', 'folder open', 150],
  ['fuzzy', 'name (worker)', 30],
  ['keyword', 'name + BM25', 100],
  ['hybrid', 'hybrid round trip', 300],
  ['hybrid_server', '  server total', 300],
  ['embed', '  query embedding', 200],
  ['dense', '  vector scan', 100]
]

export default function LatencyOverlay() {
  const on = useApp((s) => s.overlay)
  const lat = useApp((s) => s.latency)
  if (!on) return null
  return (
    <div className="mono anim-pop fixed bottom-12 right-4 z-50 w-64 bg-console-bg p-4 text-[11px] text-console-text">
      <div className="mb-2 uppercase tracking-widest opacity-70">latency · ms</div>
      {ROWS.map(([k, label, budget]) => (
        <div key={k} className="flex justify-between">
          <span className="whitespace-pre">{label}</span>
          <span style={{ color: lat[k] !== undefined && lat[k] > budget ? '#FF6B5E' : undefined }}>
            {lat[k] !== undefined ? lat[k].toFixed(1) : '—'}
            <span className="opacity-50"> /{budget}</span>
          </span>
        </div>
      ))}
    </div>
  )
}
