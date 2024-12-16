"""This file is from conda-build with our own edits.

The conda-build license is:

    Except where noted below, conda is released under the following terms:

    (c) 2012 Continuum Analytics, Inc. / http://continuum.io
    All Rights Reserved

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:
        * Redistributions of source code must retain the above copyright
          notice, this list of conditions and the following disclaimer.
        * Redistributions in binary form must reproduce the above copyright
          notice, this list of conditions and the following disclaimer in the
          documentation and/or other materials provided with the distribution.
        * Neither the name of Continuum Analytics, Inc. nor the
          names of its contributors may be used to endorse or promote products
          derived from this software without specific prior written permission.

    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
    ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
    WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
    DISCLAIMED. IN NO EVENT SHALL CONTINUUM ANALYTICS BE LIABLE FOR ANY
    DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
    (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
    LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
    ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
    (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
    SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.


    Exceptions
    ==========

    versioneer.py is Public Domain
"""
import os
from copy import copy

try:
    import cPickle as pickle
except ImportError:
    import pickle as pickle

from contextlib import contextmanager
import gzip
import hashlib
import io
import logging
from os import chmod, makedirs
from os.path import basename, dirname, exists, join, splitext
import re
import sys
import tempfile
import subprocess


@contextmanager
def disable_traceback():
    default_value = getattr(sys, "tracebacklimit", 1000)  # 1000 == default
    sys.tracebacklimit = 0
    yield
    sys.tracebacklimit = default_value  # revert changes


import click
from conda_build.source import download_to_cache
from conda_build.license_family import guess_license_family
from conda_build.config import Config

# try to import C dumper
try:
    from yaml import CSafeDumper as SafeDumper
except ImportError:
    from yaml import SafeDumper
import yaml

from requests import request
from six import string_types
from textwrap import wrap
from xml.etree import cElementTree as ET

# we sometimes hit infinite recursions so we track every recipe made here
MADE_RECIPES = set()

# This is used in two places
default_architecture = "x86_64"
default_distro = "centos7"

RPM_META = """\
package:
  name: {packagename}
  version: {version}

source:
  - url: {rpmurl}
    {checksum_name}: {checksum}
    no_hoist: true
    folder: binary
  # - url: {srcrpmurl}
  #   no_hoist: true
  #   folder: source

build:
  number: {build_number}
  noarch: generic
  binary_relocation: False
  detect_binary_files_with_prefix: False
  missing_dso_whitelist:
    - '*'
  # this skip is here because we need different package hashes per distro.
  # we therefore list all possible values in CBC and skip those we don't want;
  # use in a selector ensures that the `distro` variable shows up in the hash
  skip: true  # [distro != "{distro_name}"]

{depends}

test:
  commands:
    - echo "it installs!"

about:
  home: {home}
  license: {license}
  license_family: {license_family}
  license_file: {{{{ SRC_DIR }}}}/binary{license_file}
  summary: {summary}
  description: {description}

extra:
  recipe-maintainers:
    - conda-forge/help-cdts
"""


