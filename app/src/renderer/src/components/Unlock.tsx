import { useEffect, useRef, useState } from 'react'
import { get, post } from '../lib/api'
import { Mark } from './TopBar'
import { Switch } from './ui'

/** The encrypted index is locked (secure.py): one screen, one field, one action (section 12.7). */
export default function Unlock() {
  const [pw, setPw] = useState('')
  const [remember, setRemember] = useState(false)
  const [keyring, setKeyring] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => {
    input.current?.focus()
    void get<{ keyring: boolean }>('/security').then((s) => setKeyring(s.keyring)).catch(() => {})
  }, [])
  const open = async (): Promise<void> => {
    if (!pw || busy) return
    setBusy(true)
    setErr(null)
    try {
      await post('/security/unlock', { passphrase: pw, remember })
      location.reload()
    } catch (e) {
      setErr((e as Error).message)
      setBusy(false)
      setPw('')
      input.current?.focus()
    }
  }
  return (
    <div className="flex h-full items-center justify-center bg-bg">
      <div className="w-[440px] max-w-[calc(100vw-32px)]">
        <div className="anim-row mb-5 flex items-center gap-3">
          <Mark size={20} />
          <span className="label">Raven</span>
        </div>
        <h1 className="serif anim-reveal text-[28px] leading-[0.95]">Your index is locked.</h1>
        <p className="anim-row mb-4 mt-3 text-muted" style={{ ['--i' as string]: 2 } as React.CSSProperties}>
          Type the passphrase to open it.
        </p>
        <input
          ref={input}
          type="password"
          value={pw}
          onChange={(e) => setPw(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void open()}
          placeholder="Passphrase"
          className={`field mb-5 w-full text-[18px] ${err ? 'border-danger' : ''}`}
        />
        {keyring && (
          <label className="mb-5 flex items-center gap-3 text-muted">
            <Switch on={remember} onChange={setRemember} label="Remember on this computer" /> Remember on this computer
          </label>
        )}
        {err && <p className="anim-pop mb-4 text-danger">{err}</p>}
        <button className="btn-primary !px-4 !py-1.5 disabled:opacity-40" disabled={!pw || busy} onClick={() => void open()}>
          {busy ? 'Opening…' : 'Open'}
        </button>
      </div>
    </div>
  )
}
