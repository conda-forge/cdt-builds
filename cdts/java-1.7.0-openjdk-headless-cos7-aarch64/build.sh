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
popd > /dev/null 2>&1

# START OF INSERTED BUILD APPENDS


# CONDA-FORGE BUILD APPEND
jvm_slug=$(compgen -G "${SYSROOT_DIR}/usr/lib/jvm/java-1.7.0-openjdk-*")
jvm_slug=$(basename ${jvm_slug})

pushd ${SYSROOT_DIR}/usr/lib/jvm/${jvm_slug}/jre-abrt > /dev/null 2>&1
rm -rf lib
ln -s ${SYSROOT_DIR}/usr/lib/jvm/${jvm_slug}/jre/lib ${SYSROOT_DIR}/usr/lib/jvm/${jvm_slug}/jre-abrt/lib
popd > /dev/null 2>&1

pushd ${SYSROOT_DIR}/usr/lib/jvm/${jvm_slug}/jre/lib/security > /dev/null 2>&1
mkdir -p ../../../../../../../etc/pki/java/cacerts
popd > /dev/null 2>&1


# END OF INSERTED BUILD APPENDS

# this code makes sure that any symlinks are relative and their targets exist
# the CDT would fail at test time, but doing it here produces useful error
# messages for fixing things
error_code=0
for blnk in $(find ./binary -type l); do
  # loop is over symlinks in the RPM, so get the path in the sysroot
  lnk=${SYSROOT_DIR}${blnk#"./binary"}

  # if it is not a link in the sysroot, move on
  if [[ ! -L ${lnk} ]]; then
    continue
  fi

  # get the link dir and the destination of the link
  lnk_dir=$(dirname ${lnk})
  lnk_dst_nm=$(readlink ${lnk})
  if [[ ${lnk_dst_nm:0:1} == "/" ]]; then
    lnk_dst=${lnk_dst_nm}
  else
    lnk_dst="${lnk_dir}/${lnk_dst_nm}"
  fi

  # now test if it is absolute and relative to the system and not the PREFIX
  # also test if the dest file exists
  if [[ ${lnk_dst_nm:0:1} == "/" ]] && [[ ! ${lnk_dst_nm} == ${SYSROOT_DIR}* ]]; then
    echo "***WARNING ABSOLUTE SYMLINK***: ${lnk} -> ${lnk_dst}"
    error_code=1
  elif [[ ! -e "${lnk_dst}" ]]; then
     echo "***WARNING SYMLINK W/O DESTINATION: ${lnk} -> ${lnk_dst}"
     error_code=1
  fi
done

exit ${error_code}
