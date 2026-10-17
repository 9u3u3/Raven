import { useEffect, useState } from 'react'
import { get, post } from '../lib/api'
import { refreshPrivacy, say } from '../store'
import { Ghost } from './ui'

/** Words to always hide (PLAN4 7.4). The gate treats each word as a high-risk item: it never goes to the cloud. */
export default function PrivacyWords() {
  const [words, setWords] = useState<string[] | null>(null)
  const [draft, setDraft] = useState('')
  useEffect(() => void get<{ words: string[] }>('/privacy/words').then((r) => setWords(r.words)).catch(() => setWords([])), [])
  if (words === null) return null
  const save = async (next: string[]): Promise<void> => {
    try {
      setWords((await post<{ words: string[] }>('/privacy/words', { words: next })).words)
      void refreshPrivacy()
    } catch (e) {
      say((e as Error).message, 'error')
    }
  }
  const add = (): void => {
    const more = draft.split(/[\n,]/).map((w) => w.trim()).filter((w) => w.length >= 2)
    if (!more.length) return
    void save([...words, ...more])
    setDraft('')
  }
  return (
    <div className="max-w-[720px]">
      <div className="mb-3 flex flex-wrap gap-2">
        {words.length === 0 && <span className="text-muted">No words yet.</span>}
        {words.map((w) => (
          <span key={w} className="group inline-flex items-center gap-2 rounded-md border border-line px-2 py-0.5">
            <span className="mono text-[12px]">{w}</span>
            <button className="text-[11px] text-muted hover:text-danger" title={`Stop hiding ${w}`} onClick={() => void save(words.filter((x) => x !== w))}>
              ✕
            </button>
          </span>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <input value={draft} placeholder="a word or a name, or several with commas" spellCheck={false} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()} className="field w-96 max-w-full" />
        <Ghost disabled={!draft.trim()} onClick={add}>
          Add
        </Ghost>
      </div>
    </div>
  )
}
