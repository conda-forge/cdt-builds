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

from conda.core.index import get_index

from cdt_config import (
    CDT_PATH,
    CUSTOM_CDT_PATH,
)

LINE_SEP = """\
===============================================================================
===============================================================================
===============================================================================\
"""


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

    yaml = YAML(typ="jinja2")
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
    attrs['exists'] = attrs['skip']

    return node, attrs


def _build_cdt_meta(recipes, dist_arch_slug):
    print("getting conda-forge/label/main channel index...", flush=True)
    channel_url = '/'.join(['conda-forge', 'label', 'main'])
    channel_index = {
        prec.fn: prec
        for prec in get_index(
            [channel_url],
            prepend=False,
            use_cache=False,
        )
        if prec.subdir == 'noarch'
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
        dep in pkgs or cdt_meta[dep]['exists']
        for dep in cdt_meta[node]["all_requirements"]
        if dep in cdt_meta
    )


def _build_cdt(cdt_meta_node, no_temp=False):
    for _ in range(5):
        if no_temp:
            c = subprocess.run(
                (
                    "conda build --use-local -m conda_build_config.yaml "
                    + cdt_meta_node["recipe_path"]
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                shell=True
            )
        else:
            with tempfile.TemporaryDirectory() as tmpdir:
                with tempfile.TemporaryDirectory() as pkg_tmpdir:
                    c = subprocess.run(
                        (
                            "CONDA_PKGS_DIRS=" + str(pkg_tmpdir) + " "
                            "conda build --use-local -m conda_build_config.yaml "
                            + "--cache-dir " + str(tmpdir) + " "
                            + cdt_meta_node["recipe_path"]
                        ),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        shell=True
                    )

        if c.returncode == 0:
            break

    if (
        os.environ.get("IS_FORK", "True") == "False"
        and "ANACONDA_TOKEN" in os.environ
        and os.environ.get("BUILD_SOURCEBRANCHNAME", None) == "main"
        and c.returncode == 0
    ):
        cdt_slug = os.path.basename(cdt_meta_node["recipe_path"])
        cdt_file = glob.glob(
            os.path.expandvars("${HOME}/miniforge3/conda-bld/*/%s-*.tar.bz2" % cdt_slug)
        )
        assert len(cdt_file) == 1
        for _ in range(5):
            c_up = subprocess.run(
                "anaconda --token ${ANACONDA_TOKEN} upload "
                "--skip-existing %s" % cdt_file[0],
                shell=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            if c_up.returncode == 0:
                break
    else:
        c_up = None

    return c, c_up


def _build_all_cdts(cdt_path, custom_cdt_path, dist_arch_slug):
    recipes = (
        glob.glob(cdt_path + "/*")
        + glob.glob(custom_cdt_path + "/*")
    )
    cdt_meta = _build_cdt_meta(recipes, dist_arch_slug)
    print("\ncdts to build:", flush=True)
    for cdt in sorted(cdt_meta):
        if not cdt_meta[cdt]['skip']:
            print("    %s" % cdt, flush=True)

    num_workers = 4

    build_logs = ""
    with ThreadPoolExecutor(max_workers=num_workers) as exec:

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
                raise RuntimeError(
                    "CDT %s cannot be built "
                    "since not all deps are available!" % node
                )

        built = set()
        with tqdm.tqdm(
            total=len(cdt_meta) - len(skipped), ncols=80, desc='building recipes'
        ) as pbar:
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
                            no_temp=True if num_workers == 1 else False,
                        )] = node

                for fut in as_completed(futures):
                    node = futures[fut]
                    c, c_up = fut.result()
                    build_logs += (
                        "\n\n"
                        + LINE_SEP
                        + "\n"
                        + c.stdout
                    )
                    if c_up is not None:
                        build_logs += (
                            "\n\n"
                            + LINE_SEP
                            + "\n"
                            + c_up.stdout
                        )

                    if (
                        c.returncode == 0
                        and (
                            c_up is None
                            or c_up.returncode == 0
                        )
                    ):
                        if c_up is None:
                            pbar.write("built %s" % node, file=sys.stderr)
                        else:
                            pbar.write("built and uploaded %s" % node, file=sys.stderr)
                        sys.stderr.flush()
                        built.add(node)
                        pbar.update(1)
                        pbar.refresh()
                    else:
                        pbar.write(build_logs)
                        sys.stderr.flush()
                        raise RuntimeError("Could not build CDT %s!" % node)

    log_dir = "build_logs"
    log_pth = os.path.join(log_dir, "build_logs_%s.txt" % dist_arch_slug)
    os.makedirs(log_dir, exist_ok=True)
    with open(log_pth, "w") as fp:
        fp.write(build_logs)
    sys.stdout.write(build_logs)


@click.command()
@click.argument("dist_arch_slug", required=True)
def _main(
    dist_arch_slug,
):
    """
    Build all CDT recipes for a given DIST_ARCH_SLUG (e.g. conda-x86_64
    for post-CentOS CDTs, or cos6-x86_64, cos7-aarch64, etc. historically)
    """

    _build_all_cdts(CDT_PATH, CUSTOM_CDT_PATH, dist_arch_slug)


if __name__ == "__main__":
    _main()
