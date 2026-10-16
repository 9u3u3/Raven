import { useEffect, useRef, useState } from 'react'
import { renameTo, useApp } from '../store'

export default function RenameInput({ path, name, className = '' }: { path: string; name: string; className?: string }) {
  const [value, setValue] = useState(name)
  const ref = useRef<HTMLInputElement>(null)
  const done = useRef(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.focus()
    // Select the name without the extension, like other file managers.
    const dot = name.lastIndexOf('.')
    el.setSelectionRange(0, dot > 0 ? dot : name.length)
  }, [name])
  const commit = (): void => {
    if (done.current) return
    done.current = true
    void renameTo(path, value.trim())
  }
  return (
    <input
      ref={ref}
      value={value}
      spellCheck={false}
      onChange={(e) => setValue(e.target.value)}
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
      onBlur={commit}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Enter') commit()
        if (e.key === 'Escape') {
          done.current = true
          useApp.setState({ renaming: null })
        }
      }}
      className={`w-full min-w-0 py-0 ${className}`}
    />
  )
}
