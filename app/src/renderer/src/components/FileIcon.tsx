import {
  Box,
  Database,
  File,
  FileClock,
  FileKey,
  HardDrive,
  Link2,
  Package,
  Type,
  FileArchive,
  FileAudio,
  FileCode,
  FileImage,
  FileSpreadsheet,
  FileText,
  FileVideo,
  Folder,
  FolderOpen,
  FolderSymlink,
  type LucideIcon
} from 'lucide-react'

const GROUPS: [LucideIcon, string[]][] = [
  [FileImage, ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tif', 'tiff', 'svg', 'heic', 'avif', 'jxl', 'ico']],
  [FileVideo, ['mp4', 'mkv', 'webm', 'mov', 'avi']],
  [FileAudio, ['mp3', 'wav', 'flac', 'ogg', 'm4a', 'opus']],
  [FileArchive, ['zip', '7z', 'tar', 'gz', 'zst', 'xz', 'bz2', 'rar', 'deb', 'rpm', 'appimage', 'iso']],
  [FileSpreadsheet, ['csv', 'tsv', 'xls', 'xlsx', 'ods']],
  [FileText, ['pdf', 'doc', 'docx', 'odt', 'rtf', 'txt', 'md', 'markdown', 'rst', 'epub', 'log', 'ppt', 'pptx', 'odp']],
  [
    FileCode,
    ['py', 'js', 'ts', 'tsx', 'jsx', 'c', 'h', 'cpp', 'hpp', 'java', 'go', 'rs', 'rb', 'php', 'sh', 'sql', 'html', 'css', 'json', 'yaml', 'yml', 'toml', 'xml', 'kt', 'dart', 'lua']
  ]
]

// The sidecar sends a group with each entry (fs/kinds.py). It wins over the extension lists above.
const GROUP_ICON: Record<string, LucideIcon> = {
  image: FileImage, video: FileVideo, audio: FileAudio, archive: FileArchive, sheet: FileSpreadsheet,
  doc: FileText, log: FileText, code: FileCode, app: Package, disk: HardDrive, data: Database, font: Type,
  model3d: Box, secret: FileKey, link: Link2, partial: FileClock
}

export function iconFor(ext: string, isDir: boolean, isLink = false, group?: string): LucideIcon {
  if (isDir) return isLink ? FolderSymlink : Folder
  if (group && GROUP_ICON[group]) return GROUP_ICON[group]
  for (const [icon, exts] of GROUPS) if (exts.includes(ext)) return icon
  return File
}

// One muted color per kind of file, so a folder full of mixed files reads at a glance.
const TONE = new Map<LucideIcon, string>([
  [FileImage, 'text-k-image'],
  [FileVideo, 'text-k-video'],
  [FileAudio, 'text-k-audio'],
  [FileArchive, 'text-k-archive'],
  [FileSpreadsheet, 'text-k-sheet'],
  [FileText, 'text-k-doc'],
  [FileCode, 'text-k-code'],
  [Folder, 'text-k-folder'],
  [FolderSymlink, 'text-k-folder'],
  [Package, 'text-k-archive'],
  [HardDrive, 'text-k-archive'],
  [Database, 'text-k-sheet'],
  [FileKey, 'text-danger'],
  [Type, 'text-k-doc'],
  [Box, 'text-k-image']
])

/** The color variable for a kind of file, for small charts. */
export function kindVar(ext: string, isDir: boolean, group?: string): string {
  const cls = TONE.get(iconFor(ext, isDir, false, group))
  return cls ? `var(--${cls.replace('text-', '')})` : 'var(--muted)'
}

export function FileIcon({ ext, isDir, isLink, group, size = 16 }: { ext: string; isDir: boolean; isLink?: boolean; group?: string; size?: number }) {
  const Icon = iconFor(ext, isDir, isLink, group)
  const tone = TONE.get(Icon) ?? 'text-muted'
  // A folder opens when you point at its row (the row carries the "group" class).
  if (isDir && !isLink)
    return (
      <span className={`relative inline-block shrink-0 ${tone}`} style={{ width: size, height: size }} aria-hidden>
        <Folder size={size} strokeWidth={1.5} fill="currentColor" fillOpacity={0.16} className="absolute inset-0 transition-all duration-200 group-hover:scale-90 group-hover:opacity-0" />
        <FolderOpen size={size} strokeWidth={1.5} fill="currentColor" fillOpacity={0.22} className="absolute inset-0 scale-90 opacity-0 transition-all duration-200 group-hover:scale-100 group-hover:opacity-100" />
      </span>
    )
  return <Icon size={size} strokeWidth={1.5} className={`shrink-0 ${tone}`} fill={isDir ? 'currentColor' : 'none'} fillOpacity={isDir ? 0.16 : 0} aria-hidden />
}

export const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'avif', 'ico'])
export const THUMB_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif', 'tif', 'tiff', 'pdf'])
export const VIDEO_EXTS = new Set(['mp4', 'webm', 'mkv', 'mov', 'ogv'])
export const AUDIO_EXTS = new Set(['mp3', 'wav', 'flac', 'ogg', 'm4a', 'opus', 'aac'])
export const TEXT_EXTS = new Set([
  'txt', 'md', 'markdown', 'rst', 'csv', 'tsv', 'json', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf', 'log', 'py', 'js', 'ts', 'tsx',
  'jsx', 'c', 'h', 'cpp', 'hpp', 'java', 'kt', 'go', 'rs', 'rb', 'php', 'sh', 'bash', 'zsh', 'sql', 'html', 'htm', 'css', 'scss',
  'xml', 'tex', 'lua', 'dart', 'swift', 'r', 'cs', 'vue', 'svelte', 'gitignore', 'env', 'service', 'desktop', ''
])
