import { AlertCircle, RotateCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useApp } from '../store'
import { Mark } from './TopBar'

/**
 * The start screen: Raven core (the sidecar) starts, opens the index and the models. A large index can take a
 * minute. When the start fails, the error and a Restart button show here, not only in the status line.
 */
export default function Boot() {
  const error = useApp((s) => s.bootError)
  const [s, setS] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setS((x) => x + 1), 1000)
    return () => clearInterval(t)
  }, [])
  return (
    <div className="flex h-full items-center justify-center bg-bg">
      <div className="anim-pop w-[460px] max-w-[calc(100vw-32px)] text-center">
        <div className="relative mx-auto mb-5 grid h-20 w-20 place-content-center">
          <span className="anim-float">
            <Mark size={20} />
          </span>
        </div>
        <h1 className="serif anim-reveal mb-1 text-[28px] leading-none">Raven</h1>
        <p className="mb-6 text-muted">{error ? 'Raven core did not start.' : 'Starting Raven core, and opening your index'}</p>
        {!error && (
          <>
            <div className="relative mx-auto mb-3 h-px w-56 overflow-hidden bg-line">
              <span className="absolute inset-y-0 w-1/3 bg-accent" style={{ animation: 'rv-scan 1.2s ease-in-out infinite' }} />
            </div>
            <div className="mono text-[11px] text-muted">{s} s</div>
            {s > 15 && <p className="anim-fade mt-4 text-[12.5px] text-muted">A large index can take up to a minute to open.</p>}
          </>
        )}
        {error && (
          <div className="anim-pop mt-2 border-l-2 border-danger py-1 pl-4 text-left">
            <div className="mb-2 flex items-center gap-2 font-medium text-danger">
              <AlertCircle size={17} /> {error}
            </div>
            <p className="mb-4 text-[12.5px] text-muted">See the terminal for the log. Close other copies of Raven.</p>
            <button
              className="btn-primary flex items-center gap-2"
              onClick={() => (useApp.setState({ bootError: null }), void window.raven?.restartSidecar().then(() => location.reload()))}
            >
              <RotateCw size={14} /> Restart
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
