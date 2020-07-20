#!/bin/bash

set -o errexit -o pipefail

SYSROOT_DIR="${PREFIX}"/aarch64-conda_cos7-linux-gnu/sysroot

mkdir -p "${SYSROOT_DIR}"
if [[ -d usr/lib ]]; then
  if [[ ! -d lib ]]; then
    ln -s usr/lib lib
  fi
fi
if [[ -d usr/lib64 ]]; then
  if [[ ! -d lib64 ]]; then
    ln -s usr/lib64 lib64
  fi
fi
pushd ${SRC_DIR}/binary > /dev/null 2>&1
rsync -K -a . "${SYSROOT_DIR}"
popd

pushd ${SYSROOT_DIR}/usr/bin
rm -f pango-querymodules-64
echo -e '#!/usr/bin/env bash\n exit 0' > pango-querymodules-64
chmod u+x pango-querymodules-64
popd
