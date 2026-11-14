#!/usr/bin/env python3
"""Copy an awesome-free-llm-apis checkout into the OS snapshot.

The FreeLLMAPI dashboard reads ``public-plugins/free-llm-apis/README.md``.
This script refreshes that snapshot from a local checkout (or any directory
passed with ``--source``) and records the source commit in ``SNAPSHOT.json``.

Run:  python3 scripts/sync_free_llm_apis.py
      python3 scripts/sync_free_llm_apis.py --source ~/PycharmProjects/awesome-freellm-apis
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend import freellm_catalog  # noqa: E402

FILES = (
    "README.md",
    "README.zh-CN.md",
    "README.zh-TW.md",
    "README.ja.md",
    "README.ko.md",
    "LICENSE",
    "CONTRIBUTING.md",
)
DIRECTORIES = ("code-examples", "assets")


def _git(path, *args):
    try:
        return subprocess.check_output(
            ["git", "-C", path, *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _normalize_modes(root):
    """Snapshot files are public content, not private state."""
    os.chmod(root, 0o755)
    for base, dirs, names in os.walk(root):
        for name in dirs:
            try:
                os.chmod(os.path.join(base, name), 0o755)
            except OSError:
                pass
        for name in names:
            try:
                os.chmod(os.path.join(base, name), 0o644)
            except OSError:
                pass


def _default_source():
    for path in freellm_catalog.candidate_paths():
        if os.path.abspath(path) == os.path.abspath(freellm_catalog.SNAPSHOT_DIR):
            continue
        if os.path.isfile(os.path.join(path, "README.md")):
            return os.path.abspath(path)
    return ""


def sync(source, dest=None):
    source = os.path.abspath(source)
    if not os.path.isfile(os.path.join(source, "README.md")):
        raise SystemExit(f"No README.md in {source}")
    dest = os.path.abspath(dest or freellm_catalog.SNAPSHOT_DIR)
    parent = os.path.dirname(dest)
    os.makedirs(parent, exist_ok=True)

    tmp = tempfile.mkdtemp(prefix=".free-llm-apis-", dir=parent)
    try:
        for name in FILES:
            path = os.path.join(source, name)
            if os.path.isfile(path):
                shutil.copy2(path, os.path.join(tmp, name))
        for name in DIRECTORIES:
            path = os.path.join(source, name)
            if os.path.isdir(path):
                shutil.copytree(path, os.path.join(tmp, name))

        files = 0
        total = 0
        for base, _dirs, names in os.walk(tmp):
            for name in names:
                files += 1
                total += os.path.getsize(os.path.join(base, name))

        snapshot = {
            "source": freellm_catalog.SOURCE,
            "branch": _git(source, "rev-parse", "--abbrev-ref", "HEAD") or "main",
            "commit": _git(source, "rev-parse", "HEAD"),
            "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "files": files,
            "bytes": total,
        }
        with open(os.path.join(tmp, "SNAPSHOT.json"), "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2)
            fh.write("\n")

        backup = dest + ".old"
        if os.path.exists(dest):
            shutil.rmtree(backup, ignore_errors=True)
            os.replace(dest, backup)
        os.replace(tmp, dest)
        shutil.rmtree(backup, ignore_errors=True)
        _normalize_modes(dest)
        return snapshot
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="", help="Checkout to copy from")
    parser.add_argument("--dest", default="", help="Snapshot directory to replace")
    args = parser.parse_args()

    source = args.source or os.environ.get("DENNIS_FREELLM_REPO", "") or _default_source()
    if not source:
        raise SystemExit(
            "No awesome-free-llm-apis checkout found. Pass --source or set DENNIS_FREELLM_REPO."
        )
    snapshot = sync(source, args.dest or None)
    print(json.dumps(snapshot, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
