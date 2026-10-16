import { useEffect, useState } from 'react'
import { get, post } from '../lib/api'
import { refreshPrivacy } from '../store'
import { Chip, Ghost } from './ui'

type Account = { dir: string; label: string; selected: boolean; logged_in: boolean; email?: string; detail: string }

/**
 * The Claude account for claude -p. Each ~/.claude-* folder is one account (CLAUDE_CONFIG_DIR). The list
 * comes from `claude auth status`, which makes no model call.
 */
export default function ClaudeAccount() {
  const [accounts, setAccounts] = useState<Account[] | null>(null)
  const [busy, setBusy] = useState(false)
  const load = (): void => void get<{ accounts: Account[] }>('/cloud/claude-accounts').then((r) => setAccounts(r.accounts)).catch(() => setAccounts([]))
  useEffect(load, [])

  if (accounts === null) return <span className="mono text-[11px] text-muted">checking claude…</span>
  const cur = accounts.find((a) => a.selected) ?? accounts[0]
  if (!cur || cur.detail === 'The claude CLI is not installed') {
    return <span className="mono text-[11px] text-muted">claude CLI not installed (optional)</span>
  }
  const pick = async (dir: string): Promise<void> => {
    setBusy(true)
    try {
      await post('/settings', { key: 'claude_config_dir', value: dir })
      void refreshPrivacy()
      load()
    } finally {
      setBusy(false)
    }
  }
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
      <select value={cur.dir} disabled={busy} onChange={(e) => void pick(e.target.value)} className="field mono w-80 max-w-full">
        {accounts.map((a) => (
          <option key={a.dir} value={a.dir}>
            {a.label}
            {a.email ? ` · ${a.email}` : ''}
          </option>
        ))}
      </select>
      <Chip tone={cur.logged_in ? 'good' : 'bad'}>{cur.logged_in ? 'signed in' : cur.detail}</Chip>
      <Ghost tone="plain" onClick={load}>
        Check again
      </Ghost>
    </span>
  )
}