BUILDSH = """\
#!/bin/bash

set -o errexit -o pipefail

SYSROOT_DIR="${{PREFIX}}"/{host_machine}/sysroot

mkdir -p "${{SYSROOT_DIR}}"
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
pushd ${{SRC_DIR}}/binary > /dev/null 2>&1
rsync -K -a . "${{SYSROOT_DIR}}"
popd > /dev/null 2>&1

# START OF INSERTED BUILD APPENDS

# END OF INSERTED BUILD APPENDS

# this code makes sure that any symlinks are relative and their targets exist
# the CDT would fail at test time, but doing it here produces useful error
# messages for fixing things
error_code=0
for blnk in $(find ./binary -type l); do
  # loop is over symlinks in the RPM, so get the path in the sysroot
  lnk=${{SYSROOT_DIR}}${{blnk#"./binary"}}

  # if it is not a link in the sysroot, move on
  if [[ ! -L ${{lnk}} ]]; then
    continue
  fi

  # get the link dir and the destination of the link
  lnk_dir=$(dirname ${{lnk}})
  lnk_dst_nm=$(readlink ${{lnk}})
  if [[ ${{lnk_dst_nm:0:1}} == "/" ]]; then
    lnk_dst=${{lnk_dst_nm}}
  else
    lnk_dst="${{lnk_dir}}/${{lnk_dst_nm}}"
  fi

  # now test if it is absolute and relative to the system and not the PREFIX
  # also test if the dest file exists
  if [[ ${{lnk_dst_nm:0:1}} == "/" ]] && [[ ! ${{lnk_dst_nm}} == ${{SYSROOT_DIR}}* ]]; then
    echo "***WARNING ABSOLUTE SYMLINK***: ${{lnk}} -> ${{lnk_dst}}"
    error_code=1
  elif [[ ! -e "${{lnk_dst}}" ]]; then
     echo "***WARNING SYMLINK W/O DESTINATION: ${{lnk}} -> ${{lnk_dst}}"
     error_code=1
  fi
done

exit ${{error_code}}
"""  # noqa


def _gen_cdts():
    return dict(
        {
            "centos7": {
                "full_name": "centos7",
                "short_name": "conda",
                "base_url": "https://vault.centos.org/{extra_url_chunk}7.9.2009/os/{architecture}/Packages/",  # noqa
                "sbase_url": "https://vault.centos.org/{extra_url_chunk}7.9.2009/os/Source/SPackages/",
                "repomd_url": "https://vault.centos.org/{extra_url_chunk}7.9.2009/os/{architecture}/repodata/repomd.xml",  # noqa
                # not relevant for centos7
                "extra_subfolders": [],
                "host_machine": "{gnu_architecture}-conda-linux-gnu",
                "host_subdir": "linux-{conda_architecture}",
                "checksummer": hashlib.sha256,
                "checksummer_name": "sha256",
                # Some macros are defined in /etc/rpm/macros.* but I cannot find where
                # these ones are defined. Also, rpm --eval "%{gdk_pixbuf_base_version}"
                # gives nothing nor does rpm --showrc | grep gdk
                "macros": {"pyver": "2.6.6", "gdk_pixbuf_base_version": "2.24.1"},
                "allow_missing_sources": True,
                "glibc_ver": "2.17",
            },
            "alma8": {
                "full_name": "alma8",
                "short_name": "conda",
                "base_url": "https://vault.almalinux.org/8.9/{subfolder}/{architecture}/os/Packages/",  # noqa
                "sbase_url": "https://vault.almalinux.org/8.9/{subfolder}/Source/Packages/",
                "repomd_url": "https://vault.almalinux.org/8.9/{subfolder}/{architecture}/os/repodata/repomd.xml",  # noqa
                "extra_subfolders": ["PowerTools"],
                "host_machine": "{gnu_architecture}-conda-linux-gnu",
                "host_subdir": "linux-{conda_architecture}",
                "checksummer": hashlib.sha256,
                "checksummer_name": "sha256",
                "macros": {},
                "allow_missing_sources": True,
                "glibc_ver": "2.28",
            },
            "alma9": {
                "full_name": "alma9",
                "short_name": "conda",
                "base_url": "https://vault.almalinux.org/9.4/{subfolder}/{architecture}/os/Packages/",  # noqa
                "sbase_url": "https://vault.almalinux.org/9.4/{subfolder}/Source/Packages/",
                "repomd_url": "https://vault.almalinux.org/9.4/{subfolder}/{architecture}/os/repodata/repomd.xml",  # noqa
                "extra_subfolders": ["CRB", "devel"],
                "host_machine": "{gnu_architecture}-conda-linux-gnu",
                "host_subdir": "linux-{conda_architecture}",
                "checksummer": hashlib.sha256,
                "checksummer_name": "sha256",
                "macros": {},
                "allow_missing_sources": True,
                "glibc_ver": "2.34",
            },
        }
    )


