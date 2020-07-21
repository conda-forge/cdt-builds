#!/bin/bash

set -o errexit -o pipefail

SYSROOT_DIR="${PREFIX}"/aarch64-conda-linux-gnu/sysroot

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
pushd ${SYSROOT_DIR}/etc/pki/java
  rm -f cacerts
  mkdir -p ${SYSROOT_DIR}/etc/pki/ca-trust/extracted/java/cacerts
  ln -s ${SYSROOT_DIR}/etc/pki/ca-trust/extracted/java/cacerts ${SYSROOT_DIR}/etc/pki/java/cacerts
popd

pushd ${SYSROOT_DIR}/etc/pki/tls
  rm -f cert.pem
  echo "PLACEHOLDER"> ${SYSROOT_DIR}/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem
  ln -s ${SYSROOT_DIR}/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem ${SYSROOT_DIR}/etc/pki/tls/cert.pem
popd

pushd ${SYSROOT_DIR}/etc/pki/tls/certs
  rm -f ca-bundle.crt
  ln -s ${SYSROOT_DIR}/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem ${SYSROOT_DIR}/etc/pki/tls/certs/ca-bundle.crt
popd

pushd ${SYSROOT_DIR}/etc/pki/tls/certs
  rm ca-bundle.trust.crt
  echo "PLACEHOLDER"> ${SYSROOT_DIR}/etc/pki/ca-trust/extracted/openssl/ca-bundle.trust.crt
  ln -s ${SYSROOT_DIR}/etc/pki/ca-trust/extracted/openssl/ca-bundle.trust.crt ${SYSROOT_DIR}/etc/pki/tls/certs/ca-bundle.trust.crt
popd


# CDT BUILD APPENDED
