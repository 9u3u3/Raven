# Raven pipelines

One diagram per pipeline (SVG for editing, PNG for viewing, 1600 × 900). The facts come from the code.
To change a diagram, edit `make_pipelines.py`, then run `python3 docs/pipelines/make_pipelines.py`.

| # | Diagram | What it shows |
|---|---|---|
| 01 | [Architecture](01-architecture.png) | the two processes, the sidecar modules, the models, the database, the cloud and MCP |
| 02 | [Index](02-index.png) | walk, names, text, chunks, PII, meaning, images, speech, categories, folders |
| 03 | [Find](03-find.png) | filters, four ways to match, RRF, habits, the reranker |
| 04 | [Media](04-media.png) | search by look (SigLIP 2) and by speech (Whisper) |
| 05 | [Ask](05-ask.png) | retrieval, the "not found" check, local or cloud answer, citations |
| 06 | [Act](06-act.png) | normalize, grammar, local or cloud plan, limits, review, one undo |
| 07 | [Privacy](07-privacy.png) | the router's 4 conditions and the redaction gate |
| 08 | [Space](08-space.png) | scan, Reclaim, duplicates, plan, review, and safe compression |
| 09 | [Organize and naming](09-organize.png) | the category cascade, plans, learning, names for unnamed files |
