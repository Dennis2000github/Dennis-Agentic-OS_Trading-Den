#!/usr/bin/env python3
"""CLI preflight for DENNIS Agentic OS.

Usage:
    python3 scripts/preflight.py

Exit code 0 = ready, 1 = something needs attention. No secrets are printed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import preflight  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(preflight.main())
