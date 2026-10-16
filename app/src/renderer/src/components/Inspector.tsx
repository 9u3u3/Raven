import { useEffect, useRef, useState } from 'react'
import { get, rawUrl, visualThumbUrl } from '../lib/api'
import { clock, dateTime, size } from '../lib/format'
import type { Info } from '../lib/types'
import { activity, moveTo, setPref, useApp } from '../store'
import { AUDIO_EXTS, FileIcon, IMAGE_EXTS, TEXT_EXTS, VIDEO_EXTS } from './FileIcon'
import { EmptyState } from './EmptyState'
import { BulkProps, Explain, Lock, OwnerEditor, PermEditor, Tags, Uses } from './Props'
import FileGraph from './FileGraph'
import { ResizeEdge } from './ui'

/** Video or audio with the moments Raven found: keyframes under a video, then the timed transcript. */
function Media({ info, video }: { info: Info; video: boolean }) {
  const ref = useRef<HTMLVideoElement & HTMLAudioElement>(null)
  const at = useApp((s) => (s.mediaAt?.path === info.path ? s.mediaAt.at : null))
  const [moments, setMoments] = useState<{ at: number; thumb: string }[]>([])
  const [parts, setParts] = useState<{ at: number; text: string }[]>([])
  const [now, setNow] = useState(0)

  useEffect(() => {
    const ac = new AbortController()
    setMoments([])
    setParts([])
    if (video) get<{ moments: { at: number; thumb: string }[] }>('/visual/moments', { path: info.path }, ac.signal).then((r) => setMoments(r.moments)).catch(() => {})
    get<{ parts: { at: number; text: string }[] }>('/transcribe/text', { path: info.path }, ac.signal).then((r) => setParts(r.parts)).catch(() => {})
    return () => ac.abort()
  }, [info.path, info.mtime, video])

  useEffect(() => {
    const el = ref.current
    if (el && at != null && el.readyState >= 1) el.currentTime = at
  }, [at])

  const seek = (t: number): void => {
    const el = ref.current
    if (!el) return
    el.currentTime = t
    void el.play().catch(() => {})
  }
  const props = {
    ref,
    src: rawUrl(info.path),
    controls: true,
    onLoadedMetadata: () => {
      if (at != null && ref.current) ref.current.currentTime = at
    },
    onTimeUpdate: () => setNow(ref.current?.currentTime ?? 0)
  }
  return (
    <div>
      {video ? <video {...props} className="max-h-[45vh] w-full bg-black" /> : <audio {...props} className="w-full" />}
      {moments.length > 1 && (
        <div className="flex gap-1 overflow-x-auto border-b border-line px-2 py-2">
          {moments.map((m) => (
            <button key={m.at} title={clock(m.at)} onClick={() => seek(m.at)} className={`shrink-0 ${at != null && Math.abs(m.at - at) < 0.5 ? 'outline outline-2 outline-accent' : ''}`}>
              <img src={visualThumbUrl(m.thumb)} className="h-10 w-auto" alt="" draggable={false} />
              <div className="mono text-center text-[10px] text-muted">{clock(m.at)}</div>
            </button>
          ))}
        </div>
      )}
      {parts.length > 0 && (
        <div className="max-h-[26vh] overflow-y-auto border-b border-line px-5 py-2">
          <div className="label pb-1">Transcript</div>
          {parts.map((p, i) => {
            const end = parts[i + 1]?.at ?? Infinity
            const live = now >= p.at && now < end
            return (
              <p key={p.at} onClick={() => seek(p.at)} className={`selectable cursor-default py-0.5 text-[12.5px] ${live ? 'text-text' : 'text-muted'} hover:text-text`}>
                <span className="mono mr-2 text-[11px] text-accent">{clock(p.at)}</span>
                {p.text}
              </p>
            )
          })}
        </div>
      )}
    </div>
  )
}

