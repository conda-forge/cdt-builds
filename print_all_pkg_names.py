import os
import glob
import io
from contextlib import redirect_stdout, redirect_stderr

import tqdm
from wurlitzer import pipes

from cdt_config import (
    CDT_PATH,
    CUSTOM_CDT_PATH,
)


def _get_pkg_name(recipe):
    import conda_build.api

    try:
        f = io.StringIO()
        with redirect_stdout(f), redirect_stderr(f), pipes(stdout=f, stderr=f):
            metas = conda_build.api.render(
                recipe,
                variant_config_files=["conda_build_config.yaml"],
                bypass_env_check=True,
            )
    except Exception as e:
        print(f.getvalue())
        raise e

    dist_fnames = [
        "noarch/" + os.path.basename(path)
        for m, _, _ in metas
        for path in conda_build.api.get_output_file_paths(m)
        if not m.skip()
    ]
    return dist_fnames


def print_names():
    recipes = set(
        glob.glob(CDT_PATH + "/*")
        + glob.glob(CUSTOM_CDT_PATH + "/*")
    )
    recipes = sorted(
        r for r in recipes if not r.endswith("README.md")
    )

    names = []
    for recipe in tqdm.tqdm(recipes, desc='rendering recipes'):
        names += _get_pkg_name(recipe)
    for name in sorted(names):
        print(name)


if __name__ == '__main__':
    print_names()
