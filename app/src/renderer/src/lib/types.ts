export type Entry = {
  name: string
  path: string
  is_dir: boolean
  is_link: boolean
  is_hidden: boolean
  size: number | null
  mtime: number
  ctime: number
  atime: number
  kind: string
  /** Icon and color group from fs/kinds.py: image, video, audio, archive, sheet, doc, code, app, data ... */
  kind_group?: string
  ext: string
  /** 1: the name and the content disagree. 2: a program with a document name. */
  kind_mismatch?: number | boolean
  sniffed_kind?: string | null
  kind_warning?: string | null
  child_count?: number | null
  dir_size?: number
  category?: string
  category_why?: string
  /** A system folder (PLAN2 P2-5): the list marks it. */
  system?: boolean
}

export type Listing = { path: string; parent: string | null; entries: Entry[]; writable: boolean; archive?: string }

export type Info = Entry & {
  /** Windows only: the ACL entries from icacls (section 9.13). */
  acl?: string[] | null
  mode: string
  mode_octal: string
  owner: string
  group: string
  mime: string
  inode: number
  target: string | null
  /** PLAN2 P2-5 */
  explain?: { text: string; level: string } | null
  tags?: string[]
  flags?: string
  nlink?: number
}

export type Place = { label: string; path: string; device?: string; total?: number; free?: number; protocol?: string; url?: string }
export type Places = {
  home: string
  user: string
  folders: Place[]
  drives: Place[]
  removable: Place[]
  network: Place[]
  trash: string
}

export type Journal = { undo: string | null; redo: string | null }

export type IndexProgress = {
  stage: 'idle' | 'metadata' | 'text' | 'meaning' | 'appearance'
  files: number
  scanned: number
  text_pending: number
  chunks: number
  embed_pending: number
  embed_ready: boolean
  embed_error: string | null
  embed_model: string
  /** Images, PDF pages and videos that still wait for the image stage (search by appearance). */
  visual_pending?: number
  visual_total?: number
  visual_current?: string | null
  speech_pending?: number
  roots: string[]
  chunks_per_file?: number | null
}

export type Segment = { t: string; hit: boolean }
export type Why = 'name' | 'keyword' | 'meaning' | 'filter'

export type SearchHit = {
  id: number
  path: string
  name: string
  parent: string
  is_dir: boolean
  size: number | null
  mtime: number
  kind: string
  title: string | null
  snippet: Segment[]
  why: Why
  page: number | null
  at?: number | null
  score: number
  ranks: Partial<Record<Why, number>>
  sim?: number | null
  positions?: number[]
}

export type Chip = { kind: 'size' | 'type' | 'date' | 'folder'; label: string; token: string }

export type SearchResponse = {
  query: string
  text: string
  chips: Chip[]
  mode: 'hybrid' | 'keyword'
  results: SearchHit[]
  timings: Record<string, number>
}

export type SpaceNode = {
  name: string
  path: string
  size: number
  is_dir: boolean
  cat: number
  files?: number
  dirs?: number
  cats?: number[]
  ages?: number[]
  mtime?: number
  atime?: number
  rest?: boolean
  ghost?: number
  children?: SpaceNode[]
  scan_root?: string
  scanned?: number
}

export type SpaceStatus = {
  scanning: boolean
  path: string | null
  files: number
  bytes: number
  roots: { path: string; size: number; files: number; finished: number; seconds: number }[]
  error: string | null
  dupes: DupeStatus
}

export type DupeStatus = {
  running: boolean
  path: string | null
  stage: string
  done: number
  total: number
  finished: number | null
  error: string | null
  groups: number
  wasted: number
}

export type DupeGroup = {
  hash: string
  /** "near": similar photos (pHash), not byte copies. */
  kind?: 'near'
  size: number
  count: number
  wasted: number
  files: { path: string; mtime: number; size: number; keep: boolean; reason: string; pixels?: string }[]
}

export type Suggestion = {
  id: string
  sentence: string
  bytes: number
  total: number
  reversible: boolean
  action: 'trash' | 'compress' | 'organize' | 'privacy' | 'name'
  method?: string
  methods?: string[]
  root?: string
  unit?: string[]
  items: { path: string; size: number; checked: boolean; note?: string; is_dir?: boolean }[]
}

