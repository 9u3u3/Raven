import { useState } from 'react'
import { post } from '../lib/api'
import { refreshPrivacy } from '../store'
import { Chip, Ghost } from './ui'

type Test = { ok: boolean; detail: string }

/**
 * One cloud API key: paste it, then "Save and test". The key goes to the sidecar and never comes back to
 * the renderer: `stored` is only a hint such as "…a1b2". The test sends no file data.
 */
export default function KeyField({
  provider,
  setting,
  label,
  placeholder,
  stored,
  onSaved
}: {
  provider: 'groq' | 'anthropic'
  setting: string
  label: string
  placeholder: string
  stored?: string
  onSaved?: () => void
}) {
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [test, setTest] = useState<Test | null>(null)

  const save = async (): Promise<void> => {
    setBusy(true)
    setTest(null)
    try {
      if (value.trim()) await post('/settings', { key: setting, value: value.trim() })
      setTest(await post<Test>('/cloud/test', { provider }))
      setValue('')
      void refreshPrivacy()
      onSaved?.()
    } catch (e) {
      setTest({ ok: false, detail: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }
  const remove = async (): Promise<void> => {
    await post('/settings', { key: setting, value: null })
    setTest(null)
    void refreshPrivacy()
    onSaved?.()
  }

  return (
    <>
      <span className="text-muted">{label}</span>
      <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <input
          type="password"
          value={value}
          autoComplete="off"
          placeholder={stored ? `saved ${stored}` : placeholder}
          spellCheck={false}
          onChange={(e) => (setValue(e.target.value), setTest(null))}
          onKeyDown={(e) => e.key === 'Enter' && !busy && (value.trim() || stored) && void save()}
          className="field mono w-80 max-w-full"
        />
        <Ghost disabled={busy || !(value.trim() || stored)} onClick={() => void save()}>
          {busy ? 'Testing…' : value.trim() ? 'Save and test' : 'Test'}
        </Ghost>
        {stored && !value && (
          <Ghost tone="plain" onClick={() => void remove()}>
            Remove
          </Ghost>
        )}
        {test && <Chip tone={test.ok ? 'good' : 'bad'}>{test.ok ? `works · ${test.detail}` : test.detail}</Chip>}
      </span>
    </>
  )
}
