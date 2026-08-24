#!/usr/bin/env bash
# Merchant Risk Memory -- zero-dependency demo. Requires only Python 3.11+.
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -m riskmemory.server "$@"
