import os
import glob

from jinja2 import Template

from cdt_config import (
    LEGACY_CDT_PATH,
    LEGACY_CUSTOM_CDT_PATH,
    CDT_PATH,
    CUSTOM_CDT_PATH,
)


def render_readme():
    recipes = set(
        glob.glob(CDT_PATH + "/*")
        + glob.glob(CUSTOM_CDT_PATH + "/*")
        + glob.glob(LEGACY_CDT_PATH + "/*")
        + glob.glob(LEGACY_CUSTOM_CDT_PATH + "/*")
    )
    recipes = sorted(set(
        os.path.basename(r) for r in recipes if not r.endswith("README.md")
    ))

    with open("README.md.tmpl", "r") as fp:
        tmpl = Template(fp.read())

    with open("README.md", "w") as fp:
        fp.write(tmpl.render(cdts=recipes))


if __name__ == '__main__':
    render_readme()