function Preview({ info }: { info: Info }) {
  const ext = info.ext
  const [text, setText] = useState<{ text: string; truncated: boolean } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const pdfPage = useApp((s) => (s.pdfPage?.path === info.path ? s.pdfPage.page : null))
  const isText = !info.is_dir && (TEXT_EXTS.has(ext) || info.mime.startsWith('text/')) && (info.size ?? 0) < 50 * 1024 * 1024

  useEffect(() => {
    setText(null)
    setErr(null)
    if (!isText) return
    const ac = new AbortController()
    get<{ text: string; truncated: boolean }>('/fs/text', { path: info.path, limit: 128 * 1024 }, ac.signal)
      .then(setText)
      .catch((e) => e.name !== 'AbortError' && setErr(e.message))
    return () => ac.abort()
  }, [info.path, info.mtime, isText])

  if (info.is_dir)
    return (
      <div className="flex h-40 items-center justify-center">
        <FileIcon ext="" isDir size={20} />
      </div>
    )
  if (IMAGE_EXTS.has(ext))
    return (
      <div className="mx-5 mt-5 flex max-h-[45vh] items-center justify-center overflow-hidden bg-hover">
        <img src={rawUrl(info.path)} className="max-h-[45vh] max-w-full object-contain" alt="" draggable={false} />
      </div>
    )
  if (ext === 'pdf')
    return (
      <iframe
        key={pdfPage ?? 0}
        src={`${rawUrl(info.path)}#toolbar=0&navpanes=0${pdfPage ? `&page=${pdfPage}` : ''}`} className="h-[52vh] w-full border-0 bg-white"
        title="PDF preview"
      />
    )
  if (VIDEO_EXTS.has(ext)) return <Media info={info} video />
  if (AUDIO_EXTS.has(ext)) return <Media info={info} video={false} />
  if (isText) {
    if (err) return <div className="py-6 text-muted">No text preview: {err}</div>
    return (
      <pre className="mono selectable mx-5 mt-5 max-h-[52vh] overflow-auto whitespace-pre-wrap break-words border border-line p-3 text-[11.5px] leading-snug">
        {text ? text.text : ' '}
        {text?.truncated && <span className="text-muted">{'\n'}… preview shows the first 128 KB</span>}
      </pre>
    )
  }
  return (
    <div className="flex h-40 items-center justify-center">
      <FileIcon ext={ext} isDir={false} size={20} />
    </div>
  )
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  if (v === null || v === undefined || v === '') return null
  return (
    <div className="grid grid-cols-[84px_minmax(0,1fr)] gap-3 border-b border-line/70 py-[7px]">
      <span className="label pt-px">{k}</span>
      <span className="mono selectable break-all text-[12px]">{v}</span>
    </div>
  )
}

/** SHA-256 and BLAKE3 on request (section 6.1 Properties). */
function Checksums({ path }: { path: string }) {
  const [h, setH] = useState<{ sha256: string; blake3: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    setH(null)
    setErr(null)
    setBusy(false)
  }, [path])
  if (h)
    return (
      <>
        <Row k="SHA-256" v={h.sha256} />
        <Row k="BLAKE3" v={h.blake3} />
      </>
    )
  return (
    <div className="grid grid-cols-[88px_minmax(0,1fr)] gap-3 py-[3px]">
      <span className="label pt-px">Checksum</span>
      {err ? (
        <span className="text-[12px] text-danger">{err}</span>
      ) : (
        <button
          className="btn-text justify-self-start text-[12px] text-accent"
          disabled={busy}
          onClick={() => {
            setBusy(true)
            get<{ sha256: string; blake3: string }>('/fs/hash', { path })
              .then(setH)
              .catch((e) => setErr(e.message))
              .finally(() => setBusy(false))
          }}
        >
          {busy ? 'computing…' : 'Compute SHA-256 and BLAKE3'}
        </button>
      )}
    </div>
  )
}

/** Habits (PLAN2 P2-8.3): the folder that files like this one went to before, with one click. */
function Predict({ path }: { path: string }) {
  const [p, setP] = useState<{ folder: string; why: string } | null>(null)
  useEffect(() => {
    setP(null)
    const ac = new AbortController()
    get<{ predict: { folder: string; why: string } | null }>('/habits/predict', { path }, ac.signal).then((r) => setP(r.predict)).catch(() => {})
    return () => ac.abort()
  }, [path])
  if (!p || p.folder === path.slice(0, path.lastIndexOf('/'))) return null
  return (
    <div className="mb-2 flex items-center gap-2 text-[12px]" title={p.why}>
      <span className="text-muted">Usually goes to</span>
      <span className="truncate">{p.folder.slice(p.folder.lastIndexOf('/') + 1)}</span>
      <button className="link ml-auto text-accent" onClick={() => void moveTo(path, p.folder)}>
        Move
      </button>
    </div>
  )
}

type Description = { text?: string; source?: string; category?: string | null; reason?: string }
const SOURCE_WORD: Record<string, string> = { summary: 'folder summary', contents: 'what it holds', speech: 'what is said', appearance: 'what it shows', text: 'from the text', privacy: 'private' }

