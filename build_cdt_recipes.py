import hashlib
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

from conda.core.index import Index

from cdt_config import (
    CDT_PATH,
    CUSTOM_CDT_PATH,
    folder_to_package,
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
        on_channel &= fname in channel_index
    return on_channel


def _get_node_attrs(recipe, channel_index):
    node = folder_to_package(os.path.basename(recipe))
    attrs = {}
    attrs["recipe_path"] = recipe

    yaml = YAML(typ="jinja2")
    with open(os.path.join(recipe, "meta.yaml"), "r") as fp:
        attrs["meta"] = yaml.load(fp)

    attrs["all_requirements"] = sorted(
        set(
            (
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
            )
        )
    )

    attrs["skip"] = _cdt_exists(attrs, channel_index)
    attrs["exists"] = attrs["skip"]

    return node, attrs


def _build_cdt_meta(recipes, dist_arch_slug):
    print("getting conda-forge/label/main channel index...", flush=True)
    channel_url = "/".join(["conda-forge", "label", "main"])
    channel_index = {
        prec.fn: prec
        for prec in Index(
            [channel_url],
            prepend=False,
            use_cache=False,
        )
        if prec.subdir == "noarch"
    }

    cdt_meta = {}
    for recipe in tqdm.tqdm(recipes, desc="building CDT metadata", ncols=80):
        if dist_arch_slug not in os.path.basename(recipe):
            continue
        node, attrs = _get_node_attrs(recipe, channel_index)
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
        dep in pkgs or cdt_meta[dep]["exists"]
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
                    # These are exported in the azure pipelines workflow
                    + ' --extra-meta flow_run_id="${flow_run_id:-}"'
                    + ' remote_url="${remote_url:-}" sha="${sha:-}"'
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                shell=True,
            )
        else:
            with tempfile.TemporaryDirectory() as tmpdir:
                with tempfile.TemporaryDirectory() as pkg_tmpdir:
                    c = subprocess.run(
                        (
                            "CONDA_PKGS_DIRS=" + str(pkg_tmpdir) + " "
                            "conda build --use-local -m conda_build_config.yaml "
                            + "--cache-dir "
                            + str(tmpdir)
                            + " "
                            + cdt_meta_node["recipe_path"]
                            # These are exported in the azure pipelines workflow
                            + ' --extra-meta flow_run_id="${flow_run_id:-}"'
                            + ' remote_url="${remote_url:-}" sha="${sha:-}"'
                        ),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        shell=True,
                    )

        if c.returncode == 0:
            break

    if (
        os.environ.get("IS_FORK", "True") == "False"
        and "ANACONDA_TOKEN" in os.environ
        and os.environ.get("BUILD_SOURCEBRANCHNAME", None) == "main"
        and c.returncode == 0
    ):
        recipe = os.path.basename(cdt_meta_node["recipe_path"])
        pkg = folder_to_package(recipe)
        cdt_file = glob.glob(
            os.path.join(os.environ["HOME"], f"miniforge3/conda-bld/*/{pkg}-*.conda")
        )
        assert len(cdt_file) == 1
        for _ in range(5):
            c_up = subprocess.run(
                "anaconda --token ${ANACONDA_TOKEN} upload "
                f"--skip-existing {cdt_file[0]}",
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


def _build_cdt_groups(cdt_meta):
    all_cdts = set(cdt_meta.keys())
    groups = {}
    cdt_to_group = {}
    for name, info in cdt_meta.items():
        cdt_reqs = set(info["all_requirements"]) & all_cdts
        cdt_reqs.add(name)
        curr_groups = set()
        for group_name, group_members in groups.items():
            if cdt_reqs & group_members:
                curr_groups.add(group_name)

        if not curr_groups:
            groups[name] = cdt_reqs
            for cdt in cdt_reqs:
                cdt_to_group[cdt] = name
        else:
            new_group = set()
            for group_name in curr_groups:
                new_group |= groups.pop(group_name)
            new_group |= cdt_reqs
            groups[name] = new_group
            for cdt in new_group:
                cdt_to_group[cdt] = name

    # this bit of code double checks that all reqs are in the
    # same group
    for name, info in cdt_meta.items():
        cdt_reqs = set(info["all_requirements"]) & all_cdts
        cdt_reqs.add(name)
        curr_groups = set(cdt_to_group[nm] for nm in cdt_reqs)
        assert len(curr_groups) == 1
    assert len(set(cdt_to_group.values())) <= len(cdt_to_group)

    return cdt_to_group


def _cdt_name_to_part(name, num_parts):
    sha = hashlib.sha1(name.encode("utf-8"))
    part_zero = int(sha.hexdigest(), 16) % num_parts
    return part_zero + 1


def _build_all_cdts(cdt_path, custom_cdt_path, dist_arch_slug, part=1, num_parts=1):
    recipes = glob.glob(cdt_path + "/*") + glob.glob(custom_cdt_path + "/*")
    cdt_meta = _build_cdt_meta(recipes, dist_arch_slug)
    cdt_to_group = _build_cdt_groups(cdt_meta)

    # skip CDTs that we are not building in this job
    for node in cdt_meta:
        group_name = cdt_to_group[node]
        if _cdt_name_to_part(group_name, num_parts) != part:
            cdt_meta[node]["skip"] = True

    skipped = set(k for k, v in cdt_meta.items() if v["skip"])
    for node in sorted(skipped):
        print(
            f"WARNING: skipping CDT {node} since it has "
            "already been built or is not part of this job!",
            flush=True,
        )

    print("\ncdts to build:", flush=True)
    for cdt in sorted(set(cdt_meta.keys()) - skipped):
        print(f"    {cdt}", flush=True)

    num_workers = 2
    build_logs = ""

    with ThreadPoolExecutor(max_workers=num_workers) as exec:
        for node in cdt_meta:
            if not _has_all_cdt_deps(node, cdt_meta):
                raise RuntimeError(
                    f"CDT {node} cannot be built since not all deps are available!"
                )

        built = set()
        with tqdm.tqdm(
            total=len(cdt_meta) - len(skipped), ncols=80, desc="building recipes"
        ) as pbar:
            while not all(node in built or node in skipped for node in cdt_meta):
                futures = {}

                for node in cdt_meta:
                    if (
                        not cdt_meta[node]["skip"]
                        and _is_buildable(node, cdt_meta, built)
                        and node not in built
                    ):
                        futures[
                            exec.submit(
                                _build_cdt,
                                cdt_meta[node],
                                no_temp=True if num_workers == 1 else False,
                            )
                        ] = node

                for fut in as_completed(futures):
                    node = futures[fut]
                    c, c_up = fut.result()
                    build_logs += "\n\n" + LINE_SEP + "\n" + c.stdout
                    if c_up is not None:
                        build_logs += "\n\n" + LINE_SEP + "\n" + c_up.stdout

                    if c.returncode == 0 and (c_up is None or c_up.returncode == 0):
                        if c_up is None:
                            pbar.write(f"built {node}", file=sys.stderr)
                        else:
                            pbar.write(f"built and uploaded {node}", file=sys.stderr)
                        sys.stderr.flush()
                        built.add(node)
                        pbar.update(1)
                        pbar.refresh()
                    else:
                        pbar.write(build_logs)
                        sys.stderr.flush()
                        raise RuntimeError(f"Could not build CDT {node}!")

    log_dir = "build_logs"
    log_pth = os.path.join(log_dir, f"build_logs_{dist_arch_slug}.txt")
    os.makedirs(log_dir, exist_ok=True)
    with open(log_pth, "w") as fp:
        fp.write(build_logs)
    sys.stdout.write(build_logs)


@click.command()
@click.argument("dist_arch_slug", required=True)
@click.option(
    "--part-to-process",
    default="1:1",
    type=str,
    help=(
        "the part of the list of CDTs to process, denoted as "
        "'<part starting at 1>:<total_parts>' (e.g. '1:4', '2:4'"
        ", etc. for four parts)"
    ),
)
def _main(
    dist_arch_slug,
    part_to_process,
):
    """
    Build all CDT recipes for a given DIST_ARCH_SLUG, i.e. `f"{distro}-{arch}"`,
    where we use the full distro name, and not the shortform
    """
    part, num_parts = part_to_process.split(":")
    part = int(part)
    num_parts = int(num_parts)
    _build_all_cdts(
        CDT_PATH, CUSTOM_CDT_PATH, dist_arch_slug, part=part, num_parts=num_parts
    )


if __name__ == "__main__":
    _main()
