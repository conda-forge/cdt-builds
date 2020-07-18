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

import gzip
import hashlib
import io
from os import chmod, makedirs
from os.path import basename, dirname, exists, join, splitext
import re
import tempfile
import subprocess

import click
from conda_build.conda_interface import iteritems
from conda_build.source import download_to_cache
from conda_build.license_family import guess_license_family
from conda_build.config import Config

# try to import C dumper
try:
    from yaml import CSafeDumper as SafeDumper
except ImportError:
    from yaml import SafeDumper
import yaml


try:
    from urllib.request import urlopen
except ImportError:
    from urllib2 import urlopen

from six import string_types
from textwrap import wrap
from xml.etree import cElementTree as ET

# This is used in two places
default_architecture = "x86_64"
default_distro = "centos6"

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
  missing_dso_whitelist:
    - '*'

{depends}

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

mkdir -p "${PREFIX}"/{hostmachine}/sysroot
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
rsync -K -a . "${PREFIX}/{hostmachine}/sysroot"
popd
"""


def _gen_cdts(single_sysroot):
    return dict(
        {
            "centos6": {
                "dirname": "centos6",
                "short_name": "cos6",
                "base_url": "http://mirror.centos.org/centos/6.10/os/{base_architecture}/CentOS/",  # noqa
                "sbase_url": "http://vault.centos.org/6.10/os/Source/SPackages/",
                "repomd_url": "http://mirror.centos.org/centos/6.10/os/{base_architecture}/repodata/repomd.xml",  # noqa
                "host_machine": (
                    "{architecture}-conda-linux-gnu"
                    if single_sysroot else
                    "{architecture}-conda_cos6-linux-gnu"
                ),
                "host_subdir": "linux-{bits}",
                "fname_architecture": "{architecture}",
                "rpm_filename_platform": "el6.{architecture}",
                "checksummer": hashlib.sha256,
                "checksummer_name": "sha256",
                # Some macros are defined in /etc/rpm/macros.* but I cannot find where
                # these ones are defined. Also, rpm --eval "%{gdk_pixbuf_base_version}"
                # gives nothing nor does rpm --showrc | grep gdk
                "macros": {"pyver": "2.6.6", "gdk_pixbuf_base_version": "2.24.1"},
                "allow_missing_sources": True,
                "glibc_ver": "2.12",
            },
            "centos7": {
                "dirname": "centos7",
                "short_name": "cos7",
                "base_url": "http://mirror.centos.org/centos/7/os/{base_architecture}/CentOS/",  # noqa
                "sbase_url": "http://vault.centos.org/7.7.1908/os/Source/SPackages/",
                "repomd_url": "http://mirror.centos.org/centos/7/os/{base_architecture}/repodata/repomd.xml",  # noqa
                "host_machine": (
                    "{architecture}-conda-linux-gnu"
                    if single_sysroot else
                    "{architecture}-conda_cos7-linux-gnu"
                ),
                "host_subdir": "linux-{bits}",
                "fname_architecture": "{architecture}",
                "rpm_filename_platform": "el7.{architecture}",
                "checksummer": hashlib.sha256,
                "checksummer_name": "sha256",
                # Some macros are defined in /etc/rpm/macros.* but I cannot find where
                # these ones are defined. Also, rpm --eval "%{gdk_pixbuf_base_version}"
                # gives nothing nor does rpm --showrc | grep gdk
                "macros": {"pyver": "2.6.6", "gdk_pixbuf_base_version": "2.24.1"},
                "allow_missing_sources": True,
                "glibc_ver": "2.17",
            },
            "centos7-alt": {
                "dirname": "centos7",
                "short_name": "cos7",
                "base_url": "http://mirror.centos.org/altarch/7/os/{base_architecture}/CentOS/",  # noqa
                "sbase_url": "http://vault.centos.org/7.7.1908/os/Source/SPackages/",
                "repomd_url": "http://mirror.centos.org/altarch/7/os/{base_architecture}/repodata/repomd.xml",  # noqa
                "host_machine": (
                    "{gnu_architecture}-conda-linux-gnu"
                    if single_sysroot else
                    "{gnu_architecture}-conda_cos7-linux-gnu"
                ),
                "host_subdir": "linux-{base_architecture}",
                "fname_architecture": "{architecture}",
                "rpm_filename_platform": "el7.{architecture}",
                "checksummer": hashlib.sha256,
                "checksummer_name": "sha256",
                # Some macros are defined in /etc/rpm/macros.* but I cannot find where
                # these ones are defined. Also, rpm --eval "%{gdk_pixbuf_base_version}"
                # gives nothing nor does rpm --showrc | grep gdk
                "macros": {"pyver": "2.6.6", "gdk_pixbuf_base_version": "2.24.1"},
                "allow_missing_sources": True,
                "glibc_ver": "2.17",
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
    cached_path, _ = download_to_cache(src_cache, "", source)
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
        for name, package in iteritems(repo_primary):
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
    xmlstring = urlopen(repomd_url).read()
    # Remove the default namespace definition (xmlns="http://some/namespace")
    xmlstring = re.sub(b'\sxmlns="[^"]+"', b"", xmlstring, count=1)  # noqa
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
            if arch in new_dict[name]:
                print(
                    "WARNING: Duplicate packages exist for {} for arch {}".format(
                        name, arch
                    )
                )
            new_dict[name][arch] = new_package
        else:
            new_dict[name] = dict({arch: new_package})
    return new_dict


def valid_depends(depends):
    name = depends["name"]
    str_flags = depends["flags"]
    if (
        not name.startswith("rpmlib(")
        and not name.startswith("config(")
        and not name.startswith("pkgconfig(")
        and not name.startswith("/")
        and name != "rtld(GNU_HASH)"
        and ".so" not in name
        and "(" not in name
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
    override_arch,
    src_cache,
    build_number,
    conda_forge_style,
    single_sysroot,
):
    entry, entry_name, arch = find_repo_entry_and_arch(
        repo_primary, architectures, dict({"name": package})
    )
    if not entry:
        return
    if override_arch:
        arch = architectures[0]
    else:
        arch = cdt["fname_architecture"]
    package = entry_name
    rpm_url = dirname(dirname(cdt["base_url"])) + "/" + entry["location"]
    srpm_url = cdt["sbase_url"] + entry["source"]
    _, _, _, _, rpm_pth, sha256str = rpm_split_url_and_cache(rpm_url, src_cache)

    license_file = _test_rpm_for_license_file(rpm_pth)

    try:
        # We ignore the hash of source RPMs since they
        # are not given in the source repository data.
        _, _, _, _, _, _ = rpm_split_url_and_cache(srpm_url, src_cache)
    except Exception:
        # Just pretend the binaries are sources.
        if "allow_missing_sources" in cdt:
            srpm_url = rpm_url
        else:
            raise
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
        dep_entry, dep_name, dep_arch = find_repo_entry_and_arch(
            repo_primary, architectures, depend
        )
        if override_arch:
            dep_arch = architectures[0]
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
                override_arch,
                src_cache,
                build_number,
                conda_forge_style,
                single_sysroot,
            )

    sn = cdt["short_name"] + "-" + arch
    dependsstr = ""
    if len(depends) or conda_forge_style:
        depends_specs = [
            "{}-{}-{} {}{}".format(
                depend["name"].lower().replace("+", "x"),
                cdt["short_name"],
                depend["arch"],
                depend["flags"],
                depend["ver"],
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

            if single_sysroot:
                if len(dependsstr_run) == 0:
                    dependsstr_run = "  run:\n"
                dependsstr_run += (
                    "    - "
                    "sysroot_%s %s.*" % (
                        cdt["host_subdir"], cdt["glibc_ver"]))
            else:
                dependsstr_run += (
                    "  run_constrained:\n    - "
                    "sysroot_%s ==99999999999" % cdt["host_subdir"])

            # put sysroot in host
            if single_sysroot:
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
            "build_number": (
                "{{ cdt_build_number|int + 1000 }}"
                if single_sysroot
                else "{{ cdt_build_number }}"
            ),
            "license_file": license_file,
            "packagename": package_cdt_name,
            "hostmachine": cdt["host_machine"],
            "hostsubdir": cdt["host_subdir"],
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
            "PREFIX": "{PREFIX}",
            "RPM": "{RPM}",
            "PWD": "{PWD}",
            "RECIPE_DIR": "{RECIPE_DIR}",
            "SRC_DIR": "{SRC_DIR}",
        }
    )
    odir = join(output_dir, package_cdt_name)
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
    recursive,
    override_arch,
    dependency_add,
    config,
    build_number,
    conda_forge_style,
    single_sysroot,
    cdt_info,
):
    cdt_name = distro
    bits = "32" if architecture in ("armv6", "armv7a", "i686", "i386") else "64"
    base_architectures = dict({"i686": "i386"})
    # gnu_architectures are those recognized by the canonical config.sub / config.guess
    # and crosstool-ng. They are returned from ${CC} -dumpmachine and are a part of the
    # sysroot.
    gnu_architectures = dict({"ppc64le": "powerpc64le"})
    try:
        base_architecture = base_architectures[architecture]
    except Exception:
        base_architecture = architecture
    try:
        gnu_architecture = gnu_architectures[architecture]
    except Exception:
        gnu_architecture = architecture
    architecture_bits = dict(
        {
            "architecture": architecture,
            "base_architecture": base_architecture,
            "gnu_architecture": gnu_architecture,
            "bits": bits,
        }
    )
    cdt = dict()
    for k, v in iteritems(cdt_info[cdt_name]):
        if isinstance(v, string_types):
            cdt[k] = v.format(**architecture_bits)
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

    repomd_url = cdt["repomd_url"]
    repo_primary = get_repo_dict(
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
            override_arch,
            config.src_cache,
            build_number,
            conda_forge_style,
            single_sysroot,
        )


@click.command()
@click.argument("packages", nargs=-1, required=True)
@click.option("--output-dir", default=".", type=str)
@click.option("--version", default=None, type=str)
@click.option("--recursive", default=False, is_flag=True)
@click.option("--architecture", default=default_architecture, type=str)
@click.option("--no-override-arch", default=False, is_flag=True)
@click.option("--distro", default=default_distro, type=str)
@click.option("--conda-forge-style", default=False, is_flag=True)
@click.option("--single-sysroot", default=False, is_flag=True)
@click.option("--build-number", default="2", type=str)
def skeletonize(
    packages,
    output_dir,
    version,
    recursive,
    architecture,
    no_override_arch,
    distro,
    conda_forge_style,
    single_sysroot,
    build_number,
):
    cdt_info = _gen_cdts(single_sysroot)
    if architecture in ["aarch64", "ppc64le"]:
        cdt_info["centos7"] = cdt_info["centos7-alt"]

    with tempfile.TemporaryDirectory() as tmpdir:
        write_conda_recipe(
            packages,
            distro,
            output_dir,
            architecture,
            recursive,
            not no_override_arch,
            None,
            Config(cache_dir=str(tmpdir)),
            build_number,
            conda_forge_style,
            single_sysroot,
            cdt_info,
        )


if __name__ == "__main__":
    skeletonize()
