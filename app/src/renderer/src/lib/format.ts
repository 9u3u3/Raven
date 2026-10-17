const UNITS = ['B', 'KB', 'MB', 'GB', 'TB']

export function size(n: number | null | undefined): string {
  if (n === null || n === undefined) return ''
  if (n < 1024) return `${n} B`
  let i = 0
  let v = n
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024
    i++
  }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${UNITS[i]}`
}

/** Seconds as m:ss or h:mm:ss, for video and audio moments. */
export function clock(t: number): string {
  const s = Math.floor(t)
  const mm = String(Math.floor((s % 3600) / 60))
  const ss = String(s % 60).padStart(2, '0')
  return s >= 3600 ? `${Math.floor(s / 3600)}:${mm.padStart(2, '0')}:${ss}` : `${mm}:${ss}`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function date(ms: number): string {
  if (!ms) return ''
  const d = new Date(ms)
  const now = new Date()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (d.toDateString() === now.toDateString()) return `Today ${hm}`
  return `${String(d.getDate()).padStart(2, '0')} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`
}

export function dateTime(ms: number): string {
  if (!ms) return ''
  const d = new Date(ms)
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return `${String(d.getDate()).padStart(2, '0')} ${MONTHS[d.getMonth()]} ${d.getFullYear()} ${hm}`
}

export function basename(p: string): string {
  const s = p.replace(/\/+$/, '')
  return s.slice(s.lastIndexOf('/') + 1) || '/'
}

export function dirname(p: string): string {
  const s = p.replace(/\/+$/, '')
  const i = s.lastIndexOf('/')
  if (i <= 0) return '/'
  const d = s.slice(0, i)
  // The folder above C:/x is the drive root C:/, not "C:".
  return /^[A-Za-z]:$/.test(d) ? d + '/' : d
}

/** A Windows path, as the sidecar sends it: C:/x (a drive) or //host/share (a share). */
export function isWinPath(p: string): boolean {
  return /^[A-Za-z]:(\/|$)/.test(p) || /^\/\/[^/]/.test(p)
}

/** The top of a path: / on Linux and macOS, a drive root such as C:/ on Windows. */
export function isRoot(p: string): boolean {
  return p === '/' || /^[A-Za-z]:\/?$/.test(p)
}

/** The folder separator that the user sees for this path. */
export function sep(p: string): string {
  return isWinPath(p) ? '\\' : '/'
}

export function join(dir: string, name: string): string {
  return dir.endsWith('/') ? dir + name : `${dir}/${name}`
}

/** The path as the system file manager shows it. Linux and macOS show the home folder as ~.
 * Windows shows the full path with \, like File Explorer (C:\Users\x), because Windows does not use ~. */
export function tildify(p: string, home: string): string {
  if (isWinPath(p)) return p.replace(/\//g, '\\')
  if (home && (p === home || p.startsWith(home + '/'))) return '~' + p.slice(home.length)
  return p
}

export function expandPath(p: string, home: string): string {
  let s = p.trim()
  // Windows: C:\x (as File Explorer shows it) becomes C:/x (as the sidecar sends it). "C:" is the drive root.
  if (/^[A-Za-z]:([\\/]|$)/.test(s)) {
    s = s.replace(/\\/g, '/').replace(/\/+$/, '')
    return s.length === 2 ? s + '/' : s
  }
  if (s === '~' || s.startsWith('~/')) s = home + s.slice(1)
  s = s.replace(/\$HOME|\$\{HOME\}/g, home)
  if (s.length > 1) s = s.replace(/\/+$/, '')
  return s
}
