# Changelog

Raven uses versions of the form MAJOR.MINOR.PATCH. Before 1.0, a MINOR step can change settings or the index.
A PATCH step only fixes problems. A beta build adds `-beta.N` (for example `0.3.0-beta.1`).
`core/src/raven_core/config.py`, `core/pyproject.toml` and `app/package.json` hold the same version.
`tests/test_release_build.py` checks this.

## Unreleased

### Changed

- Raven is open source under AGPL-3.0-or-later. The builds, the issues and the update check move to
  https://github.com/9u3u3/Raven. The 9u3u3/ravenfiles repository stays for older builds and gets no new builds.

## 0.3.0-beta.2 (2026-10-18)

### New

- The `raven` terminal command, on Linux and macOS. It has one feature now: the Ctrl+K search of the app, in the
  terminal. Enter goes to the folder of the result. For a file, it goes to the folder that holds it.
- Settings, Integrations, Terminal: the list of the terminal features, a button that installs the command in
  `~/.local/bin/raven`, and the line for bash, zsh or fish that adds Ctrl+K and `rv`.
- The command uses the running Raven. When Raven is not running, it starts the Raven core for the time that it
  runs.

### Fixed

- The sidecar exits when its app is gone, also when it cannot unload the Ollama models.

## 0.3.0-beta.1 (2026-10-18)

The first build for early users, on Linux and Windows. The builds and the issues are in
https://github.com/9u3u3/ravenfiles. BETA-LICENSE.md in that repo says how you can use this beta.

### New

- First start, "This computer": a device check, one row for each feature with a fit level, and the downloads.
  Raven installs Ollama for you after a confirm, or shows the steps.
- Settings, "Features and models": turn each feature on or off, see the progress, retry, and remove a model.
- AI providers: Anthropic, OpenAI, Groq, OpenRouter, Google Gemini and a custom OpenAI-compatible service, in
  your order. The keys stay in the system keyring.
- Integrations: one button adds Raven to Claude Code, Claude Desktop, Cursor, Windsurf or VS Code as an MCP server.
- Privacy: India ID types and the types for all countries (cards, IBAN, email, phone, IP, secrets, wallets), with
  checksums. Words to always hide. Strict mode with a second model. Private folders: no AI reads them.
- Every cloud request waits in a review. You see what Raven hid and the exact text, and nothing goes until you
  press Send.
- Settings in a new order: General, Appearance, Features and models, AI providers, Privacy, Integrations, Data,
  Updates, Help, Advanced.
- Settings, Data: the data folder and its size, export the settings, and "Delete all Raven data".
- Settings, General: start Raven with the computer.
- Settings, Help: "Copy diagnostics", the log folder, the third-party notices, "Report a problem", and the
  text "What Raven sends".
- The guide ("?") has a short help page for each screen.
- Welcome cards at the first start: what Raven does, and what stays on this computer. Settings, Help shows them
  again.
- Settings shows one page at a time. Each description stays hidden until you open it.

### Changed

- PDF reading uses PDFium (pypdfium2) and PDF optimize uses pikepdf. PyMuPDF is gone.
- The local models load only for a task and unload after a few idle minutes.
- A release build has no `claude -p` provider, no test hooks and no developer tools.

### Fixed

- Windows: the sidecar stops when the app stops, also after a crash.
- Windows: no console window opens next to Raven.
- Windows: a quit also stops the Ollama model runners that Raven started.

## 0.2.0

The first internal build: search, Ask, the agent, storage tools, the redaction gate, and undo for every change.
