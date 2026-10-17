# Release test guide: PLAN4 R1 to R6

This guide tests everything that PLAN4 phases R1 to R6 built. Use it on Linux (the AppImage) and on Windows (the NSIS
setup). Do each part in order. Tick each box when the result is correct. Write the problems in the table at the end.

`notes/plans/PLAN4-release.md` section 0.1 lists what each phase built and what is still open.

## Contents

0. Prepare
1. Install and first start (R2, R5)
2. Files and paths (R5)
3. R1: safe to share (release build, logs, diagnostics, notices)
4. R2: device check, features, Ollama
5. R3: leak proof (Privacy Filter, words to hide, Strict mode, private folders, media rule, review before send)
6. R4: AI providers and MCP clients
7. R5: Windows checks
8. R6: Settings, Data, Updates, Help
9. Delete all Raven data (do this last)
10. Results

## 0. Prepare

### 0.1 Get the builds

1. Open GitHub, Actions, workflow **Build**. Open the last green run.
2. Download the artifacts `raven-ubuntu-22.04`, `raven-windows-2022` and `raven-sha256sums`.
   Or run this command:

   ```bash
   gh run download <run id> -R 9u3u3/Raven -n raven-ubuntu-22.04 -n raven-windows-2022 -n raven-sha256sums
   ```

3. Check the files against the sums:

   ```bash
   sha256sum -c SHA256SUMS.txt --ignore-missing
   ```

   - [ ] Each line says `OK`.

### 0.2 Make test files

Make a folder `RavenTest` in your Documents folder. Put these files in it:

| File | Content |
|---|---|
| `notes.md` | A few lines about a project, for example "Plan for Project Falcon. Budget 4 lakh. Meet Priya on Monday." |
| `ids.txt` | `PAN ABCDE1234F`, `Aadhaar 2345 6789 0124` (a test number), `card 4111 1111 1111 1111`, `phone +91 98765 43210`, `mail test@example.com` |
| `table.csv` | A header row `name,pan,city` and two rows with fake names and PAN numbers |
| `photo.jpg` | Any photo |
| `report.pdf` | Any PDF with text |
| `Diary/secret.md` | A sub-folder `Diary` with one text file that says "my secret diary" |

Use fake data only. Do not use your real ID numbers.

### 0.3 Know the folders

| What | Linux | Windows |
|---|---|---|
| Data folder (index, models, keys list, logs) | `~/.local/share/raven` | `%LOCALAPPDATA%\raven` |
| Sidecar log | `~/.local/share/raven/logs/raven-core.log` | `%LOCALAPPDATA%\raven\logs\raven-core.log` |
| App log | `~/.config/raven/logs/main.log` | `%APPDATA%\raven\logs\main.log` |
| Installed app | the AppImage file | `%LOCALAPPDATA%\Programs\Raven` |

To start clean, close Raven and delete the data folder before part 1. To keep your real index, start the test with
another data folder: `RAVEN_DATA_DIR=~/raven-test-data ./Raven-0.3.0-x86_64.AppImage`.

## 1. Install and first start (R2, R5)

### 1.1 Install

Linux:

1. Run `chmod +x Raven-0.3.0-x86_64.AppImage`.
2. Run `./Raven-0.3.0-x86_64.AppImage`.

- [ ] The window opens in less than 30 s.

Windows:

1. Double-click `Raven-0.3.0-x64.exe`.
2. SmartScreen shows "Windows protected your PC", because the build is not signed. Click **More info**, then
   **Run anyway**.
3. Follow the setup.

- [ ] The setup ends with no error. Start menu has "Raven".
- [ ] Raven opens. No black console window opens with it.
- [ ] Task Manager shows `Raven.exe` and one `raven-core.exe`.

### 1.2 First start, step 1 "This computer"

- [ ] The device line shows the system, the CPU, the threads, the RAM, the GPU and the free disk. The values are
      correct for this computer.
- [ ] Each feature row has a check box, a dot (green "Runs well", amber "Runs slowly", red "Not recommended"), a
      reason line and a size.
- [ ] Raven ticked only the features that run well.
- [ ] Tick a red row. One warning line shows, and Raven does not block the tick.
- [ ] Click **Test the speed**. After about 1 s the rows update.
- [ ] Click **Check again**. The device line loads again.
- [ ] The size choices: "Find by meaning" offers EmbeddingGemma, Potion 32M and Potion 8M. "Local AI model" offers
      Qwen3 1.7B, 4B and 8B. Raven suggests the largest size that runs well.
- [ ] The download total at the bottom changes when you tick or untick a row.

### 1.3 Ollama block

