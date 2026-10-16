"""
Linux GTK4 Desktop GUI launcher.
Delegates to src.gui.gui.
"""
import sys
import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT_DIR, "src")
for p in [ROOT_DIR, SRC_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from src.gui.gui import JobApp, JobAppWindow, resolve_script_path

if __name__ == "__main__":
    app = JobApp()
    sys.exit(app.run(sys.argv))
