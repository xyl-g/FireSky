#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ] || [ -z "${1:-}" ]; then
  echo "usage: $0 DEFINITIONS_DIR" >&2
  exit 2
fi

DEFS_DIR="$1"
if [ ! -d "$DEFS_DIR" ] || [ ! -d "$DEFS_DIR/grib2" ] || [ ! -f "$DEFS_DIR/boot.def" ]; then
  echo "error: not a valid ecCodes definitions directory: $DEFS_DIR" >&2
  exit 1
fi

UNUSED_DIRS=(
  bufr
  grib1
  grib3
  taf
  metar
  gts
  hdf5
  cdf
  tide
  budg
  diag
  wrap
)

for dir in "${UNUSED_DIRS[@]}"; do
  rm -rf -- "$DEFS_DIR/$dir"
done
