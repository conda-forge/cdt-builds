Please see the repo readme for directions on how to make PRs on this repo.

Checklist:

- [ ] if you have added a CDT, it appears in the `cdt_slugs.yaml` file
- [ ] if you have changed the CDT generator code (`rpm.py`), you have bumped
  the build number in `cdt_config.py` and have remade all of the recipes via running
    `python gen_cdt_recipes.py`
- [ ] if you have added a custom CDT recipe, you have added the name of the CDT
  with `custom: true` in the `cdt_slugs.yaml` file.