Do this test on a computer without Ollama (a clean Windows computer is good).

- [ ] When a ticked feature needs Ollama, the block "Install Ollama" shows with **Install for me** and **Show me the
      steps**.
- [ ] **Show me the steps** shows the steps for this system, with commands to copy, and **Check again**.
- [ ] **Install for me** downloads Ollama, shows the progress, and then shows Ollama as running. No administrator
      prompt shows.
- [ ] If the computer has no internet, the install fails with a clear error, and the steps show.
- [ ] On a computer that already has Ollama, the block does not show, and Raven uses that Ollama.

### 1.4 Steps 2 to 4 and Start

- [ ] Step 2 "Folders to index" lists your home folders. Add `RavenTest` and remove one folder.
- [ ] Step 3 "AI mode" has **Local only** and **Hybrid**.
- [ ] Choose **Hybrid**. Step 4 "AI providers" shows.
- [ ] Click **Start**. The "Getting ready." view shows the downloads and the index progress. You can open the
      files view at once.
- [ ] Close Raven and open it again. The first start screen does not show again.

## 2. Files and paths (R5)

### 2.1 Path line

Linux:

- [ ] Open Downloads. The path line shows `/ home / <you> / Downloads`, from the root `/`.
- [ ] Click `/`. Raven shows the root folder. The **Parent folder** arrow is off at `/`.
- [ ] The drive list shows "File System" for `/`.

Windows:

- [ ] Open Downloads. The path line shows `Local Disk (C:) > Users > <you> > Downloads`, as File Explorer does. It
      does not show `~`.
- [ ] Click `Local Disk (C:)`. Raven shows `C:\`. The **Parent folder** arrow is off at `C:\`.
- [ ] The drive list shows "Local Disk (C:)" and the other drives with their names, not `C:/`.
- [ ] Point at the path line. The tooltip shows `C:\Users\<you>\Downloads`.
- [ ] Press Ctrl+L and type `C:\Windows`. Press Enter. Raven opens that folder.
- [ ] Press Ctrl+L, type `C:\Us`, then press Tab. Raven completes the path with `\`.

Both systems:

- [ ] Press Ctrl+L and type `~/Documents` (Linux) or `C:\Users\<you>\Documents` (Windows). Raven opens it.
- [ ] Type a description, for example "my test folder". The list suggests `RavenTest`.
- [ ] Back, Forward, Parent folder and Reload work. Alt+Left, Alt+Right and Alt+Up work.

### 2.2 File work

- [ ] Copy, move and rename a file in `RavenTest`. Ctrl+Z undoes each change.
- [ ] Delete a file. It goes to the Raven Trash. Ctrl+Z brings it back.
- [ ] Open a file with a double-click. The system app opens it.
- [ ] Select `photo.jpg` and press Space. The inspector shows the preview and the properties.
- [ ] Hidden files: Ctrl+H shows and hides them.

### 2.3 Search

Wait until the status line says that the index is done.

- [ ] Ctrl+K, type `notes`. `notes.md` shows at the top.
- [ ] Type words from inside `report.pdf`. The PDF shows.
- [ ] If "Find by meaning" is on: type a question with other words for the content. The right file shows.

## 3. R1: safe to share

### 3.1 Release build

- [ ] F12 does not open the developer tools.
- [ ] Ctrl+Shift+D does not open the latency overlay.
- [ ] Settings, AI providers has no `claude -p` row and no Claude account list.

Optional, with the frozen sidecar alone (Linux example, Windows is the same with `raven-core.exe`):

```bash
./Raven-0.3.0-x86_64.AppImage --appimage-extract >/dev/null
RAVEN_TOKEN=t RAVEN_DATA_DIR=/tmp/raven-alone squashfs-root/resources/raven-core/raven-core --port 8799 &
curl -s -H 'Authorization: Bearer t' http://127.0.0.1:8799/health
curl -s -H 'Authorization: Bearer t' -X POST -H 'Content-Type: application/json' \
  -d '{"cloud_provider":"claude"}' http://127.0.0.1:8799/settings
