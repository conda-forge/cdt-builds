import os
import subprocess
import glob
import tempfile
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import tqdm
import click
from ruamel.yaml import YAML

from cdt_config import (
    LEGACY_CDT_PATH,
    LEGACY_CUSTOM_CDT_PATH,
    CDT_PATH,
    CUSTOM_CDT_PATH,
)

LINE_SEP = """\
===============================================================================
===============================================================================
===============================================================================\
"""

yaml = YAML()


def _split_req(req):
    return req.split(" ")[0]


def _get_recipe_attrs(recipe):
    node = os.path.basename(recipe)
    attrs = {}
    attrs["recipe_path"] = recipe

    with open(os.path.join(recipe, "meta.yaml"), "r") as fp:
        attrs["meta"] = yaml.load(fp)

    attrs["all_requirements"] = sorted(set((
        [
            _split_req(req)
            for req in attrs["meta"].get("requirements", {}).get("build", [])
        ]
        + [
            _split_req(req)
            for req in attrs["meta"].get("requirements", {}).get("host", [])
        ]
        + [
            _split_req(req)
            for req in attrs["meta"].get("requirements", {}).get("run", [])
        ]

    )))
    return node, attrs


def _build_cdt_meta(recipes, dist_arch_slug):
    cdt_meta = {}
    for recipe in recipes:
        if dist_arch_slug not in os.path.basename(recipe):
            continue
        node, attrs = _get_recipe_attrs(recipe)
        cdt_meta[node] = attrs
    return cdt_meta


def _has_all_cdt_deps(node, cdt_meta):
    tail = "-".join(node.rsplit("-", maxsplit=2)[1:])
    return all(
        dep in cdt_meta
        for dep in cdt_meta[node]["all_requirements"]
        if dep.endswith(tail)
    )


def _is_buildable(node, cdt_meta, pkgs):
    return all(
        dep in pkgs
        for dep in cdt_meta[node]["all_requirements"]
        if dep in cdt_meta
    )


def _cdt_exists(cdt_meta_node):
    return False


def _build_cdt(cdt_meta_node):
    if not _cdt_exists(cdt_meta_node):
        with tempfile.TemporaryDirectory() as tmpdir:
            c = subprocess.run(
                (
                    "conda build --use-local -m conda_build_config.yaml "
                    + "--cache-dir " + str(tmpdir) + " "
                    + cdt_meta_node["recipe_path"]
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                shell=True
            )
    return c


def _build_all_cdts(cdt_path, custom_cdt_path, dist_arch_slug):
    build_logs = ""
    with ThreadPoolExecutor(max_workers=16) as exec:
        # legacy CDTs for the old compiler sysroots
        recipes = (
            glob.glob(cdt_path + "/*")
            + glob.glob(custom_cdt_path + "/*")
        )
        cdt_meta = _build_cdt_meta(recipes, dist_arch_slug)
        print("cdts to build:", flush=True)
        for cdt in sorted(cdt_meta):
            print("    %s" % cdt, flush=True)

        skipped = set()
        for node in cdt_meta:
            if not _has_all_cdt_deps(node, cdt_meta):
                print(
                    "WARNING: skipping CDT %s since not all "
                    "CDT deps are being built!" % node
                )
                cdt_meta[node]["skip"] = True
                skipped.add(node)
            else:
                cdt_meta[node]["skip"] = False

        built = set()
        with tqdm.tqdm(total=len(cdt_meta), ncols=80) as pbar:
            while not all(node in built or node in skipped for node in cdt_meta):
                futures = {}

                for node in cdt_meta:
                    if (
                        not cdt_meta[node]["skip"]
                        and _is_buildable(node, cdt_meta, built)
                        and node not in built
                    ):
                        futures[exec.submit(
                            _build_cdt,
                            cdt_meta[node],
                        )] = node

                for fut in as_completed(futures):
                    node = futures[fut]
                    c = fut.result()
                    build_logs += (
                        "\n\n"
                        + LINE_SEP
                        + "\n"
                        + c.stdout
                    )
                    if c.returncode == 0:
                        tqdm.write("built %s" % node)
                        sys.stderr.flush()
                        built.add(node)
                        pbar.update(1)
                        pbar.refresh()
                    else:
                        tqdm.tqdm.write(build_logs)
                        raise RuntimeError("Could not build CDT %s!" % node)

            print(build_logs, flush=True)


@click.command()
@click.argument("dist_arch_slug", required=True)
@click.option(
    "--legacy", default=False, is_flag=True,
    help="Build old-style, legacy CDTs in the legacy_* folders."
)
def _main(
    dist_arch_slug,
    legacy
):
    """
    Build all CDT recipes for a given DIST_ARCH_SLUG (e.g. cos6-x86_64,
    cos7-aarch64, etc.)
    """

    if legacy:
        _build_all_cdts(LEGACY_CDT_PATH, LEGACY_CUSTOM_CDT_PATH, dist_arch_slug)
    else:
        _build_all_cdts(CDT_PATH, CUSTOM_CDT_PATH, dist_arch_slug)


if __name__ == "__main__":
    _main()
