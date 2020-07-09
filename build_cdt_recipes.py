import os
import subprocess
import glob
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


def _build_graph(recipes, dist_arch_slug):
    graph = {}
    for recipe in recipes:
        if dist_arch_slug not in os.path.basename(recipe):
            continue
        node, attrs = _get_recipe_attrs(recipe)
        graph[node] = attrs
    return graph


def _has_all_cdt_deps(node, graph):
    tail = "-".join(node.rsplit("-", maxsplit=2)[1:])
    return all(
        dep in graph
        for dep in graph[node]["all_requirements"]
        if dep.endswith(tail)
    )


def _is_buildable(node, graph, pkgs):
    return all(
        dep in pkgs
        for dep in graph[node]["all_requirements"]
        if dep in graph
    )


def _build_all_cdts(cdt_path, custom_cdt_path, dist_arch_slug):
    with ThreadPoolExecutor(max_workers=16) as exec:
        # legacy CDTs for the old compiler sysroots
        recipes = (
            glob.glob(cdt_path + "/*")
            + glob.glob(custom_cdt_path + "/*")
        )
        graph = _build_graph(recipes, dist_arch_slug)

        skipped = set()
        for node in graph:
            if not _has_all_cdt_deps(node, graph):
                print(
                    "WARNING: skipping CDT %s since not all "
                    "CDT deps are being built!" % node
                )
                graph[node]["skip"] = True
                skipped.add(node)
            else:
                graph[node]["skip"] = False

        built = set()
        with tqdm.tqdm(total=len(graph), ncols=80) as pbar:
            while not all(node in built or node in skipped for node in graph):
                futures = {}

                for node in graph:
                    if (
                        not graph[node]["skip"]
                        and _is_buildable(node, graph, built)
                        and node not in built
                    ):
                        futures[exec.submit(
                            subprocess.run,
                            "conda build --use-local " + graph[node]["recipe_path"],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            shell=True
                        )] = node

                for fut in as_completed(futures):
                    node = futures[fut]
                    c = fut.result()
                    if c.returncode == 0:
                        tqdm.tqdm.write("%s: built" % node)
                        tqdm.tqdm.write(c.stdout)
                        built.add(node)
                        pbar.update(1)
                    else:
                        tqdm.tqdm.write(c.stdout)
                        raise RuntimeError("Could not build CDT %s!" % node)


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