```

- [ ] `/health` says `"dev": false`.
- [ ] The `/settings` call returns 400.

### 3.2 Logs

- [ ] The sidecar log and the app log exist (section 0.3).
- [ ] Linux: `ls -l` shows mode `-rw-------` for `raven-core.log`.
- [ ] The logs have no file content and no key.

### 3.3 Diagnostics and notices

- [ ] Settings, Help, **Copy diagnostics**. Paste the text into an editor.
- [ ] It has the version 0.3.0, the system, the device, the feature states, the index counts and the log lines.
- [ ] It has no API key. Linux: `~` replaces the home folder.
- [ ] Settings, Help, third-party notices: the list has the models, the programs, the Python packages and the Node
      packages, with the Gemma notice.

## 4. R2: device check, features, Ollama

Settings, **Features and models**:

- [ ] The rows are the same as at first start, with a switch, the state and the size.
- [ ] Turn on a feature that is off. The row shows the download progress, then "on".
- [ ] Stop the network during a download. The row shows "failed" and **Retry**. Start the network and click
      **Retry**. The download continues.
- [ ] Click **Remove** on a model. A confirm shows. After the confirm, the size goes and the feature is off.

## 5. R3: leak proof

For the cloud checks you need one AI provider with a key (part 6.1). Groq has a free plan.

### 5.1 Privacy Filter and fail closed

1. Settings, Features and models: turn off the Privacy Filter (or remove its model).
2. Ask a question that goes to the cloud: Ask screen, **Think harder**.

- [ ] Raven does not send. The message says that the Privacy Filter is not ready. The local model answers, or
      Raven says to wait.
- [ ] Turn the Privacy Filter on again. Wait until it is "on".

### 5.2 Review before send

1. Settings, AI providers: AI mode **Hybrid**.
2. Ask screen: "Summarize the files in RavenTest", and press **Think harder**.

- [ ] A review dialog shows before anything goes. It shows the exact text, with placeholders such as
      `[PERSON_1]`, `[PAN_1]`, `[AADHAAR_1]`, `[CARD_1]`, `[PHONE_1]`, `[EMAIL_1]`.
- [ ] The text has no real PAN, Aadhaar, card, phone or mail value from `ids.txt` and `table.csv`.
- [ ] The "may still be private" list shows words such as "Project Falcon", with a category and a score.
- [ ] Click **Hide** on a word. The text updates with a `HIDDEN` placeholder.
- [ ] Try to show a PAN or an Aadhaar item again. Raven does not allow it.
- [ ] Click **Don't send**. Nothing goes. The local model answers.
- [ ] Do it again and click **Send**. The answer has the real values back in place of the placeholders.
- [ ] Privacy screen: the ledger lists the call with the provider, the size and the hidden types.
- [ ] Do not answer a review for 5 minutes. Raven sends nothing, and the local model answers.

### 5.3 Words to always hide

1. Settings, Privacy, **Words to always hide**: add `Falcon`.
2. Ask again with Think harder.

- [ ] The review shows `Falcon` hidden (type CUSTOM). You cannot show it again.

### 5.4 Strict mode

1. Settings, Features and models: turn on Strict mode (downloads `gliner-pii`, 197 MB).
2. Ask again with Think harder.

- [ ] Names and addresses that the first model missed are now hidden.
- [ ] Remove the Strict model files while Strict mode is on. A cloud call is blocked.

### 5.5 Private folders

1. Right-click `RavenTest/Diary`, choose **Private: no AI**. Confirm.

- [ ] A search for "secret diary" does not find `secret.md`.
- [ ] Ask "what is in my diary" gives no source from `Diary`.
- [ ] The folder summary of `Diary` is refused. The agent refuses a task in `Diary`.
- [ ] You can still open, copy, rename and delete files in `Diary`, with undo.
- [ ] Add a new file in `Diary`. It does not come into the index.
- [ ] Settings, Privacy, **Private folders** lists `Diary`. Remove it there. The folder comes back into the index.

### 5.6 Media rule

- [ ] Ask about `photo.jpg` with Think harder. The question goes to the local model, not to the cloud.
- [ ] The Privacy ledger shows no cloud call with photo, video or audio data.

## 6. R4: AI providers and MCP clients

### 6.1 AI providers

Settings, **AI providers**:

- [ ] Add each provider you have a key for: Anthropic, OpenAI, Groq, OpenRouter, Gemini.
- [ ] Each row has an on and off switch, the key field, the model list and **Test**.
- [ ] **Test** works with a correct key and gives a clear error with a wrong key.
- [ ] Groq, Gemini and OpenRouter show the free plan note. OpenRouter picks a `:free` model first.
- [ ] Drag a row up or down. Raven uses the first row that is on.
- [ ] Turn off the first row. Raven uses the next one.
- [ ] Remove a row. Its key goes from the keyring.
- [ ] Linux: `secret-tool search service raven` (or the Seahorse app) shows the keys. Windows: Credential Manager,
      Windows Credentials, shows them.

For each provider with a key, do one cloud call (part 5.2) and check the answer:

| Provider | Test passes | Cloud call works |
|---|---|---|
| Anthropic | [ ] | [ ] |
| OpenAI | [ ] | [ ] |
| Groq | [ ] | [ ] |
| OpenRouter | [ ] | [ ] |
| Gemini | [ ] | [ ] |

### 6.2 MCP clients

Settings, **Integrations**:

- [ ] The list shows Claude Desktop, Cursor, Windsurf, VS Code and Claude Code, with a state for each.
- [ ] Windows: the paths are under `C:\Users\<you>\AppData\Roaming\...` and `C:\Users\<you>\.cursor`.
- [ ] Click **Add** for one client that you have. Raven makes a backup copy of its config file first.
- [ ] Open that client. Ask it to search your files with Raven. It finds `notes.md`. It cannot change a file.
- [ ] Ask the client to read `ids.txt`. The text has placeholders, not the values.
- [ ] Ask the client to read `photo.jpg`. Raven refuses.
- [ ] Click **Remove**. The client config no longer has Raven.

## 7. R5: Windows checks

- [ ] Space screen: the scan of `C:\Users\<you>` finishes and shows the sizes of the folders. The folders are not
      all 0 B.
- [ ] Space, duplicates: copy `report.pdf` two times. Raven lists the three files as duplicates.
- [ ] Organize `RavenTest`: the plan shows in the review view. Run it, then undo it.
- [ ] Compress a folder to zip, then restore it.
- [ ] A file name with non-English letters (for example `résumé ü 日本.txt`) shows, opens and is found by search.
- [ ] Close Raven with the window close button. Task Manager has no `raven-core.exe` and no `ollama.exe` that Raven
      started.
- [ ] End `Raven.exe` in Task Manager. After about 10 s, `raven-core.exe` stops by itself.
- [ ] The agent: ask it to move files in `C:\Windows` or `C:\Program Files`. It refuses.
- [ ] Network share (no automatic test yet): map a share with `net use Z: \\server\share`. Open `Z:` in Raven. List,
      copy and open a file. A share that does not answer gives an error in about 5 s.

## 8. R6: Settings, Data, Updates, Help

### 8.1 Settings order

- [ ] The sections show in this order: General, Appearance, Features and models, AI providers, Privacy,
      Integrations, Data, Updates, Help, Advanced.
- [ ] Each item in the left list scrolls to its section.

### 8.2 General: start with the computer

- [ ] Turn on **Start with the computer**.
- [ ] Log out and log in. Raven opens minimized and the index runs.
- [ ] Linux: the file `~/.config/autostart/raven.desktop` exists and points to the AppImage.
- [ ] Windows: Task Manager, Startup apps, lists Raven.
- [ ] Turn it off. Log out and log in. Raven does not open.

### 8.3 Data

- [ ] The section shows the data folder path and the size of each part (index, thumbnails, logs, models, Ollama,
      Trash, other).
- [ ] **Make the index smaller** runs and the index size goes down or stays the same.
- [ ] **Export the settings** saves a JSON file. Open it. It has your settings and the provider order. It has no key.

### 8.4 Updates

- [ ] The section shows version 0.3.0.
- [ ] **Check for updates** is off at first.
- [ ] Turn it on. Raven checks GitHub. The repo is private and has no release yet, so the check can fail or find
      nothing. The message must be clear.
- [ ] The notes of this version (from `CHANGELOG.md`) show.

### 8.5 Help

- [ ] The guide opens. Press "?" on each screen (Files, Space, Organize, Privacy, Ask, Settings). A short help page
      for that screen shows first.
- [ ] **Report a problem** opens the browser on a new GitHub issue form with the version and the system filled in.
- [ ] **Copy diagnostics**, the logs folder button and the notices work (part 3.3).
- [ ] "What Raven sends" shows the privacy text. It is the same as `docs/PRIVACY.md`.

### 8.6 Advanced

- [ ] Power user, Folder rules, Binary vectors and Shortcuts show and work.

## 9. Delete all Raven data (do this last)

This step deletes the index, the downloaded models, the keys and the settings. Your files stay.

1. Settings, Data, **Delete all Raven data**.
2. Read the confirm. Click **Delete all**.

- [ ] Raven closes and starts again with the first start screen.
- [ ] The data folder (section 0.3) has no index, no models and no logs. The Raven Trash stays (Windows: the
      `Trash` folder in the data folder. Linux: the system trash).
- [ ] The keyring has no Raven key.
- [ ] `RavenTest` and all your other files are still there.
- [ ] An Ollama that Raven installed is gone. A system Ollama and its models are still there.

## 10. Results

Write one line for each problem:

| Part | System | What happened | What you expected | Screenshot or log |
|---|---|---|---|---|
| | | | | |

Copy the diagnostics (Settings, Help) and add them to each problem report.
