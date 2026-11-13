#!/bin/bash
# Copy the decoder closure and collect installed package versions and license files.
set -euo pipefail
DEST="${1:?usage: bundle_macos_eccodes.sh RESOURCE_DIR}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec node "$ROOT/apps/shared-web/scripts/bundle-macos-eccodes.mjs" "$DEST"
