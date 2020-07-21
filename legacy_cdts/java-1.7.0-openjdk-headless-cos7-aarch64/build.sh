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


# CDT BUILD APPENDED
pushd ${SYSROOT_DIR}/usr/lib/jvm/java-1.7.0-openjdk-1.7.0.221-2.6.18.1.el7.aarch64/jre-abrt
rm -rf lib
ln -s ${SYSROOT_DIR}/usr/lib/jvm/java-1.7.0-openjdk-1.7.0.221-2.6.18.1.el7.aarch64/jre/lib ${SYSROOT_DIR}/usr/lib/jvm/java-1.7.0-openjdk-1.7.0.221-2.6.18.1.el7.aarch64/jre-abrt/lib
popd

pushd ${SYSROOT_DIR}/usr/lib/jvm/java-1.7.0-openjdk-1.7.0.221-2.6.18.1.el7.aarch64/jre/lib/security
mkdir -p ../../../../../../../etc/pki/java/cacerts
popd


# CDT BUILD APPENDED
