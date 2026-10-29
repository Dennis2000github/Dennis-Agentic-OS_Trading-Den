#!/usr/bin/env python3
"""Download the latest cporter202/API-mega-list repository snapshot.

The Plugins tab reads the repository's own generated READMEs. This script is
only a convenience wrapper around backend.megalist.sync() for terminal use.

Run:  python3 scripts/sync_api_mega_list.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend import megalist  # noqa: E402


def main():
    result = megalist.sync()
    print(json.dumps(result.get("snapshot", result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
