#!/bin/bash

set -o errexit -o pipefail

SYSROOT_DIR="${PREFIX}"/x86_64-conda-linux-gnu/sysroot

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


# CDT BUILD APPENDED
pushd ${SYSROOT_DIR}/usr/share/java-utils
  rm -f abs2rel.sh
  ln -s ${SYSROOT_DIR}/usr/bin/abs2rel ${SYSROOT_DIR}/usr/share/java-utils/abs2rel.sh
popd


# CDT BUILD APPENDED
