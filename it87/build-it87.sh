#!/usr/bin/env bash
# Build it87.ko for the RUNNING TrueNAS kernel, inside a container (no compiler on the host).
# Run as root on the TrueNAS host. Re-run after every TrueNAS update that changes the kernel.
#
#   ./build-it87.sh [output_dir]        default output_dir: ./out
#
# Needs the host's kernel headers (TrueNAS ships them in /usr/src) mounted read-only.
set -euo pipefail

OUT="${1:-$(dirname "$(readlink -f "$0")")/out}"
HERE="$(dirname "$(readlink -f "$0")")"
KVER="$(uname -r)"
BUILD="$(readlink -f "/lib/modules/${KVER}/build")"

[ -d "$BUILD" ] || { echo "No kernel headers at $BUILD" >&2; exit 1; }
mkdir -p "$OUT"

docker build -q -f "$HERE/Dockerfile.it87" -t local/it87-build:latest "$HERE"
docker run --rm \
  -v /usr/src:/usr/src:ro -v /lib/modules:/lib/modules:ro -v "$OUT":/out \
  local/it87-build:latest \
  bash -c "cp -r /src /tmp/b && cd /tmp/b && make KERNEL_BUILD='$BUILD' && cp it87.ko /out/ && cat /COMMIT > /out/COMMIT"

echo "Built: $OUT/it87.ko  (kernel $KVER, source $(cat "$OUT/COMMIT"))"
sha256sum "$OUT/it87.ko"
echo "Load now (temporary):   insmod $OUT/it87.ko"
echo "Make it load at boot:   TrueNAS UI > System > Advanced > Init/Shutdown Scripts > Add > Command"
echo "                        insmod $OUT/it87.ko     (When: Post Init)"