def yaml_quote_string(string):
    """
    Quote a string for use in YAML.

    We can't just use yaml.dump because it adds ellipses to the end of the
    string, and it in general doesn't handle being placed inside an existing
    document very well.

    Note that this function is NOT general.
    """
    return (
        yaml.dump(string, Dumper=SafeDumper)
        .replace("\n...\n", "")
        .replace("\n", "\n  ")
    )


def cache_file(src_cache, url, fn=None, checksummer=hashlib.sha256):
    if fn:
        source = dict({"url": url, "fn": fn})
    else:
        source = dict({"url": url})
    # avoid getting spammed by failed downloads
    logging.disable(logging.WARN)
    with disable_traceback():
        cached_path, _ = download_to_cache(src_cache, "", source)
    # restore previous defaults
    logging.disable(logging.NOTSET)
    csum = checksummer()
    csum.update(open(cached_path, "rb").read())
    csumstr = csum.hexdigest()
    return cached_path, csumstr


def rpm_filename_split(rpmfilename):
    base, _ = splitext(rpmfilename)
    release_platform = base.split("-")[-1]
    parts = release_platform.split(".")
    if len(parts) == 2:
        release, platform = parts[0], parts[1]
    elif len(parts) > 2:
        release, platform = ".".join(parts[0:len(parts) - 1]), ".".join(parts[-1:])
    else:
        print("ERROR: Cannot figure out the release and platform for {}".format(base))
    name_version = base.split("-")[0:-1]
    version = name_version[-1]
    rpm_name = "-".join(name_version[0:len(name_version) - 1])
    return rpm_name, version, release, platform


def rpm_split_url_and_cache(rpm_url, src_cache):
    cached_path, sha256str = cache_file(src_cache, rpm_url)
    rpm_name, version, release, platform = rpm_filename_split(basename(rpm_url))
    return rpm_name, version, release, platform, cached_path, sha256str


def rpm_filename_generate(rpm_name, version, release, platform):
    return "{}-{}-{}.{}.rpm".format(rpm_name, version, release, platform)


def rpm_url_generate(url_dirname, rpm_name, version, release, platform, src_cache):
    """
    Forms the URL and also attempts to cache it to verify it exists.
    """
    result = rpm_filename_generate(rpm_name, version, release, platform)
    url = join(url_dirname, result)
    path, _ = download_to_cache(src_cache, "", dict({"url": url}))
    assert path, "Failed to cache generated RPM url {}".format(result)
    return url


def find_repo_entry_and_arch(repo_primary, architectures, depend):
    dep_name = depend["name"]
    found_package_name = ""
    try:
        # Try direct lookup first.
        found_package = repo_primary[dep_name]
        found_package_name = dep_name
    except Exception:
        # Look through the provides of all packages.
        for name, package in repo_primary.items():
            for arch in architectures:
                if arch in package:
                    if "provides" in package[arch]:
                        for provide in package[arch]["provides"]:
                            if provide["name"] == dep_name:
                                print("Found {} in {}".format(dep_name, name))
                                found_package = package
                                found_package_name = name
                                break

    if found_package_name == "":
        print(
            "WARNING: Did not find package called (or another one providing) {}".format(
                dep_name
            )
        )  # noqa
        return None, None, None

    chosen_arch = None
    for arch in architectures:
        if arch in found_package:
            chosen_arch = arch
            break
    if not chosen_arch:
        return None, None, None
    entry = found_package[chosen_arch]
    return entry, found_package_name, chosen_arch


str_flags_to_conda_version_spec = dict(
    {"LT": "<", "LE": "<=", "EQ": "==", "GE": ">=", "GT": ">"}
)


def dictify(r, root=True):
    if root:
        return {r.tag: dictify(r, False)}
    d = copy(r.attrib)
    if r.text:
        d["_text"] = r.text
    for x in r.findall("./*"):
        if x.tag not in d:
            d[x.tag] = []
        d[x.tag].append(dictify(x, False))
    return d


