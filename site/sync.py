"""
Copies the Workspace scene from the dashboard into this folder, so the hosted demo and the
live dashboard always use the same robots and office.

    python site/sync.py           # copy dashboard/workspace.js and workspace.css into site/
    python site/sync.py --check   # exit 1 if the copies are out of date (for CI or before deploying)

Vercel deploys only what is inside site/, which is why the two files are copied here
instead of being loaded from ../dashboard.
"""

from __future__ import annotations

import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DASHBOARD = os.path.join(os.path.dirname(HERE), "dashboard")
SHARED = ["workspace.js", "workspace.css"]


def main() -> int:
    check = "--check" in sys.argv
    stale = []
    for name in SHARED:
        source, target = os.path.join(DASHBOARD, name), os.path.join(HERE, name)
        with open(source, "rb") as f:
            wanted = f.read()
        current = None
        if os.path.exists(target):
            with open(target, "rb") as f:
                current = f.read()
        if current != wanted:
            stale.append(name)
            if not check:
                shutil.copyfile(source, target)
    if check:
        if stale:
            print("Out of date: " + ", ".join(stale) + ". Run: python site/sync.py")
            return 1
        print("site/ is in sync with dashboard/.")
        return 0
    print("Copied: " + ", ".join(stale) if stale else "Already up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
