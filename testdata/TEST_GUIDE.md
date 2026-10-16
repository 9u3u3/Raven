# Raven test guide (dummy home: testdata/device)

Start: `cd app && RAVEN_DATA_DIR=~/raven-test-data pnpm dev`. Wait until the index panel says all stages are done.
Some tests change files (compress, organize, agent, rename). Each one has an Undo. To start clean again:
close Raven, `rm -rf ~/raven-test-data`, `core/.venv/bin/python testdata/make_device.py`, start Raven, index `testdata/device`.

"D" below means `~/Codes/my-products/raven/testdata/device`.

## 1. File manager

1. Open D/Documents in Files. Expect a list with Name, Size, Modified and Kind. Kind shows a category after the type
   ("PDF · Lease" style) for classified files.
2. Ctrl+2: grid with thumbnails. Ctrl+1: back to the list.
3. Select D/Documents/scan_rent_receipt.pdf, press Space. Expect the inspector with the PDF preview.
   Click "Compute SHA-256 and BLAKE3". Expect two hashes.
4. Ctrl+H: hidden files show (D/.ssh, D/.fonts). Ctrl+H again: hidden.
5. Ctrl+L, type `~/` then Tab. Expect the path to complete like a shell. Type `/etc` and Enter. Expect /etc.
6. Back (Alt+Left), Forward (Alt+Right), Up (Alt+Up) work. The breadcrumb hides middle parts in a narrow window.
7. Ctrl+Shift+N: new folder, F2 to rename it, Delete to trash it, Ctrl+Z: it comes back.
8. Copy D/Documents/data.json, open D/Downloads, Ctrl+V. Paste again: the conflict dialog opens.
   Try Keep both (data (2).json), then Replace, then Skip. Ctrl+Z after each.
9. Copy D/Downloads/big_backup.bin (48 MB) to D/Submissions. Expect progress in the status line with Pause and Cancel.
   Press Pause, then Resume. Copy again and press Cancel: no half file stays.
10. Select 5 files in D/Downloads, press F2: batch rename. Try Template `{name} {n:2}{ext}`, Find and replace
    with a regex, and Case. Expect a live preview; a clash shows in red. Rename, then Ctrl+Z.
11. F3: dual pane. Tab switches panes. Drag a file to the other pane: it copies (Ctrl) or moves. F3 again closes it.
12. Ctrl+T new tab, Ctrl+W close, Ctrl+Shift+T reopen, Ctrl+Tab next, middle-click a folder: new tab.
13. Right-click D/Downloads/Archives/trip_docs.zip, Open. It opens like a folder: trip_docs/inner/tickets.md.
    Do the same for .7z, .tar.gz and .tar.zst. Right-click tickets.md: no rename or delete inside an archive.
    Right-click the zip, "Extract here": a trip_docs folder appears. Ctrl+Z removes it.
14. Select 3 files, right-click, "Create archive…", .zip. A zip appears. Ctrl+Z removes it.
15. Trash a file, open Trash in the left column, right-click it, Restore: it goes back to its folder.
16. Right-click D/Codes/weather-app/src/forecast.js, "Copy relative path": expect `src/forecast.js`.
17. Right-click a folder, "Add to favorites": it shows in the left column.

## 2. Find (Ctrl+K)

Type each query. Expect the file in the first rows. The right column says why: name, keyword, meaning.

