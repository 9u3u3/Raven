// What Raven sends, where and when (PLAN4 section 8, privacy text). docs/PRIVACY.md has the same text for the
// site. Change both together.

export const PRIVACY_TEXT: { title: string; lines: string[] }[] = [
  {
    title: 'Cloud AI',
    lines: [
      'Only in Hybrid mode. In Local only mode, nothing goes to a cloud AI.',
      'Only for heavy tasks and Think harder, and only after you press Send in the review.',
      'Only redacted text goes, to the providers that you added in Settings, AI providers, in your order.',
      'The redaction gate hides names, IDs, bank and card numbers, health words, secrets and your own words. If the Privacy Filter is not ready, nothing goes.',
      'Photos, videos and audio never go, and their text (OCR, transcripts) never goes. Files in private folders never go.',
      'The Privacy screen ledger keeps a copy of each payload that went.',
      'When you add or test a provider, Raven asks it for its model list. That request has your key and no file data.'
    ]
  },
  {
    title: 'MCP clients',
    lines: [
      'Off by default. When you turn it on, the MCP clients on this computer (Claude Code, Claude Desktop, Cursor and others) can search and read your indexed files.',
      'Each answer goes through the same redaction gate. MCP cannot change, move or delete files.'
    ]
  },
  {
    title: 'Downloads',
    lines: [
      'Raven downloads models from Hugging Face and the Ollama library, and the Ollama program from GitHub, when you turn a feature on.',
      'These downloads send no file data and no user ID.'
    ]
  },
  {
    title: 'Update check',
    lines: ['Off by default. When it is on, Raven asks GitHub for the latest release, once a day. The request has no file data and no user ID.']
  },
  {
    title: 'Logs and diagnostics',
    lines: [
      'The log files stay on this computer. They hold no file content and no payload.',
      '"Copy diagnostics" puts a report on the clipboard. Raven sends nothing: you decide where to paste it.'
    ]
  },
  {
    title: 'Nothing else',
    lines: [
      'Raven has no telemetry, no crash reports and no account.',
      'The window can only talk to Raven on this computer. Every other request from the window is blocked.'
    ]
  }
]
