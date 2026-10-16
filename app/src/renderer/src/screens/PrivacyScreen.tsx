import { ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { EmptyState } from '../components/EmptyState'
import { del, get, on, post } from '../lib/api'
import { dateTime, size, tildify } from '../lib/format'
import type { AiMode, LedgerRow, PrivacyStatus } from '../lib/types'
import { refreshPrivacy, reveal, say, setAiMode, useApp } from '../store'
import { Ghost, Seg, Switch, stagger, useCountUp } from '../components/ui'

// The Privacy screen: one moving picture of what may leave, one switch, three numbers, and the details in tabs.

const MODES: { id: AiMode; label: string; line: string }[] = [
  { id: 'local', label: 'Local only', line: 'Nothing leaves this computer.' },
  { id: 'hybrid', label: 'Hybrid', line: 'Only redacted text leaves, for hard tasks.' },
  { id: 'ask', label: 'Ask each time', line: 'You see and approve every send.' }
]
const EXCLUDED = ['.ssh', '.gnupg', '.password-store', '.mozilla', '.thunderbird', 'node_modules', '.git', '.venv', '.cache', '.config']

const NOISE = '█▓▒░<>/#*%'

/** Three modes as three cells. The chosen cell has an accent edge and says what leaves the computer. */
function ModePicker({ mode }: { mode: AiMode }) {
  return (
    <div className="grid grid-cols-3 border border-line">
      {MODES.map((m, k) => {
        const on = mode === m.id
        return (
          <button
            key={m.id}
            onClick={() => void setAiMode(m.id)}
            className={`relative px-4 py-3 text-left transition-colors duration-200 ${k ? 'border-l border-line' : ''} ${on ? 'bg-accent-soft' : 'hover:bg-hover'}`}
          >
            <span className={`absolute inset-x-0 top-0 h-[2px] transition-colors ${on ? 'bg-accent' : 'bg-transparent'}`} />
            <div className={`text-[14px] ${on ? 'text-accent' : 'text-text'}`}>{m.label}</div>
            <div className="mt-1 text-[12px] text-muted">{m.line}</div>
          </button>
        )
      })}
    </div>
  )
}

/** The path a cloud call takes, in one line: what the gate does in this mode. */
function Path({ mode }: { mode: AiMode }) {
  return (
    <div className="mt-4 flex items-center gap-3 text-[12.5px]">
      <span>Your files</span>
      <span className="h-px w-8 bg-line" />
      <span className="flex items-center gap-1.5 text-accent">
        <ShieldCheck size={14} strokeWidth={1.6} /> Redaction gate
      </span>
      <span className={`h-px w-8 ${mode === 'local' ? 'border-t border-dashed border-muted/60' : 'bg-line'}`} />
      <span className={mode === 'local' ? 'text-muted line-through' : ''}>Cloud</span>
    </div>
  )
}

/** One number that counts up, with a small word under it. */
function Stat({ value, label, fmt }: { value: number; label: string; fmt?: (n: number) => string }) {
  const v = useCountUp(value, 900)
  return (
    <div className="border-l border-line pl-4 first:border-l-0 first:pl-0">
      <div className="serif text-[24px] leading-none tabular-nums">{fmt ? fmt(v) : Math.round(v).toLocaleString()}</div>
      <div className="mt-1.5 text-[11.5px] text-muted">{label}</div>
    </div>
  )
}

/** What works now: one row per part. A problem shows its fix, because that is the one thing to act on. */
function Ready({ p }: { p: PrivacyStatus }) {
  const cloud = p.providers.claude || p.providers.groq || !!p.providers.anthropic
  const rows: [boolean, string, string][] = [
    [p.local_ready, 'Local model', p.local_ready ? p.local_model : `run: ollama pull ${p.local_model}`],
    [!p.layer2.error && p.layer2.available, 'Privacy Filter', p.layer2.error ?? (p.layer2.available ? 'PII layer 2 on the CPU' : 'not downloaded: patterns only')],
    [cloud, 'Cloud model', cloud ? [p.providers.claude && 'claude -p', p.providers.groq && 'Groq', p.providers.anthropic && 'Anthropic'].filter(Boolean).join(', ') : 'none: add a key in Settings']
  ]
  return (
    <div className="divide-y divide-line">
      {rows.map(([ok, name, detail]) => (
        <div key={name} className="flex items-center gap-3 py-2 text-[12.5px]">
          <span className={`h-[7px] w-[7px] ${ok ? 'bg-ok' : 'bg-danger'}`} />
          <span className="w-32 shrink-0 whitespace-nowrap">{name}</span>
          <span className={`mono truncate text-[11.5px] ${ok ? 'text-muted' : 'text-danger'}`}>{detail}</span>
        </div>
      ))}
      {p.online === false && <div className="py-2 text-[12px] text-accent">Offline: Raven answers on this computer only.</div>}
    </div>
  )
}

function Ledger({ rows }: { rows: LedgerRow[] }) {
  const [open, setOpen] = useState<number | null>(null)
  const exportJson = async (): Promise<void> => {
    const r = await get<{ json: string }>('/privacy/ledger/export')
    const url = URL.createObjectURL(new Blob([r.json], { type: 'application/json' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `raven-ledger-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(url)
  }
  if (!rows.length) return <EmptyState art="search" title="Nothing has left this computer" />
  return (
    <div>
      <div className="mb-2 flex justify-end">
        <Ghost tone="plain" onClick={() => void exportJson()}>
          Export JSON
        </Ghost>
      </div>
      {rows.map((r, k) => (
        <div key={r.id} style={stagger(k)} className="anim-row border-b border-line">
          <button className="group grid w-full grid-cols-[140px_minmax(0,1fr)_80px_110px] items-baseline gap-4 py-2.5 text-left transition-colors hover:bg-hover" onClick={() => setOpen(open === r.id ? null : r.id)}>
            <span className="mono text-[11.5px] text-muted">{dateTime(r.ts)}</span>
            <span className="truncate transition-transform duration-200 group-hover:translate-x-1">
              {r.feature} <span className="text-muted">· {r.provider}</span>
            </span>
            <span className="mono text-right text-[12px]">{size(r.bytes)}</span>
            <span className={`text-right text-[12px] ${r.pii_count ? 'text-ok' : 'text-muted'}`}>{r.pii_count} hidden</span>
          </button>
          <div className={`grid transition-[grid-template-rows] duration-500 ease-[cubic-bezier(0.3,0.8,0.2,1)] ${open === r.id ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
            <div className="min-h-0 overflow-hidden">
              <pre className="selectable mb-3 max-h-[50vh] overflow-auto whitespace-pre-wrap bg-console-bg p-4 font-mono text-[11.5px] text-console-text" style={{ borderRadius: 3 }}>
                {r.payload}
              </pre>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

type Sensitive = { path: string; name: string; size: number; mtime: number; level: number; types: Record<string, number> }

function SensitiveFiles() {
  const [files, setFiles] = useState<Sensitive[] | null>(null)
  const home = useApp((s) => s.places?.home ?? '')
  useEffect(() => {
    void get<{ files: Sensitive[] }>('/privacy/sensitive').then((r) => setFiles(r.files))
  }, [])
  if (!files) return null
  return (
    <div>
      {!files.length && <EmptyState art="search" title="No personal data found" />}
      {files.map((f, k) => (
        <div
          key={f.path}
          style={stagger(k)}
          onDoubleClick={() => reveal(f.path)}
          title={`${tildify(f.path, home)} · double-click to show`}
          className="anim-row group flex items-baseline gap-3 border-b border-line py-2.5 transition-colors hover:bg-hover"
        >
          <span className={`h-[6px] w-[6px] shrink-0 self-center rounded-full ${f.level > 1 ? 'bg-danger' : 'bg-accent'}`} />
          <span className="truncate transition-transform duration-200 group-hover:translate-x-1">{f.name}</span>
          <span className="mono ml-auto shrink-0 text-[11px] text-muted">{Object.keys(f.types).map((t) => t.toLowerCase()).join(' · ')}</span>
        </div>
      ))}
      <p className="mono mt-4 text-[11px] text-muted">Never read: {EXCLUDED.join(' · ')}</p>
    </div>
  )
}

/** Characters that settle into the placeholder, left to right. */
function Scramble({ text, delay }: { text: string; delay: number }) {
  const [s, setS] = useState(() => text.replace(/./g, '░'))
  useEffect(() => {
    let raf = 0
    const start = performance.now() + delay
    const tick = (t: number): void => {
      const p = (t - start) / 420
      if (p >= 1) return setS(text)
      const n = Math.max(0, Math.floor(p * text.length))
      setS(text.slice(0, n) + Array.from({ length: text.length - n }, () => NOISE[(Math.random() * NOISE.length) | 0]).join(''))
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [text, delay])
  return <>{s}</>
}

type Redacted = { text: string; count: number; ms: number; blocked: boolean; reason: string }

function TryIt() {
  const [text, setText] = useState('Invoice for Rahul Sharma, A/C 50100234567890, rahul.s@gmail.com\nPAN ABCPN1234K, phone +91 98765 43210')
  const [res, setRes] = useState<Redacted | null>(null)
  const [busy, setBusy] = useState(false)
  const [run, setRun] = useState(0)
  const go = async (): Promise<void> => {
    setBusy(true)
    try {
      setRes(await post<Redacted>('/privacy/redact', { text }))
      setRun((n) => n + 1)
    } finally {
      setBusy(false)
    }
  }
  const parts = res ? res.text.split(/(\[[A-Z]+_\d+\])/) : []
  let ph = 0
  return (
    <div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={3}
        spellCheck={false}
        className="field mono w-full resize-none text-[12.5px]"
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) void go()
          e.stopPropagation()
        }}
      />
      <div className="mt-3 flex items-center gap-4">
        <button className="btn-primary" onClick={() => void go()} disabled={busy}>
          {busy ? 'Redacting…' : 'Redact'}
        </button>
        {res && (
          <span key={run} className="anim-fade text-[12px] text-muted">
            {res.blocked ? <span className="text-danger">Blocked: {res.reason}</span> : `${res.count} hidden in ${res.ms} ms`}
          </span>
        )}
      </div>
      {res && (
        <pre key={run} className="selectable anim-pop mt-4 whitespace-pre-wrap bg-console-bg p-4 font-mono text-[12.5px] leading-relaxed text-console-text" style={{ borderRadius: 4 }}>
          {parts.map((s, k) =>
            /^\[[A-Z]+_\d+\]$/.test(s) ? (
              <span key={k} className="rounded-sm bg-accent/20 px-0.5 text-accent">
                <Scramble text={s} delay={200 + ph++ * 140} />
              </span>
            ) : (
              <span key={k}>{s}</span>
            )
          )}
        </pre>
      )}
    </div>
  )
}

type Learn = { enabled: boolean; keep_days: number; counts: Record<string, number>; moves: number; top_folders: { folder: string }[] }

/** Habits: what Raven learned, on this computer only. */
function Learned() {
  const [l, setL] = useState<Learn | null>(null)
  const load = (): void => void get<Learn>('/habits/summary').then(setL).catch(() => {})
  useEffect(load, [])
  if (!l) return null
  const n = (k: string): number => l.counts[k] ?? 0
  return (
    <div>
      <div className="flex items-center gap-3">
        <Switch on={l.enabled} onChange={(v) => void post('/settings', { key: 'habits', value: v }).then(load)} label="Learn from how I use files" />
        <span>Learn from how I use files</span>
        <span className="text-[12px] text-muted">on this computer · {l.keep_days} days</span>
        <button className="link ml-auto text-[12px] text-danger" onClick={() => void del<Learn>('/habits').then((r) => (setL(r), say('Raven forgot what it learned.')))}>
          Forget all
        </button>
      </div>
      <div className="mt-8 grid grid-cols-3">
        <Stat value={n('open')} label="opens" />
        <Stat value={n('search')} label="searches" />
        <Stat value={l.moves} label="moves" />
      </div>
      {l.top_folders.length > 0 && (
        <p className="mt-6 text-center text-[12px] text-muted">
          You use {l.top_folders.slice(0, 3).map((f) => f.folder.slice(f.folder.lastIndexOf('/') + 1)).join(', ')} most.
        </p>
      )}
    </div>
  )
}

type Tab = 'ledger' | 'sensitive' | 'try' | 'learned'

export default function PrivacyScreen() {
  const p = useApp((s) => s.privacy)
  const mode = p?.ai_mode ?? 'hybrid'
  const [tab, setTab] = useState<Tab>('ledger')
  const [rows, setRows] = useState<LedgerRow[]>([])
  useEffect(() => {
    const load = (): void => {
      void refreshPrivacy()
      void get<{ rows: LedgerRow[] }>('/privacy/ledger').then((r) => setRows(r.rows)).catch(() => {})
    }
    load()
    return on('ledger', load)
  }, [])
  const since = Date.now() - 14 * 86400000
  const recent = rows.filter((r) => r.ts >= since)
  
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="px-5 pb-10 pt-5">
        {/* Both panels take the height of the taller one. The mode cards grow, so the left panel has no empty band. */}
        <div className="grid grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] items-stretch gap-5">
          <div className="panel ticks flex flex-col px-6 py-5">
            <div className="label mb-3">
              <span className="sec-no">01</span>AI mode
            </div>
            <div className="flex flex-1 flex-col [&>div]:flex-1">
              <ModePicker mode={mode} />
            </div>
            <Path mode={mode} />
          </div>
          <div className="panel px-6 py-5">
            <div className="label mb-3">
              <span className="sec-no">02</span>Last 14 days
            </div>
            <div className="grid grid-cols-3">
              <Stat value={recent.reduce((a, r) => a + r.bytes, 0)} label="sent to the cloud" fmt={(v) => size(Math.round(v)) || '0 B'} />
              <Stat value={recent.length} label="cloud calls" />
              <Stat value={recent.reduce((a, r) => a + r.pii_count, 0)} label="private items hidden" />
            </div>
            {p && (
              <div className="mt-4">
                <Ready p={p} />
              </div>
            )}
          </div>
        </div>

        <div className="mt-8">
          <Seg
            options={[
              ['ledger', 'Ledger'],
              ['sensitive', 'Sensitive files'],
              ['try', 'Try redaction'],
              ['learned', 'Learned']
            ]}
            value={tab}
            onChange={setTab}
          />
        </div>
        <div className="h-px bg-line" />
        <div key={tab} className="anim-fade mt-5">
          {tab === 'ledger' && <Ledger rows={rows} />}
          {tab === 'sensitive' && <SensitiveFiles />}
          {tab === 'try' && <TryIt />}
          {tab === 'learned' && <Learned />}
        </div>
      </div>
    </div>
  )
}
