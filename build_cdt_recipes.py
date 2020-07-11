import os
import subprocess
import glob
import tempfile
import sys
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stdout, redirect_stderr
from wurlitzer import pipes

import tqdm
import click
from ruamel.yaml import YAML

from conda_build.conda_interface import get_index

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


def _cdt_exists(cdt_meta_node, channel_index):
    import conda_build.api

    try:
        f = io.StringIO()
        with redirect_stdout(f), redirect_stderr(f), pipes(stdout=f, stderr=f):
            metas = conda_build.api.render(
                cdt_meta_node["recipe_path"],
                variant_config_files=["conda_build_config.yaml"],
                bypass_env_check=True,
            )
    except Exception as e:
        print(f.getvalue())
        raise e

    dist_fnames = [
        path
        for m, _, _ in metas
        for path in conda_build.api.get_output_file_paths(m)
        if not m.skip()
    ]

    on_channel = True
    for dist_fname in dist_fnames:
        fname = os.path.basename(dist_fname)
        on_channel &= (fname in channel_index)
    return on_channel


def _get_recipe_attrs(recipe, channel_index):
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

    attrs['skip'] = _cdt_exists(attrs, channel_index)

    return node, attrs


def _build_cdt_meta(recipes, dist_arch_slug):
    print("getting conda-forge/label/main channel index...", flush=True)
    channel_url = '/'.join(['conda-forge', 'label', 'main'])
    dist_index = get_index(
        [channel_url],
        prepend=False,
        use_cache=False
    )
    channel_index = {
        c.to_filename(): a
        for c, a in dist_index.items()
        if a['subdir'] == 'noarch'
    }

    cdt_meta = {}
    for recipe in tqdm.tqdm(recipes, desc='building CDT metadata', ncols=80):
        if dist_arch_slug not in os.path.basename(recipe):
            continue
        node, attrs = _get_recipe_attrs(recipe, channel_index)
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


def _build_cdt(cdt_meta_node):
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
    recipes = (
        glob.glob(cdt_path + "/*")
        + glob.glob(custom_cdt_path + "/*")
    )
    cdt_meta = _build_cdt_meta(recipes, dist_arch_slug)
    print("cdts to build:", flush=True)
    for cdt in sorted(cdt_meta):
        print(
            "    %s: %s" % (
                cdt,
                'skipped'
                if cdt_meta[cdt]['skip']
                else 'building'
            ),
            flush=True
        )

    build_logs = ""
    with ThreadPoolExecutor(max_workers=16) as exec:

        skipped = set()
        for node in cdt_meta:
            if cdt_meta[node]['skip']:
                print(
                    "WARNING: skipping CDT %s since it has "
                    "already been built!" % node,
                    flush=True,
                )
                skipped.add(node)
            elif not _has_all_cdt_deps(node, cdt_meta):
                print(
                    "WARNING: skipping CDT %s since not all "
                    "CDT deps are being built!" % node,
                    flush=True,
                )
                cdt_meta[node]["skip"] = True
                skipped.add(node)

        built = set()
        with tqdm.tqdm(total=len(cdt_meta), ncols=80, desc='building recipes') as pbar:
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
                        pbar.write("built %s" % node, file=sys.stderr)
                        sys.stderr.flush()
                        built.add(node)
                        pbar.update(1)
                        pbar.refresh()
                    else:
                        pbar.write(build_logs)
                        sys.stderr.flush()
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