/** A short description from the index: a folder summary, the first words said, what a photo shows, the start of a text. */
function Describe({ path, stamp }: { path: string; stamp: number }) {
  const [d, setD] = useState<Description | null>(null)
  useEffect(() => {
    const ac = new AbortController()
    setD(null)
    get<Description>('/describe', { path }, ac.signal).then(setD).catch(() => {})
    return () => ac.abort()
  }, [path, stamp])
  if (!d || (!d.text && !d.category)) return null
  return (
    <div className="anim-fade mt-2.5">
      {d.text && <p className="selectable text-[12.5px] leading-[1.5] text-text/80">{d.text}</p>}
      <div className="mono mt-1.5 flex flex-wrap gap-x-3 text-[10.5px] text-muted">
        {d.category && <span title={d.reason} className="text-accent">{d.category}</span>}
        {d.text && d.source && <span>{SOURCE_WORD[d.source] ?? d.source}</span>}
      </div>
    </div>
  )
}

export default function Inspector() {
  const width = useApp((s) => s.inspectorWidth)
  const selected = useApp((s) => s.selected)
  const cursor = useApp((s) => s.cursor)
  const listing = useApp((s) => s.listing)
  const [info, setInfo] = useState<Info | null>(null)
  const target = cursor && selected.has(cursor) ? cursor : selected.size === 1 ? [...selected][0] : null
  const entry = listing?.entries.find((e) => e.path === target)

  useEffect(() => {
    if (!target) {
      setInfo(null)
      return
    }
    const ac = new AbortController()
    get<Info>('/fs/info', { path: target }, ac.signal)
      .then(setInfo)
      .catch(() => setInfo(null))
    // Habits: a file you look at for 1.5 s counts as a preview.
    const t = setTimeout(() => activity('preview', target), 1500)
    return () => (ac.abort(), clearTimeout(t))
  }, [target, entry?.mtime])

  return (
    <aside data-tour="inspector" className="anim-slide relative flex shrink-0 flex-col border-l border-line" style={{ width }}>
      <ResizeEdge
        side="left"
        get={() => useApp.getState().inspectorWidth}
        onWidth={(w) => useApp.setState({ inspectorWidth: w })}
        onDone={(w) => setPref('inspectorWidth', w)}
        min={260}
        max={760}
        reset={340}
      />
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      {!target && selected.size > 1 && <BulkProps paths={[...selected]} />}
      {!target && selected.size <= 1 && <EmptyState art="search" title="Nothing selected" />}
      {target && info && info.path === target && (
        <>
          <Preview info={info} />
          <div className="p-4">
            <div className="mb-5">
              <FileIcon ext={info.name.includes('.') ? info.name.slice(info.name.lastIndexOf('.') + 1).toLowerCase() : ''} isDir={info.is_dir} group={info.kind_group} size={20} />
              <div className="serif selectable mt-2 min-w-0 break-words text-[16px] leading-[1.1]">{info.name}</div>
              <Describe path={info.path} stamp={info.mtime} />
              <div className="anim-rule mt-3 h-px bg-line" />
            </div>
            <Explain e={info.explain} />
            {!info.is_dir && <Predict path={info.path} />}
            {!info.is_dir && <FileGraph local={info.path} height="200px" compact />}
            <Row k="Kind" v={info.kind_mismatch ? `${info.kind} · content: ${info.sniffed_kind}` : info.kind} />
            {info.kind_warning && <div className="mb-3 text-[12px] text-danger">{info.kind_warning}</div>}
            <Row k="Size" v={info.is_dir ? (info.child_count != null ? `${info.child_count} items` : '') : `${size(info.size)} (${(info.size ?? 0).toLocaleString()} bytes)`} />
            <Row k="Modified" v={dateTime(info.mtime)} />
            <Row k="Created" v={dateTime(info.ctime)} />
            <Row k="Opened" v={dateTime(info.atime)} />
            <Row k="Path" v={info.path} />
            <Row k="Link to" v={info.target} />
            <Row k="MIME" v={info.mime} />
            {(info.nlink ?? 1) > 1 && !info.is_dir && <Row k="Links" v={`${info.nlink} names share this file`} />}
            {info.mode && !info.is_link && (
              <div className="mb-2">
                <div className="label">Permissions · {info.mode}</div>
                <PermEditor paths={[info.path]} octal={info.mode_octal.replace('0o', '')} isDir={info.is_dir} />
              </div>
            )}
            {info.owner && (
              <div className="mb-2">
                <div className="label mb-0.5">Owner</div>
                <OwnerEditor paths={[info.path]} owner={info.owner} group={info.group} />
              </div>
            )}
            {info.acl && info.acl.length > 0 && <Row k="ACL" v={info.acl.join('\n')} />}
            <div className="mb-2">
              <div className="label mb-0.5">Tags</div>
              <Tags paths={[info.path]} tags={info.tags ?? []} />
            </div>
            <div className="mb-2 flex items-center gap-4">
              <Uses path={info.path} />
              <Lock path={info.path} flags={info.flags ?? ''} />
            </div>
            {!info.is_dir && <Checksums path={info.path} />}
          </div>
        </>
      )}
      </div>
    </aside>
  )
}
