import re

CDT_PATH = "cdts"
CUSTOM_CDT_PATH = "custom_cdts"

pat = re.compile(r"^(?P<pkg>.*)-(centos7|alma8|alma9)-(?P<arch>.*)$")


def folder_to_package(folder):
    # our folders are distinguished by distro, but packages aren't
    if pat.match(folder):
        return pat.sub(r"\g<pkg>-conda-\g<arch>", folder)
    else:
        raise ValueError(f"unexpected name {folder}")