| Query | Expect | What it tests |
|---|---|---|
| `kiosk` | kiosk_pitch.pptx | name |
| `zephyrine solar kiosk` | kiosk_pitch.pptx | PPTX text |
| `quokka lantern budget` | club_budget.xlsx | XLSX cells |
| `marigold committee` | marigold_minutes.docx and heron_notes.odt | DOCX and ODT text |
| `orchid bracket hex key` | orchid_page.html | HTML |
| `lighthouse keeper tangerine cat` | lighthouse_story.epub | EPUB |
| `saffron apartments rent` | scan_rent_receipt.pdf | OCR of a scanned PDF |
| `mango grove stores payment` | Screenshot_2025-06-01_upi.png | OCR of a screenshot |
| `capybara spreadsheet` | deep_note.txt | a deep folder |
| `narwhal robotics intern` | résumé_ünïcode.md | a Unicode name |
| `flamingo cache timeout` | server.log | a log file |
| `rent agreement` | scan_0042.pdf | meaning (the words differ) |
| `kayak safety orange life jacket` | kayak_briefing.mp4, "said at 0:00" | speech in a video |
| `teal notebook sunday` | the memo_* recordings | speech in MP3, M4A, WAV, OGG, Opus |
| `forecast.js` | forecast.js (name only, no text) | a code project |
| `res_7.xml` | no res_7.xml (weak meaning matches can show) | SDK contents are not indexed |
| `id_ed25519` | no id_ed25519 | .ssh is never indexed |

Filters:
1. `type:pdf rent` — only PDFs.  2. `>10MB` — big_backup.bin.  3. `invoices older than 6 months`.
4. Ctrl+Enter on `DBMS notes from last semester bigger than 1MB`: the results screen shows chips. The local model reads
   the words that the rules do not know. Remove a chip with its x: the results change.
5. `photos from two weeks ago`: a date chip "about 2 weeks ago".

Folders: `Sem5`, `old lab project` (Projects/old-sem2-lab by its content), `~/Codes/hack` (a path completes).
An empty command bar shows "Frequent folders" after you open a few folders.

## 3. Search by appearance

1. Ctrl+K `a blue bar chart`. Expect a "Looks like" row: quarterly.png, chart_sales.png and the slides video.
2. Ctrl+Enter on `a dark night sky`. Expect night_sky.webp, and kayak_briefing.mp4 "at 0:05". Double-click the video:
   the inspector opens it at 0:05, with a keyframe strip and the transcript under it. Click a keyframe: it jumps.
3. `a photo of a sandy desert` → desert.bmp. `a sunset` → sunset_photo.jpg and the slides video at 0:00.
4. `upi payment screenshot` → the screenshots. `the page with a table` → PDF pages.
5. Right-click Pictures/Camera/IMG_20250412_101500.jpg, "Find similar": the other two camera photos come first.
6. A long text query (`invoice for the website design work`) shows no image row: text search answers it.

## 4. Ask (type a question in Ctrl+K)

1. `What is the late fee in my rent agreement?` → "Rs 500 for each day" with [1]. Click [1]: scan_0042.pdf opens at page 1.
2. `What did the kayak briefing say to wear?` → the orange life jacket, [1] = the video. Click it: the video opens at the moment.
3. `When is my phone bill due?` → 25 April. `What is the PNR of my train ticket to Delhi?` → 4471882290.
4. `Who won the cricket world cup in 1983?` → "I did not find this in your files."
5. A follow-up in the box at the bottom: `and the security deposit?`
6. "Think harder" under an answer: the cloud model answers (Hybrid mode). The tier line says CLOUD and the redacted count.

## 5. Act (the agent, in Ctrl+K)

1. `convert all DOCX files in Sem5 to PDF and move them to Submissions`. The review opens on the right with 2 steps.
   Run. Check D/Submissions. Ctrl+Z: everything goes back in one step.
2. `move all pdfs in Downloads to Scans` → a new Scans folder in the review. Run, then Ctrl+Z.
3. `compress Projects/old-sem3 to 7z`, `rename all md files in Downloads to note-{n}.md`, `copy the screenshots to Desktop`.
4. Right-click a DOCX, Convert to… PDF. Right-click 3 images, Convert to… "One PDF from these images".

## 6. Space (G then S)

1. Click the drive line or scan D. Expect the treemap. Click a block to go down, the breadcrumb to go up. Switch to Sunburst.
2. Bars by category and by last use. The growth line shows after a second scan on another day.
3. Suggestions: "Archive old-sem2-lab, not opened for 210 days". Review, pick .7z, Run. Expect old-sem2-lab.7z,
   the folder in the trash, "+ghost" in the status line, and a hatched outline in the treemap.
