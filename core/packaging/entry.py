"""Entry point of the frozen sidecar (PyInstaller). Same flags as `python -m raven_core`."""

import multiprocessing

from raven_core.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
