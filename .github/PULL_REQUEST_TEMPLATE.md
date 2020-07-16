Please see the repo readme for directions on how to make PRs on this repo.

Checklist:

- [ ] if you have added a CDT, it appears in the `cdt_slugs.yaml` file
- [ ] if you have changed the CDT generator code (`rpm.py`), you have bumped
  the build number in `conda_build_config.yaml` and have remade all of the
  recipes via running `python gen_cdt_recipes.py`
- [ ] if you have added a custom CDT recipe, you have added the name of the CDT
  with `custom: true` in the `cdt_slugs.yaml` file.
- [ ] all CDT recipes have build number set by `{{ cdt_build_number }}` for
  old-style/legacy CDTs or `{{ cdt_build_number|int + 1000 }}` for new-style CDTs