4. Compressed tab: the job with sizes. Restore: the folder comes back after a hash check. Ctrl+Z also works.
5. Duplicates tab, Find duplicates in D. Expect: exact copies (IMG_20250412_101500.jpg and "copy of trip photo.jpg",
   IMG_8801.jpg and sunset_photo.jpg) and "similar photos" (the 3 camera photos, and the 4 DSC_410x photos).
   "Review all copies" checks the copies. "Review similar photos" checks nothing: you choose.
6. Junk: node_modules in Projects/web-app, __pycache__ in old-sem3, the .part and .crdownload downloads, cache_dump.bin.
7. Largest: big_backup.bin first.
8. Rules tab: an Auto compression rule (folder, cold days, size). "Run now", then Undo in the status line.

## 7. Organize (G then O)

1. Plan a folder: D/Downloads, Suggest. Two trees: now and after. Unchecking an item removes it from the plan.
   Drag an item to another folder in the right tree. Rename an item in place. Apply. Ctrl+Z undoes all of it.
2. Change the folder of an invoice in a plan (for example to Money/Invoices) and Apply. The status line says Raven
   keeps that folder. Suggest again in another folder: invoices go to Money/Invoices.
3. "Think harder": the cloud proposes the tree from redacted names. Check the Privacy ledger after it.
4. Watch rules: folder D/Downloads, "extension is" `deb, AppImage`, "move to" D/Downloads/Installers. Add rule.
   Copy setup-installer.AppImage to D/Downloads with a new name and wait 30 s: the rule moves it, with Undo.
   "Run now" also takes the files that were there before.
5. Right-click scan_0043.pdf, "Set category…", Bill. The Learned list shows it.
6. After 3 manual moves of one category to one folder, "Suggested from your moves" offers a rule.
7. File map: points by meaning, groups with names. Drag to move, wheel to zoom, click a point to open the file.
   Click a group name: the files list on the right. "Put these in one folder…", Move, then Ctrl+Z.

## 8. Privacy (G then P)

1. Modes: Local only, Hybrid, Ask each time. With Ask each time, Think harder shows the payload first.
2. Ledger: every cloud call with the exact redacted payload ([PERSON_1], [ACCOUNT_1]). Export JSON.
3. Sensitive files: kyc.txt and statement(2).pdf (PAN, Aadhaar, phone). passwords.txt is a Secret.
4. Test redaction: paste `Rahul Sharma, PAN ABCDE1234F, phone 9876543210` → placeholders.
5. Offline: turn the network off. Within a minute the mode line says LOCAL ONLY (offline). Think harder answers locally.

## 9. Settings (Ctrl+,)

1. Indexed folders: add or remove a folder. The index panel (click the status line) shows each stage and the time left.
2. Power user: "Read the files in code projects and software folders too" (off: forecast.js is a name only).
   Power user mode: then a "Permission denied" offers "Retry as administrator" (needs a polkit agent).
3. Performance: binary vectors (automatic from 50,000 chunks; try "on", search still works).
4. Encrypted index: set a passphrase. Raven restarts and shows the unlock screen. A wrong passphrase fails.
   Turn it off again with the passphrase.
5. MCP clients: turn it on, copy the config into Claude Desktop, ask Claude "search my files for the rent agreement".
   The ledger shows "mcp search".
6. Text size, theme (light, dark, system), shortcuts list.

## 10. Network

1. Connect to server (left column): smb://, sftp:// as in the README table.
2. "Cloud and S3 (rclone)" tab: needs rclone and fuse3. Add a WebDAV or S3 remote, Open: it mounts and opens as a folder.

## 11. What to send me when something fails

The query or the step, a screenshot, and the last lines of the terminal that runs `pnpm dev`.
