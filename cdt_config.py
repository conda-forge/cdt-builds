CDT_PATH = "cdts"
CUSTOM_CDT_PATH = "custom_cdts"


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
