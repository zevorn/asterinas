#!/bin/bash
# Asterinas GDB Debug Helper Scripts — Setup
#
# This script configures ~/.gdbinit so that GDB auto-loads the Asterinas
# GDB helper scripts when launched from the project root.
#
# Usage:
#   ./scripts/gdb/setup.sh
#
# What it does:
#   1. Adds the project's scripts/gdb/ to ~/.gdbinit's auto-load safe-path
#   2. Creates a .gdbinit at the project root that sources asterinas-gdb.py
#
# This is idempotent — safe to run multiple times.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
GDBINIT_GLOBAL="$HOME/.gdbinit"
GDBINIT_LOCAL="$PROJECT_ROOT/.gdbinit"
GLOBAL_MARKER="# asterinas-gdb-auto-load"
LOCAL_BEGIN="# >>> asterinas-gdb-auto-load >>>"
LOCAL_END="# <<< asterinas-gdb-auto-load <<<"

# --- Step 1: Add auto-load safe-path to ~/.gdbinit ---

SAFE_PATH_LINE="add-auto-load-safe-path $PROJECT_ROOT/.gdbinit $GLOBAL_MARKER"

if [ -f "$GDBINIT_GLOBAL" ] && grep -qF "$GLOBAL_MARKER" "$GDBINIT_GLOBAL"; then
    echo "[setup] ~/.gdbinit already configured (skipping)."
else
    echo "$SAFE_PATH_LINE" >> "$GDBINIT_GLOBAL"
    echo "[setup] Added auto-load safe-path to ~/.gdbinit"
fi

# --- Step 2: Update project-root .gdbinit without overwriting user content ---

ASTERINAS_BLOCK=$(cat <<'GDBINIT_EOF'
# >>> asterinas-gdb-auto-load >>>
# Asterinas GDB Debug Helper Scripts
python
import os
_gdbinit_dir = os.path.dirname(os.path.abspath(".gdbinit"))
_gdb_script = os.path.join(_gdbinit_dir, "scripts", "gdb", "asterinas-gdb.py")
if os.path.exists(_gdb_script):
    gdb.execute(f"source {_gdb_script}")
else:
    print(f"Warning: {_gdb_script} not found. Asterinas GDB helpers not loaded.")
end
# <<< asterinas-gdb-auto-load <<<
GDBINIT_EOF
)

if [ -f "$GDBINIT_LOCAL" ] && grep -qF "$LOCAL_BEGIN" "$GDBINIT_LOCAL"; then
    echo "[setup] $GDBINIT_LOCAL already contains the Asterinas auto-load block."
else
    if [ -f "$GDBINIT_LOCAL" ] && [ -s "$GDBINIT_LOCAL" ]; then
        printf '\n%s\n' "$ASTERINAS_BLOCK" >> "$GDBINIT_LOCAL"
    else
        printf '%s\n' "$ASTERINAS_BLOCK" > "$GDBINIT_LOCAL"
    fi
    echo "[setup] Updated $GDBINIT_LOCAL"
fi

# --- Step 3: Check for rust-gdb ---

if command -v rust-gdb &>/dev/null; then
    echo "[setup] rust-gdb found: $(command -v rust-gdb)"
    echo "[setup] Done. Use 'rust-gdb' instead of 'gdb' for full Rust pretty-printer support."
else
    echo "[setup] Note: rust-gdb not found. Pretty-printers will be auto-detected from rustc sysroot."
    echo "[setup] Done. Launch GDB from $PROJECT_ROOT to auto-load Asterinas helpers."
fi
