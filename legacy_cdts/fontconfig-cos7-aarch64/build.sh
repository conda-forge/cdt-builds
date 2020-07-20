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

pushd ${SYSROOT_DIR}/etc/fonts/conf.d
for lnk in $(find . -maxdepth 1 -type l | xargs basename); do
  lnk_dst=$(readlink ${lnk})
  if [[ ${lnk_dst:0:1} == "/" ]]; then
    rm -f ${lnk}
    ln -s ${SYSROOT_DIR}${lnk_dst} ${SYSROOT_DIR}/etc/fonts/conf.d/${lnk}
  fi
done
popd
