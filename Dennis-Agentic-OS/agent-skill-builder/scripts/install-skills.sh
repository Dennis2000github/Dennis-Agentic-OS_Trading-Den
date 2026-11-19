#!/usr/bin/env bash
# Link workspace skills into ~/.claude/skills so Claude Code can load them.
# Usage: install-skills.sh [skill-name ...]   (default: all)
set -euo pipefail
# shellcheck source=scripts/_env.sh
. "$(dirname "$0")/_env.sh"

DEST="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
mkdir -p "$DEST"

names=("$@")
if [ "${#names[@]}" -eq 0 ]; then
  while IFS= read -r d; do
    names+=("$(basename "$d")")
  done < <(find "$ROOT/skills" -mindepth 1 -maxdepth 1 -type d | sort)
fi

for name in "${names[@]}"; do
  src="$ROOT/skills/$name"
  if [ ! -f "$src/SKILL.md" ]; then
    echo "skip: $name (no SKILL.md at $src)" >&2
    continue
  fi
  target="$DEST/$name"
  if [ -L "$target" ]; then
    rm -f "$target"
  elif [ -e "$target" ]; then
    backup="$target.bak.$(date +%s)"
    mv "$target" "$backup"
    echo "backed up existing $name -> $backup"
  fi
  ln -s "$src" "$target"
  echo "linked: $target -> $src"
done

echo
echo "Installed skills in $DEST:"
ls -1 "$DEST"
