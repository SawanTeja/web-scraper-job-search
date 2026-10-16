"""
Windows / Cross-platform CustomTkinter Desktop GUI launcher.
Delegates to src.gui.win_gui.
"""
import sys
import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT_DIR, "src")
for p in [ROOT_DIR, SRC_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from src.gui.win_gui import JobAppWindow, resolve_script_path

if __name__ == "__main__":
    app = JobAppWindow()
    app.mainloop()
