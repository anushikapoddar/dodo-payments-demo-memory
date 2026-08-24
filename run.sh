#!/usr/bin/env bash
# Merchant Risk Memory -- zero-dependency demo. Requires only Python 3.11+.
set -euo pipefail
cd "$(dirname "$0")"
if command -v python3.11 >/dev/null 2>&1; then
  exec python3.11 -m riskmemory.server "$@"
fi
exec python3 -m riskmemory.server "$@"