def dictify_pickled(xml_file, src_cache, dict_massager=None, cdt=None):
    pickled = xml_file + ".p"
    if exists(pickled):
        return pickle.load(open(pickled, "rb"))
    with io.open(xml_file, "r", encoding="utf-8") as xf:
        xmlstring = xf.read()
        # Remove the global namespace.
        xmlstring = re.sub(r'\sxmlns="[^"]+"', r"", xmlstring, count=1)
        # Replace sub-namespaces with their names.
        xmlstring = re.sub(r'\sxmlns:([a-zA-Z]*)="[^"]+"', r' xmlns:\1="\1"', xmlstring)
        root = ET.fromstring(xmlstring.encode("utf-8"))
        result = dictify(root)
        if dict_massager:
            result = dict_massager(result, src_cache, cdt)
        pickle.dump(result, open(pickled, "wb"))
        return result


def get_repo_dict(repomd_url, data_type, dict_massager, cdt, src_cache):
    xmlstring = request("get", repomd_url).content
    # Remove the default namespace definition (xmlns="http://some/namespace")
    xmlstring = re.sub(br'\sxmlns="[^"]+"', b"", xmlstring, count=1)  # noqa
    repomd = ET.fromstring(xmlstring)
    for child in repomd.findall("*[@type='{}']".format(data_type)):
        open_csum = child.findall("open-checksum")[0].text
        xml_file = join(src_cache, open_csum)
        try:
            xml_file, xml_csum = cache_file(
                src_cache, xml_file, None, cdt["checksummer"]
            )
        except Exception:
            csum = child.findall("checksum")[0].text
            location = child.findall("location")[0].attrib["href"]
            xmlgz_file = dirname(dirname(repomd_url)) + "/" + location
            cached_path, cached_csum = cache_file(
                src_cache, xmlgz_file, None, cdt["checksummer"]
            )
            assert (
                csum == cached_csum
            ), "Checksum for {} does not match value in {}".format(
                xmlgz_file, repomd_url
            )
            with gzip.open(cached_path, "rb") as gz:
                xml_content = gz.read()
                xml_csum = cdt["checksummer"]()
                xml_csum.update(xml_content)
                xml_csum = xml_csum.hexdigest()
                if xml_csum == open_csum:
                    with open(xml_file, "wb") as xml:
                        xml.write(xml_content)
                else:
                    print(
                        "ERROR: Checksum of uncompressed file {} does not match".format(
                            xmlgz_file
                        )
                    )  # noqa
        return dictify_pickled(xml_file, src_cache, dict_massager, cdt)
    return dict({})


def massage_primary_requires(requires, cdt):
    for require in requires:
        require["name"] = require["name"]
        if "flags" in require:
            require["flags"] = str_flags_to_conda_version_spec[require["flags"]]
        else:
            require["flags"] = None
        if "ver" in require:
            if "%" in require["ver"]:
                require["ver"] = require["ver"].replace("%", "")
                if not require["ver"].startswith("{"):
                    require["ver"] = "{" + require["ver"]
                if not require["ver"].endswith("}"):
                    require["ver"] = require["ver"] + "}"
                require["ver"] = require["ver"].format(**cdt["macros"])
    return requires


