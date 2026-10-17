"""Write src/raven_core/third_party.json: the licenses of everything that Raven ships or downloads (PLAN4 B6).

Run it after a dependency change, and before a release build:

    cd core && uv run python scripts/notices.py

It reads:
1. the Python packages of the sidecar (uv export --no-dev, then the metadata of each installed package);
2. the Node packages that the renderer bundle holds (the runtime packages in app/package.json and what they
   need, from `pnpm licenses list --json`);
3. the models and programs that Raven downloads (a fixed list here, checked on the model pages).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

CORE = Path(__file__).resolve().parents[1]
APP = CORE.parent / "app"
OUT = CORE / "src" / "raven_core" / "third_party.json"

# The renderer bundle: these packages and their own dependencies. Build tools (vite, electron-builder,
# typescript, tailwind) do not go into the app.
RUNTIME_NODE = {
    "@fontsource-variable/archivo", "@fontsource-variable/inter", "@fontsource/commit-mono", "@fontsource/geist-mono",
    "@fontsource/ibm-plex-mono", "@fontsource/jetbrains-mono", "@radix-ui/react-context-menu", "@radix-ui/react-dialog",
    "@tanstack/react-virtual", "echarts", "electron", "fzf", "graphology", "graphology-layout-forceatlas2", "lucide-react",
    "react", "react-dom", "sigma", "zustand",
}

MODELS = [
    {"name": "EmbeddingGemma 300M", "source": "https://huggingface.co/google/embeddinggemma-300m (through Ollama)",
     "license": "Gemma Terms of Use",
     "notice": "Gemma is provided under and subject to the Gemma Terms of Use found at ai.google.dev/gemma/terms."},
    {"name": "Qwen3 1.7B, 4B, 8B", "source": "https://huggingface.co/Qwen (through Ollama)", "license": "Apache-2.0"},
    {"name": "SigLIP 2 base (ONNX)", "source": "https://huggingface.co/google/siglip2-base-patch16-224", "license": "Apache-2.0"},
    {"name": "MiniLM L-6 cross-encoder (ms-marco)", "source": "https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2", "license": "Apache-2.0"},
    {"name": "Whisper base (faster-whisper)", "source": "https://huggingface.co/Systran/faster-whisper-base", "license": "MIT"},
    {"name": "Privacy Filter", "source": "https://huggingface.co/openai/privacy-filter", "license": "Apache-2.0"},
    {"name": "GLiNER PII base v1.0", "source": "https://huggingface.co/knowledgator/gliner-pii-base-v1.0", "license": "Apache-2.0"},
    {"name": "Potion retrieval 32M, Potion base 8M", "source": "https://huggingface.co/minishlab", "license": "MIT"},
]
PROGRAMS = [
    {"name": "Ollama", "source": "https://github.com/ollama/ollama", "license": "MIT"},
    {"name": "Electron and Chromium", "source": "https://www.electronjs.org",
     "license": "MIT (Electron). Chromium licenses: LICENSES.chromium.html in the app folder."},
]


def _license(dist: metadata.Distribution) -> str:
    m = dist.metadata
    expr = m.get("License-Expression")
    if expr:
        return expr
    lic = (m.get("License") or "").strip()
    if lic and len(lic) < 80 and "\n" not in lic:
        return lic
    classes = [c.split("::")[-1].strip() for c in m.get_all("Classifier") or [] if c.startswith("License ::")]
    if classes:
        return ", ".join(classes)
    return lic.splitlines()[0][:80] if lic else "see the package"


def python_packages() -> list[dict]:
    out = subprocess.run(["uv", "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project",
                          "--format", "requirements-txt"], cwd=CORE, capture_output=True, text=True, check=True).stdout
    names = []
    for ln in out.splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==", ln.strip())
        if m:
            names.append(m.group(1))
    rows = []
    for n in sorted(set(names), key=str.lower):
        try:
            d = metadata.distribution(n)
        except metadata.PackageNotFoundError:
            continue
        rows.append({"name": d.metadata["Name"], "version": d.version, "license": _license(d),
                     "source": d.metadata.get("Home-page") or next((u.split(",", 1)[-1].strip() for u in d.metadata.get_all("Project-URL") or []), "")})
    return rows


def node_packages() -> list[dict]:
    raw = subprocess.run(["pnpm", "licenses", "list", "--json"], cwd=APP, capture_output=True, text=True, check=True).stdout
    by_name: dict[str, dict] = {}
    for lic, pkgs in json.loads(raw).items():
        for p in pkgs:
            by_name[p["name"]] = {**p, "license": lic}
    # What the runtime packages need, from each package.json.
    keep, todo = set(), list(RUNTIME_NODE)
    while todo:
        n = todo.pop()
        if n in keep or n not in by_name:
            continue
        keep.add(n)
        for path in by_name[n].get("paths", []):
            try:
                pj = json.loads((Path(path) / "package.json").read_text())
            except OSError:
                continue
            todo += list((pj.get("dependencies") or {}).keys())
    return [{"name": n, "version": ", ".join(by_name[n]["versions"]), "license": by_name[n]["license"],
             "source": by_name[n].get("homepage") or ""} for n in sorted(keep)]


def main() -> None:
    data = {"models": MODELS, "programs": PROGRAMS, "python": python_packages(), "node": node_packages()}
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(f"{OUT}: {len(data['python'])} Python, {len(data['node'])} Node packages", file=sys.stderr)


if __name__ == "__main__":
    main()
