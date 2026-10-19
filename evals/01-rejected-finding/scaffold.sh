#!/usr/bin/env bash
# Throwaway project for this case: see ../_lib/scaffold.py.
set -euo pipefail
case_dir="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$case_dir/../_lib/scaffold.py" "$case_dir"