def massage_primary(repo_primary, src_cache, cdt):
    """
    Massages the result of dictify() into a less cumbersome form.
    In particular:
    1. There are many lists that can only be of length one that
       don't need to be lists at all.
    2. The '_text' entries need to go away.
    3. The real information starts at ['metadata']['package']
    4. We want the top-level key to be the package name and under
       that, an entry for each arch for which the package exists.
    """

    new_dict = dict({})
    for package in repo_primary["metadata"]["package"]:
        name = package["name"][0]["_text"]
        arch = package["arch"][0]["_text"]
        if arch == "src":
            continue
        checksum = package["checksum"][0]["_text"]
        source = package["format"][0]["{rpm}sourcerpm"][0]["_text"]
        # If you need to check if the sources exist (perhaps you've got the
        # source URL wrong
        # or the distro has forgotten to copy them?):
        # import requests
        # sbase_url = cdt['sbase_url']
        # surl = sbase_url + source
        # print("{} {}".format(requests.head(surl).status_code, surl))
        location = package["location"][0]["href"]
        version = package["version"][0]
        summary = package["summary"][0]["_text"]
        try:
            description = package["description"][0]["_text"]
        except Exception:
            description = "NA"
        if "_text" in package["url"][0]:
            url = package["url"][0]["_text"]
        else:
            url = ""
        license = package["format"][0]["{rpm}license"][0]["_text"]
        try:
            provides = package["format"][0]["{rpm}provides"][0]["{rpm}entry"]
            provides = massage_primary_requires(provides, cdt)
        except Exception:
            provides = []
        try:
            requires = package["format"][0]["{rpm}requires"][0]["{rpm}entry"]
            requires = massage_primary_requires(requires, cdt)
        except Exception:
            requires = []
        new_package = dict(
            {
                "checksum": checksum,
                "location": location,
                "home": url,
                "source": source,
                "version": version,
                "summary": yaml_quote_string(summary),
                "description": description,
                "license": license,
                "provides": provides,
                "requires": requires,
            }
        )
        if name in new_dict:
            new_dict[name][arch] = new_package
        else:
            new_dict[name] = dict({arch: new_package})
    return new_dict


def valid_depends(depends):
    name = depends["name"]
    str_flags = depends["flags"]
    # the or name.endswith w/ "(x86-64)" "(aarch-64)" etc is a
    # hack around bad repo data upstream for libXtst-devel
    if (
        not name.startswith("rpmlib(")
        and not name.startswith("config(")
        and not name.startswith("pkgconfig(")
        and not name.startswith("/")
        and name != "rtld(GNU_HASH)"
        and ".so" not in name
        and (
            "(" not in name
            or name.endswith("(x86-64)")
            or name.endswith("(aarch-64)")
            or name.endswith("(ppc-64)")
        )
        and str_flags
    ):
        return True
    return False


def remap_license(rpm_license):
    mapping = {
        "gplv3": "GPL-3.0-only",
        "gplv2": "GPL-2.0-only",
        "lgplv2+": "LGPL-2.0-or-later",
        "gplv2+": "GPL-2.0-or-later",
        "public domain (uncopyrighted)": "Public-Domain",
        "public domain": "Public-Domain",
        "mit/x11": "MIT",
        "the open group license": "The Open Group License",
        "mplv2.0": "MPL-2.0",
    }
    l_rpm_license = rpm_license.lower()
    if l_rpm_license in mapping:
        license, family = (
            mapping[l_rpm_license],
            guess_license_family(mapping[l_rpm_license]),
        )
    else:
        license, family = rpm_license, guess_license_family(rpm_license)
    # Yuck:
    if family == "APACHE":
        family = "Apache"
    elif family == "PUBLIC-DOMAIN":
        family = "Public-Domain"
    elif family == "PROPRIETARY":
        family = "Proprietary"
    elif family == "OTHER":
        family = "Other"
    return license, family


def tidy_text(text, wrap_at=0):
    stripped = text.strip("'\"\n ")
    if wrap_at > 0:
        stripped = wrap(stripped, wrap_at)
    return stripped


def _test_rpm_for_license_file(rpm_pth, raise_on_not_found=False):
    c = subprocess.run(
        "rpm -qlp %s" % rpm_pth,
        shell=True,
        check=True,
        capture_output=True,
        text=True,
    )
    found = False
    for line in c.stdout.splitlines():
        fline = basename(line).lower()
        if any(c in fline for c in ["license", "licence", "copyright", "copying"]):
            found = True
            license_file = line

    if not found:
        if raise_on_not_found:
            raise RuntimeError("could not find the license file in the RPM!")
        else:
            print(
                "WARNING: could not find a suitable "
                "license file in the RPM %s!" % os.path.basename(rpm_pth)
            )
            license_file = "/LICENSE_NOT_FOUND"

    return license_file