export type CompressJob = {
  id: number
  mode: string
  method: string
  root: string
  label: string | null
  archive: string | null
  outputs: string[]
  files: number
  size_before: number
  size_after: number
  gain: number
  reversible: boolean
  status: 'planned' | 'running' | 'verifying' | 'done' | 'failed' | 'restored' | 'undone' | 'missing'
  error: string | null
  created: number
  restored: number | null
}

export type CompressSummary = { ghost: number; jobs: number; outputs: Record<string, number> }

export type AiMode = 'local' | 'hybrid' | 'ask'

export type PrivacyStatus = {
  online?: boolean | null
  ai_mode: AiMode
  /** Provider id: ready to use (PLAN4 4). */
  providers: Record<string, boolean>
  provider_names?: Record<string, string>
  dev?: boolean
  claude_account?: string
  local_model: string
  local_ready: boolean
  layer2: { available: boolean; error: string | null; load_ms: number | null }
  /** Fail closed (PLAN4 7.5): why nothing goes to the cloud now, or null. */
  cloud_blocked?: string | null
  /** The number of custom words to always hide (PLAN4 7.4). */
  words?: number
  today: { calls: number; bytes: number; pii_sent: number; mcp_calls?: number; mcp_bytes?: number }
}

export type LedgerRow = { id: number; ts: number; feature: string; tier: string; provider: string; bytes: number; payload: string; pii_count: number }

export type RouterResult = {
  tier: 'T2' | 'T3'
  provider: string
  model: string
  summary: string
  points: string[]
  redacted: number
  entities: { type: string; placeholder: string; layer: number }[]
  layer2: string | null
  ledger_id: number | null
  files: number
  ms: number
  fallback: string | null
}

export type ConsentRequest = { needs_consent: true; payload: string; provider: string; redacted: number; entities: { type: string; placeholder: string }[] }

export type AskSource = { n: number; path: string; name: string; page: number | null; at?: number | null; heading: string; why: string; snippet: string }

/** A match by appearance: a photo, a PDF page or a video moment (media/visual.py). */
export type VisualHit = {
  id: number
  path: string
  name: string
  parent: string
  size: number | null
  mtime: number
  kind: string
  match: 'image' | 'page' | 'frame'
  at: number | null
  thumb: string | null
  sim: number
  moments: number[]
}

export type AskSection = {
  id: number
  question: string
  think: boolean
  status: 'retrieving' | 'streaming' | 'done' | 'error' | 'consent'
  sources: AskSource[]
  text: string
  tier?: 'T2' | 'T3' | 'none'
  provider?: string | null
  model?: string | null
  reason?: string
  redacted?: number
  firstMs?: number | null
  ms?: number
  notFound?: boolean
  fallback?: string | null
  consent?: { payload: string; provider: string; redacted: number }
  error?: string
  started: number
}

export type FolderWhy = 'name' | 'disk' | 'project' | 'keyword' | 'summary' | 'content' | 'path' | 'connect'

export type FolderHit = {
  path: string
  name: string
  parent: string
  why: FolderWhy
  files: number | null
  size: number | null
  mtime: number
  summary: string | null
  project?: string | null
  top: { path: string; name: string; page: number | null }[]
  score?: number
  ranks?: Partial<Record<FolderWhy, number>>
  sim?: number | null
}

export type FolderResponse = {
  query: string
  mode: 'path' | 'describe'
  results: FolderHit[]
  timings: Record<string, number>
}

/** A name that exists in the target folder of a copy or a move (fs/jobs.py). */
export type Conflict = {
  src: string
  target: string
  name: string
  src_size: number
  src_mtime: number
  src_dir: boolean
  dst_size: number
  dst_mtime: number
  dst_dir: boolean
}
export type ConflictChoice = 'replace' | 'skip' | 'keep_both'

/** A job in the copy queue, with byte progress. */
export type CopyJob = {
  id: string
  kind: 'copy' | 'move'
  dst: string
  count: number
  srcs: string[]
  status: 'queued' | 'running' | 'paused' | 'done' | 'cancelled' | 'error'
  bytes_total: number
  bytes_done: number
  files_total: number
  files_done: number
  current: string | null
  error: string | null
  results: string[]
  skipped: number
  speed: number
  eta: number | null
}
