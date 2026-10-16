// Drag and drop. Raven starts a native drag (Electron startDrag), so the same drag
// works inside Raven and out to other apps. Drops read file paths from the drag data.
import { transfer, useApp } from '../store'

export function startDrag(e: React.DragEvent, paths: string[]): void {
  if (!paths.length) return
  useApp.setState({ dragPaths: paths })
  if (window.raven) {
    e.preventDefault()
    window.raven.startDrag(paths)
  } else {
    e.dataTransfer.setData('text/plain', paths.join('\n'))
    e.dataTransfer.effectAllowed = 'copyMove'
  }
}

export function dragOver(e: React.DragEvent): void {
  e.preventDefault()
  e.dataTransfer.dropEffect = e.ctrlKey ? 'copy' : 'move'
}

function droppedPaths(e: React.DragEvent): string[] {
  const out: string[] = []
  const files = e.dataTransfer.files
  if (files && files.length && window.raven) {
    for (const f of Array.from(files)) {
      const p = window.raven.pathForFile(f)
      if (p) out.push(p)
    }
  }
  if (!out.length) out.push(...useApp.getState().dragPaths)
  if (!out.length) {
    const text = e.dataTransfer.getData('text/plain')
    if (text.startsWith('/')) out.push(...text.split('\n').filter(Boolean))
  }
  return out
}

/** Drop onto a folder: move, or copy with Ctrl held. */
export function dropInto(e: React.DragEvent, folder: string): void {
  e.preventDefault()
  e.stopPropagation()
  const paths = droppedPaths(e).filter((p) => p !== folder && !folder.startsWith(p + '/'))
  useApp.setState({ dragPaths: [] })
  if (paths.length) void transfer(paths, folder, e.ctrlKey ? 'copy' : 'move')
}