def write_conda_recipes(
    recursive,
    repo_primary,
    package,
    architectures,
    cdt,
    output_dir,
    src_cache,
    build_number,
    conda_forge_style,
    build_number_bump,
):

    if build_number_bump is None:
        _extra_build_num_str = ""
    else:
        _extra_build_num_str = " + %s" % build_number_bump

    build_number_jinja2 = (
        "{{ cdt_build_number|int + 1000%s }}" % _extra_build_num_str
    )

    entry, entry_name, arch = find_repo_entry_and_arch(
        repo_primary, architectures, dict({"name": package})
    )
    if not entry:
        return

    arch = architectures[0] if arch == "noarch" else arch
    package = entry_name
    package_l = package.lower().replace("+", "x")
    sn = cdt["short_name"] + "-" + arch
    package_cdt_name = package_l + "-" + sn

    global MADE_RECIPES
    if package_cdt_name in MADE_RECIPES:
        return package
    else:
        MADE_RECIPES.add(package_cdt_name)

    rpm_url = dirname(dirname(cdt["base_url"])) + "/" + entry["location"]
    srpm_url = cdt["sbase_url"] + entry["source"]
    _, _, _, _, rpm_pth, sha256str = rpm_split_url_and_cache(rpm_url, src_cache)

    license_file = _test_rpm_for_license_file(rpm_pth)

    # we are not using these so do not bother to hash
    # try:
    #     # We ignore the hash of source RPMs since they
    #     # are not given in the source repository data.
    #     _, _, _, _, _, _ = rpm_split_url_and_cache(srpm_url, src_cache)
    # except Exception:
    #     # Just pretend the binaries are sources.
    #     if "allow_missing_sources" in cdt:
    #         srpm_url = rpm_url
    #     else:
    #         raise
    depends = [required for required in entry["requires"] if valid_depends(required)]

    if package in cdt["dependency_add"]:
        for missing_dep in cdt["dependency_add"][package]:
            e_missing, e_name_missing, _ = find_repo_entry_and_arch(
                repo_primary, architectures, dict({"name": missing_dep})
            )
            if e_missing:
                for provides in e_missing["provides"]:
                    if provides["name"] == e_name_missing:
                        copy_provides = copy(provides)
                        if "rel" in copy_provides:
                            del copy_provides["rel"]
                        depends.append(copy_provides)
            else:
                print(
                    "WARNING: Additional dependency of {}, {} not found".format(
                        package, missing_dep
                    )
                )

    for depend in depends:
        # replace of "(x86-64)", "(aarch-64)"
        # hack around bad repo data upstream for libXtst-devel
        for tail in ["(x86-64)", "(aarch-64)", "(ppc-64)"]:
            if depend["name"].endswith(tail):
                depend["name"] = depend["name"][:-len(tail)]

        dep_entry, dep_name, dep_arch = find_repo_entry_and_arch(
            repo_primary, architectures, depend
        )
        dep_arch = architectures[0] if dep_arch == "noarch" else dep_arch

        depend["arch"] = dep_arch
        # Because something else may provide a substitute for the wanted package
        # we need to also overwrite the versions with those of the provider, e.g.
        # libjpeg 6b is provided by libjpeg-turbo 1.2.1
        if depend["name"] != dep_name and "version" in dep_entry:
            if "ver" in dep_entry["version"]:
                depend["ver"] = dep_entry["version"]["ver"]
            if "epoch" in dep_entry["version"]:
                depend["epoch"] = dep_entry["version"]["epoch"]
        if recursive:
            depend["name"] = write_conda_recipes(
                recursive,
                repo_primary,
                depend["name"],
                architectures,
                cdt,
                output_dir,
                src_cache,
                build_number,
                conda_forge_style,
                build_number_bump,
            )

    sn = cdt["short_name"] + "-" + arch
    dependsstr = ""
    if len(depends) or conda_forge_style:
        depends_specs = [
            "{}-{}-{} {}{} *_{}".format(
                depend["name"].lower().replace("+", "x"),
                cdt["short_name"],
                depend["arch"],
                depend["flags"],
                depend["ver"],
                build_number_jinja2,
            )
            for depend in depends
        ]
        dependsstr_part = "\n".join(
            ["    - {}".format(depends_spec) for depends_spec in depends_specs]
        )
        if len(dependsstr_part) > 0:
            dependsstr_build = "  build:\n" + dependsstr_part + "\n"
            dependsstr_host = "  host:\n" + dependsstr_part + "\n"
            dependsstr_run = "  run:\n" + dependsstr_part
        else:
            dependsstr_build = ""
            dependsstr_host = ""
            dependsstr_run = ""

        if conda_forge_style:
            # fixup run w/ sysroot
            if len(dependsstr_run) > 0:
                dependsstr_run += "\n"

            if len(dependsstr_run) == 0:
                dependsstr_run = "  run:\n"
            dependsstr_run += (
                "    - "
                "sysroot_%s %s.*" % (
                    cdt["host_subdir"], cdt["glibc_ver"]))

            # put sysroot in host
            if len(dependsstr_host) == 0:
                dependsstr_host = "  host:\n"
            dependsstr_host += (
                "    - "
                "sysroot_%s %s.*\n" % (
                    cdt["host_subdir"], cdt["glibc_ver"]))

        dependsstr = (
            "requirements:\n" + dependsstr_build + dependsstr_host + dependsstr_run
        )

    package_l = package.lower().replace("+", "x")
    package_cdt_name = package_l + "-" + sn
    license, license_family = remap_license(entry["license"])
    d = dict(
        {
            "version": entry["version"]["ver"],
            "build_number": build_number_jinja2,
            "license_file": license_file,
            "packagename": package_cdt_name,
            "distro_name": cdt["full_name"],
            "host_machine": cdt["host_machine"],
            "depends": dependsstr,
            "rpmurl": rpm_url,
            "srcrpmurl": srpm_url,
            "home": entry["home"] or package_cdt_name,
            "license": license,
            "license_family": license_family,
            "checksum_name": cdt["checksummer_name"],
            "checksum": entry["checksum"],
            "summary": '"(CDT) ' + tidy_text(entry["summary"]) + '"',
            "description": "|\n        "
            + "\n        ".join(tidy_text(entry["description"], 78)),  # noqa
            # Cheeky workaround.  I use ${PREFIX},
            # ${PWD}, ${RPM} and ${RECIPE_DIR} in
            # BUILDSH and they get interpreted as
            # format string tokens so bounce them
            # back.
            # or you can use double curly brackets ${{PREFIX}}
            # "PREFIX": "{PREFIX}",
            # "RPM": "{RPM}",
            # "PWD": "{PWD}",
            # "RECIPE_DIR": "{RECIPE_DIR}",
            # "SRC_DIR": "{SRC_DIR}",
            # "SYSROOT_DIR": "{SYSROOT_DIR}"
        }
    )
    # cannot use package_cdt_name for path, because the shortnames
    # may coincide, but we need to distinguish recipes per distro
    odir = join(output_dir, f"{package_l}-{cdt['full_name']}-{arch}")
    try:
        makedirs(odir)
    except Exception:
        pass
    with open(join(odir, "meta.yaml"), "w") as f:
        f.write(RPM_META.format(**d))
    buildsh = join(odir, "build.sh")
    with open(buildsh, "w") as f:
        chmod(buildsh, 0o755)
        f.write(BUILDSH.format(**d))
    return package


