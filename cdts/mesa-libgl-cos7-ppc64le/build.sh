#!/bin/bash

set -o errexit -o pipefail

SYSROOT_DIR="${PREFIX}"/powerpc64le-conda-linux-gnu/sysroot

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
popd > /dev/null 2>&1

# START OF INSERTED BUILD APPENDS


# CONDA-FORGE BUILD APPEND
pushd ${SYSROOT_DIR}/usr/lib64 > /dev/null 2>&1
rm libGLX_system.so.0
ln -s libGLX_mesa.so.0 libGLX_system.so.0
popd > /dev/null 2>&1


# END OF INSERTED BUILD APPENDS

error_code=0
for blnk in $(find ./binary -type l); do
  lnk=${SYSROOT_DIR}${blnk#"./binary"}
  if [[ ! -L ${lnk} ]]; then
    continue
  fi
  lnk_dir=$(dirname ${lnk})
  lnk_dst_nm=$(readlink ${lnk})
  if [[ ${lnk_dst_nm:0:1} == "/" ]]; then
    lnk_dst=${lnk_dst_nm}
  else
    lnk_dst="${lnk_dir}/${lnk_dst_nm}"
  fi
  if [[ ${lnk_dst_nm:0:1} == "/" ]] && [[ ! ${lnk_dst_nm} == ${SYSROOT_DIR}* ]]; then
    echo "***WARNING ABSOLUTE SYMLINK***: ${lnk} -> ${lnk_dst}"
    error_code=1
  elif [[ ! -e "${lnk_dst}" ]]; then
     echo "***WARNING SYMLINK W/O DESTINATION: ${lnk} -> ${lnk_dst}"
     error_code=1
  fi
done

exit ${error_code}
