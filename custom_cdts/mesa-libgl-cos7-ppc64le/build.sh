#!/bin/bash

set -o errexit -o pipefail

mkdir -p "${PREFIX}"/powerpc64le-conda-linux-gnu/sysroot
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
pushd "${PREFIX}"/powerpc64le-conda-linux-gnu/sysroot > /dev/null 2>&1
cp -Rf "${SRC_DIR}"/binary/* .

pushd usr/lib64
rm libGLX_system.so.0
ln -s libGLX_mesa.so.0 libGLX_system.so.0
popd

popd
