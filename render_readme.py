import os
import glob

from jinja2 import Template

from cdt_config import (
    CDT_PATH,
    CUSTOM_CDT_PATH,
)


def folder_to_package(folder):
    # our folders are distinguished by distro, but packages aren't (after alma8)
    if "centos7" in folder:
        idx = folder.index("centos7")
        pkg = folder[:idx] + "cos7" + folder[idx + 7:]
    elif "alma" in folder:
        idx = folder.index("alma")  # followed by 8 or 9
        pkg = folder[:idx] + "conda" + folder[idx + 5:]
    else:
        raise ValueError(f"unexpected name {folder}")
    return pkg


def render_readme():
    recipes = set(
        glob.glob(CDT_PATH + "/*")
        + glob.glob(CUSTOM_CDT_PATH + "/*")
    )
    recipes = set(
        os.path.basename(r) for r in recipes if not r.endswith("README.md")
    )
    pkgs = sorted(list({folder_to_package(x) for x in recipes}))

    with open("README.md.tmpl", "r") as fp:
        tmpl = Template(fp.read())

    with open("README.md", "w") as fp:
        fp.write(tmpl.render(cdts=pkgs))


if __name__ == '__main__':
    render_readme()