# How do we map conda names to RPM names? The issue would be if two distros
# name their RPMs differently we probably want to hide that away from users
# Do I want to pass just the package name, the CDT and the arch and rely on
# expansion to form the URL? I have been going backwards and forwards here.
def write_conda_recipe(
    packages,
    distro,
    output_dir,
    architecture,
    subfolder,
    recursive,
    dependency_add,
    config,
    build_number,
    conda_forge_style,
    cdt_info,
    build_number_bump,
):
    # conda architectures are `linux-{64,aarch64,ppc64le}`
    conda_architectures = dict({"x86_64": "64"})
    conda_architecture = conda_architectures.get(architecture, architecture)
    # gnu_architectures are those recognized by the canonical config.sub / config.guess
    # and crosstool-ng. They are returned from ${CC} -dumpmachine and are a part of the
    # sysroot.
    gnu_architectures = dict({"ppc64le": "powerpc64le"})
    gnu_architecture = gnu_architectures.get(architecture, architecture)
    # centos7 has an extra url-portion for non-x64
    extra_url_chunk = "" if architecture == "x86_64" else "altarch/"

    formatting_bits = dict(
        {
            "architecture": architecture,
            "conda_architecture": conda_architecture,
            "gnu_architecture": gnu_architecture,
            "extra_url_chunk": extra_url_chunk,
            "subfolder": subfolder,
        }
    )
    cdt = dict()
    for k, v in cdt_info[distro].items():
        if isinstance(v, string_types):
            cdt[k] = v.format(**formatting_bits)
        else:
            cdt[k] = v

    # Add undeclared dependencies. These can be baked into the global
    # CDTs dict, passed in on the commandline or a mixture of both.
    if "dependency_add" not in cdt:
        cdt["dependency_add"] = dict()
    if dependency_add:
        for package_and_missed_deps in dependency_add:
            as_list = package_and_missed_deps[0].split(",")
            if as_list[0] in cdt["dependency_add"]:
                cdt["dependency_add"][as_list[0]].extend(as_list[1:])
            else:
                cdt["dependency_add"][as_list[0]] = as_list[1:]

    repo_primary = {}
    base_subfolders = ["BaseOS", "AppStream"]
    extra_subfolders = cdt_info[distro]["extra_subfolders"]
    for subfolder in base_subfolders + extra_subfolders:
        # don't use `cdt`, where we already formatted the subfolder out of the URL
        formatting_bits["subfolder"] = subfolder
        repomd_url = cdt_info[distro]["repomd_url"].format(**formatting_bits)
        repo_primary |= get_repo_dict(
            repomd_url, "primary", massage_primary, cdt, config.src_cache
        )
    for package in packages:
        write_conda_recipes(
            recursive,
            repo_primary,
            package,
            [architecture, "noarch"],
            cdt,
            output_dir,
            config.src_cache,
            build_number,
            conda_forge_style,
            build_number_bump,
        )


