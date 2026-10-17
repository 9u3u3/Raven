# PyInstaller spec for the Raven sidecar: one folder with its own Python (section 13.12, packaging).
# Build: cd core && .venv/bin/pyinstaller packaging/raven-core.spec --noconfirm --distpath dist --workpath build
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, collect_dynamic_libs

datas, binaries, hidden = [], [], []
for pkg in ("sqlite_vec", "onnxruntime", "tokenizers", "faster_whisper", "ctranslate2", "magika", "markitdown",
            "sqlcipher3", "pypdfium2", "pypdfium2_raw", "pikepdf", "keyring",
            "anthropic", "google.genai",  # the cloud provider SDKs (PLAN4 4)
            "model2vec", "safetensors"):  # model2vec: lite mode (PLAN2 P2-6)
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h
hidden += collect_submodules("raven_core")
# The license list for Settings, Help (scripts/notices.py writes it).
datas += [("../src/raven_core/third_party.json", "raven_core")]
# The release notes for Settings, Updates.
datas += [("../../CHANGELOG.md", "raven_core")]
hidden += ["psutil", "zstandard"]
# The MCP SDK, without its CLI (it needs typer, and Raven does not use it).
hidden += collect_submodules("mcp", filter=lambda n: not n.startswith("mcp.cli"))
datas += collect_data_files("mcp")
hidden += collect_submodules("uvicorn") + collect_submodules("websockets") + ["httptools", "uvloop"]
hidden += ["keyring.backends.SecretService", "keyring.backends.Windows", "keyring.backends.macOS", "keyring.backends.fail"]

a = Analysis(
    ["entry.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    # PLAN3 P1-4: the map by meaning and the photo hash use NumPy only. Keep the heavy science stack out.
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "torch", "scipy", "sklearn", "numba", "llvmlite", "umap", "pynndescent"],
    noarchive=False,
)
pyz = PYZ(a.pure)
# Python UTF-8 mode: open() and read_text() use UTF-8 on Windows too, not the code page (cp1252).
exe = EXE(pyz, a.scripts, [("X utf8", None, "OPTION")], exclude_binaries=True, name="raven-core", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="raven-core", upx=False)
