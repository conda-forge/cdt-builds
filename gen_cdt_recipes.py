import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
import shutil
import collections

import tqdm
import click
from ruamel.yaml import YAML

from cdt_config import (
    LEGACY_CDT_PATH,
    LEGACY_CUSTOM_CDT_PATH,
    CDT_PATH,
    CUSTOM_CDT_PATH,
)


def _gen_dist_arch_str(arch, dist):
    return "%s-%s" % (dist.replace("ent", ""), arch)


def _make_cdt_recipes(*, extra, cdt_path, arch_dist_tuples, cdts, exec, only_new):
    futures = {}
    for arch, dist in arch_dist_tuples:
        for cdt, cfg in cdts.items():
            if cfg["custom"]:
                continue

            if (
                "skipped_cdts" in cfg
                and _gen_dist_arch_str(arch, dist) in cfg["skipped_cdts"]
            ):
                continue

            if cfg.get("recursive", True):
                _extra = extra + " --recursive"
            else:
                _extra = extra

            _pth = os.path.join(
                cdt_path,
                cdt + "-" + _gen_dist_arch_str(arch, dist),
            )

            if only_new and os.path.exists(_pth):
                continue

            futures[exec.submit(
                subprocess.run,
                (
                    f"python rpm.py {cdt} --output-dir={cdt_path} "
                    + f"--architecture={arch} --distro={dist} "
                    + _extra
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                shell=True
            )] = {"cdt": cdt, "arch": arch, "dist": dist}
    return futures


def _cleanup_custom_cdt_overlaps(*, cdt_path, arch_dist_tuples, cdts):
    for arch, dist in arch_dist_tuples:
        for cdt, cfg in cdts.items():
            if not cfg["custom"]:
                continue

            pth = os.path.join(
                cdt_path,
                cdt + "-" + dist.replace("ent", "") + "-" + arch,
            )
            if os.path.exists(pth):
                try:
                    subprocess.run(
                        "git rm -r -f --ignore-unmatch " + pth,
                        shell=True,
                        capture_output=True,
                        check=True,
                    )
                    subprocess.run(
                        "rm -rf " + pth,
                        shell=True,
                        capture_output=True,
                        check=True,
                    )
                except subprocess.CalledProcessError as e:
                    print(
                        "WARNING: error removing autogenerated "
                        "recipe for custom CDT %s: %s" % (cdt, repr(e))
                    )


def _clear_gen_cdts(pth):
    try:
        subprocess.run(
            "git rm -r -f --ignore-unmatch " + pth + "/*-*-*",
            shell=True,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            "rm -rf " + pth + "/*-*-*",
            shell=True,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print("WARNING: error removing autogenerated CDTs at %s: %s" % (pth, repr(e)))


def _fix_cdt_licenses(*, cdts, arch_dist_tuples, cdt_path):
    for arch, dist in arch_dist_tuples:
        for cdt, cfg in cdts.items():
            pth = os.path.join(
                cdt_path,
                cdt + "-" + dist.replace("ent", "") + "-" + arch,
            )
            if 'license_file' in cfg and os.path.exists(pth):
                if cfg["license_file"] is None:
                    pass
                elif isinstance(cfg["license_file"], collections.abc.MutableSequence):
                    for lf in cfg['license_file']:
                        shutil.copy2(lf, os.path.join(pth, "."))
                else:
                    shutil.copy2(cfg['license_file'], os.path.join(pth, "."))

                yaml = YAML(typ="jinja2")
                yaml.indent(mapping=2, sequence=4, offset=2)
                yaml.width = 320
                with open(os.path.join(pth, "meta.yaml"), "r") as fp:
                    meta = yaml.load(fp)

                if cfg["license_file"] is None:
                    meta["about"].pop("license_file")
                elif isinstance(cfg["license_file"], collections.abc.MutableSequence):
                    meta["about"]["license_file"] = [
                        os.path.basename(lf)
                        for lf in cfg["license_file"]
                    ]
                else:
                    meta["about"]["license_file"] = os.path.basename(
                        cfg["license_file"]
                    )

                with open(os.path.join(pth, "meta.yaml"), "w") as fp:
                    meta = yaml.dump(meta, fp)


@click.command()
@click.option(
    "--only-new", default=False, is_flag=True,
    help="Only generate new CDT recipes."
)
@click.option(
    "--no-legacy", default=False, is_flag=True,
    help="Do not denerate the old-style, legacy CDTs in the legacy_* folders."
)
def _main(only_new, no_legacy):
    """
    Generate all CDT recipes.
    """
    yaml = YAML()

    with open("cdt_slugs.yaml", "r") as fp:
        cdts = yaml.load(fp)

    if not no_legacy:
        os.makedirs(LEGACY_CDT_PATH, exist_ok=True)
        os.makedirs(LEGACY_CUSTOM_CDT_PATH, exist_ok=True)
    os.makedirs(CDT_PATH, exist_ok=True)
    os.makedirs(CUSTOM_CDT_PATH, exist_ok=True)

    print("generating CDT recipes...")
    futures = {}
    with ThreadPoolExecutor(max_workers=20) as exec:
        if not no_legacy:
            # legacy CDTs for the old compiler sysroots

            if not only_new:
                _clear_gen_cdts(LEGACY_CDT_PATH)

            extra = "--conda-forge-style"
            arch_dist_tuples = [
                ("x86_64", "centos6"),
                ("aarch64", "centos7"),
                ("ppc64le", "centos7")
            ]

            futures.update(
                _make_cdt_recipes(
                    extra=extra,
                    cdt_path=LEGACY_CDT_PATH,
                    arch_dist_tuples=arch_dist_tuples,
                    cdts=cdts,
                    exec=exec,
                    only_new=only_new)
                )

        # new CDTs for the new compilers with a single sysroot
        if not only_new:
            _clear_gen_cdts(CDT_PATH)

        extra = "--conda-forge-style --single-sysroot"
        arch_dist_tuples = [
            ("x86_64", "centos6"), ("x86_64", "centos7"),
            ("aarch64", "centos7"), ("ppc64le", "centos7")
        ]
        futures.update(
            _make_cdt_recipes(
                extra=extra,
                cdt_path=CDT_PATH,
                arch_dist_tuples=arch_dist_tuples,
                cdts=cdts,
                exec=exec,
                only_new=only_new)
            )

        for fut in tqdm.tqdm(as_completed(futures), total=len(futures)):
            c = fut.result()
            pkg = futures[fut]
            nm = "-".join([pkg["cdt"], pkg["dist"].replace("ent", ""), pkg["arch"]])
            if (
                c.returncode != 0
                or "WARNING: Did not find package called (or another one providing)" in c.stdout  # noqa
            ):
                tqdm.tqdm.write("WARNING: making CDT recipe %s failed!" % nm)
                tqdm.tqdm.write(c.stdout)

            if (
                "WARNING: could not find a suitable license " in c.stdout
            ):
                for line in c.stdout.splitlines():
                    if "WARNING: could not find a suitable license " in line:
                        _found_cdt = None
                        for _cdt in cdts:
                            if _cdt.lower() in line.lower():
                                if _found_cdt is None:
                                    _found_cdt = _cdt
                                elif len(_cdt) > len(_found_cdt):
                                    _found_cdt = _cdt

                        if _found_cdt is not None:
                            if "license_file" not in cdts[_found_cdt]:
                                tqdm.tqdm.write(line.strip())
                        else:
                            tqdm.tqdm.write(line.strip())

    # finally, we have to clean up any CDTs marked as custom that happened to be
    # made by the templates again
    if not no_legacy:
        # legacy CDTs for the old compiler sysroots
        arch_dist_tuples = [
            ("x86_64", "centos6"),
            ("aarch64", "centos7"),
            ("ppc64le", "centos7")
        ]
        _cleanup_custom_cdt_overlaps(
            cdt_path=LEGACY_CDT_PATH,
            arch_dist_tuples=arch_dist_tuples,
            cdts=cdts)

        _fix_cdt_licenses(
            cdts=cdts,
            arch_dist_tuples=arch_dist_tuples,
            cdt_path=LEGACY_CDT_PATH
        )

    # new CDTs for the new compilers with a single sysroot
    arch_dist_tuples = [
        ("x86_64", "centos6"), ("x86_64", "centos7"),
        ("aarch64", "centos7"), ("ppc64le", "centos7")
    ]
    _cleanup_custom_cdt_overlaps(
        cdt_path=CDT_PATH,
        arch_dist_tuples=arch_dist_tuples,
        cdts=cdts)

    _fix_cdt_licenses(
        cdts=cdts,
        arch_dist_tuples=arch_dist_tuples,
        cdt_path=CDT_PATH
    )


if __name__ == "__main__":
    _main()