@click.command()
@click.argument("packages", nargs=-1, required=True)
@click.option("--output-dir", default=".", type=str)
@click.option("--version", default=None, type=str)
@click.option("--recursive", default=False, is_flag=True)
@click.option("--architecture", default=default_architecture, type=str)
@click.option("--subfolder", default="BaseOS", type=str)
@click.option("--distro", default=default_distro, type=str)
@click.option("--conda-forge-style", default=False, is_flag=True)
@click.option("--build-number", default="2", type=str)
@click.option("--build-number-bump", default=None, type=str)
@click.option("--use-global-cache", default=False, is_flag=True)
def skeletonize(
    packages,
    output_dir,
    version,
    recursive,
    architecture,
    subfolder,
    distro,
    conda_forge_style,
    build_number,
    build_number_bump,
    use_global_cache,
):
    cdt_info = _gen_cdts()

    def _call(cfg):
        write_conda_recipe(
            packages,
            distro,
            output_dir,
            architecture,
            subfolder,
            recursive,
            None,
            cfg,
            build_number,
            conda_forge_style,
            cdt_info,
            build_number_bump,
        )

    if use_global_cache:
        cfg = Config()
        _call(cfg)
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Config(cache_dir=str(tmpdir))
            _call(cfg)


if __name__ == "__main__":
    skeletonize()
