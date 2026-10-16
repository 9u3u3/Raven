import { useEffect, useState } from 'react'
import { del, get, on, post } from '../lib/api'
import { tildify } from '../lib/format'
import { say, useApp } from '../store'
import { EmptyState } from './EmptyState'
import { Ghost, SectionTitle, Switch, stagger } from './ui'

type Rule = { id: number; folder: string; match: 'category' | 'ext' | 'name' | 'generic'; value: string; action: 'move' | 'rename' | 'name'; target: string; enabled: number; applied: number }
type Suggestion = { folder: string; match: 'category'; value: string; action: 'move'; target: string; times: number; text: string }
type Example = { id: number; path: string; name: string; category: string }

const MATCH = { category: 'category is', ext: 'extension is', name: 'name has', generic: 'the name is generic' }

/**
 * Watch folders with rules (section 6.2): a rule acts on new files in one folder. It runs every 10 s,
 * and each run is one undo. Below the rules: rules Raven suggests from your own moves, and what Raven
 * learned from your corrections (learn.py).
 */
export default function WatchRules() {
  const cwd = useApp((s) => s.path)
  const home = useApp((s) => s.places?.home ?? '')
  const [rules, setRules] = useState<Rule[]>([])
  const [sugg, setSugg] = useState<Suggestion[]>([])
  const [examples, setExamples] = useState<Example[]>([])
  const [folders, setFolders] = useState<Record<string, string>>({})
  const [cats, setCats] = useState<string[]>([])
  const [form, setForm] = useState({ folder: cwd, match: 'category' as Rule['match'], value: 'Invoice', action: 'move' as Rule['action'], target: '' })
  const [error, setError] = useState<string | null>(null)

  const load = (): void => {
    void get<{ rules: Rule[]; suggestions: Suggestion[] }>('/rules').then((r) => (setRules(r.rules), setSugg(r.suggestions))).catch(() => {})
    void get<{ examples: Example[]; folders: Record<string, string> }>('/learn/examples').then((r) => (setExamples(r.examples), setFolders(r.folders))).catch(() => {})
  }
  useEffect(() => {
    load()
    void get<{ categories: string[] }>('/classify/categories').then((r) => setCats(r.categories)).catch(() => {})
    // A rule ran (rules.py publishes rule_applied): show the new counts at once.
    return on('rule_applied', load)
  }, [])

  const add = async (r: Omit<Rule, 'id' | 'enabled' | 'applied'>): Promise<void> => {
    setError(null)
    try {
      await post('/rules', r)
      say('Rule added. It acts on new files in that folder.')
      load()
    } catch (e) {
      setError((e as Error).message)
    }
  }
  const runNow = async (id: number): Promise<void> => {
    try {
      const r = await post<{ moved: number; label?: string; journal?: never }>(`/rules/${id}/run`)
      say(r.moved ? `${r.label}: ${r.moved} file${r.moved === 1 ? '' : 's'}` : 'No file in that folder matches the rule now.', 'info', { undo: r.moved > 0 })
      load()
    } catch (e) {
      say((e as Error).message, 'error')
    }
  }
  const describe = (r: { match: Rule['match']; value: string; action: Rule['action']; target: string }): string =>
    `${MATCH[r.match]} ${r.value} → ${r.action === 'move' ? `move to ${tildify(r.target, home)}` : `rename to “${r.target}”`}`

  // A rule is a sentence. The parts that change are underlined in brass.
  const Sentence = ({ r, folder }: { r: { match: Rule['match']; value: string; action: Rule['action']; target: string }; folder?: string }) => (
    <p className="serif text-[14px] leading-[1.35]">
      {folder && (
        <>
          <span className="text-muted">In </span>
          <span className="mono align-middle text-[13px] text-text">{tildify(folder, home)}</span>
          <span className="text-muted">, when </span>
        </>
      )}
      {!folder && <span className="text-muted">When </span>}
      <span className="text-muted">{MATCH[r.match]} </span>
      {r.match !== 'generic' && <span className="text-accent underline decoration-accent/40 underline-offset-4">{r.value}</span>}
      <span className="text-muted">, {r.action === 'move' ? 'move it to ' : r.action === 'name' ? 'give it a real name' : 'rename it to '}</span>
      {r.action !== 'name' && <span className="mono align-middle text-[13px] text-text">{r.action === 'move' ? tildify(r.target, home) : r.target}</span>}
      <span className="text-muted">.</span>
    </p>
  )
  const word = 'serif text-[14px]  text-muted'

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6">
      <div>
        <section className="mb-4">
          <SectionTitle note={rules.length ? `${rules.filter((r) => r.enabled).length} of ${rules.length} on · checked every 10 seconds` : undefined}>Rules</SectionTitle>
          {!rules.length && <EmptyState art="folder" title="No rules yet" />}
          <ol>
            {rules.map((r, k) => (
              <li key={r.id} style={stagger(k)} className={`anim-row group grid grid-cols-[44px_minmax(0,1fr)_auto] items-center gap-5 border-b border-line py-4 transition-opacity ${r.enabled ? '' : 'opacity-50'}`}>
                <Switch on={!!r.enabled} label={r.enabled ? 'Turn off' : 'Turn on'} onChange={(v) => void post(`/rules/${r.id}`, { enabled: v }).then(load)} />
                <div className="min-w-0">
                  <Sentence r={r} folder={r.folder} />
                  <div className="mono mt-1 text-[11px] text-muted">
                    {r.applied} file{r.applied === 1 ? '' : 's'} so far
                  </div>
                </div>
                <div className="flex items-center gap-6">
                  <Ghost onClick={() => void runNow(r.id)} title="Also takes the files that were there before the rule">
                    Run now
                  </Ghost>
                  <button className="link text-muted opacity-0 transition-all hover:text-danger group-hover:opacity-100" onClick={() => void del(`/rules/${r.id}`).then(load)} title="Delete this rule">
                    delete
                  </button>
                </div>
              </li>
            ))}
          </ol>

          {/* The new rule reads as a sentence: in this folder, when this, then that. */}
          <div className="anim-row mt-4" style={stagger(rules.length)}>
            <div className="label mb-3">A new rule</div>
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-4">
              <span className={word}>In</span>
              <input value={form.folder} spellCheck={false} onChange={(e) => setForm({ ...form, folder: e.target.value })} className="field mono w-[280px]" placeholder="folder to watch" />
              <span className={word}>when</span>
              <select value={form.match} onChange={(e) => setForm({ ...form, match: e.target.value as Rule['match'], value: e.target.value === 'category' ? 'Invoice' : '' })} className="field">
                <option value="category">category is</option>
                <option value="ext">extension is</option>
                <option value="name">name has</option>
                <option value="generic">name is generic</option>
              </select>
              {form.match === 'generic' ? null : form.match === 'category' ? (
                <select value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} className="field">
                  {cats.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              ) : (
                <input value={form.value} spellCheck={false} placeholder={form.match === 'ext' ? 'deb, AppImage' : 'screenshot'} onChange={(e) => setForm({ ...form, value: e.target.value })} className="field mono w-[180px]" />
              )}
              <span className={word}>then</span>
              <select value={form.action} onChange={(e) => setForm({ ...form, action: e.target.value as Rule['action'], target: '' })} className="field">
                <option value="move">move to</option>
                <option value="rename">rename to</option>
                <option value="name">give a real name</option>
              </select>
              {form.action !== 'name' && <input
                value={form.target}
                spellCheck={false}
                placeholder={form.action === 'move' ? '~/Documents/Money/Invoices' : '{date} {name}{ext}'}
                onChange={(e) => setForm({ ...form, target: e.target.value })}
                className="field mono w-[300px]"
              />}
              <button className="btn-primary" onClick={() => void add({ ...form, value: form.match === 'generic' ? '*' : form.value, target: form.target.replace(/^~(?=\/|$)/, home) })}>
                Add rule
              </button>
            </div>
            {form.action === 'rename' && <div className="mono mt-2 text-[11px] text-muted">{'{name} {ext} {date} {category}'}</div>}
            {error && <div className="mt-2 text-danger">{error}</div>}
          </div>
        </section>

        {sugg.length > 0 && (
          <section className="mb-4">
            <SectionTitle note="Raven noticed you moving files by hand">Suggested from your moves</SectionTitle>
            <ol>
              {sugg.map((sg, k) => (
                <li key={`${sg.folder}|${sg.value}|${sg.target}`} style={stagger(k)} className="anim-row grid grid-cols-[minmax(0,1fr)_auto] items-center gap-6 border-b border-line py-4">
                  <div>
                    <div className="mb-1 text-muted">{sg.text}</div>
                    <Sentence r={sg} folder={sg.folder} />
                  </div>
                  <Ghost onClick={() => void add(sg)}>Add this rule</Ghost>
                </li>
              ))}
            </ol>
          </section>
        )}

        <section>
          <SectionTitle note="Raven gets better as you correct it">Learned from your corrections</SectionTitle>
          {!examples.length && !Object.keys(folders).length && <EmptyState art="search" title="Nothing learned yet" />}
          <ul>
            {examples.map((x, k) => (
              <li key={x.id} style={stagger(k)} className="anim-row group flex items-baseline gap-3 border-b border-line/70 py-2.5">
                <span className="mono truncate text-[12.5px]">{x.name}</span>
                <span className="serif  text-muted">is</span>
                <span className="serif text-[18px]  text-accent">{x.category}</span>
                <span className="ml-auto" />
                <button className="link text-muted opacity-0 transition-all hover:text-danger group-hover:opacity-100" onClick={() => void del(`/learn/examples/${x.id}`).then(load)}>
                  forget
                </button>
              </li>
            ))}
            {Object.entries(folders).map(([cat, f]) => (
              <li key={cat} className="flex items-baseline gap-2 border-b border-line/70 py-2.5 text-muted">
                Organize puts <span className="serif text-[18px]  text-accent">{cat.toLowerCase()}</span> files in <span className="mono text-text">{f}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  )
}
