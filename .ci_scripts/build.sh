#!/bin/bash
set -e

source $HOME/miniforge3/etc/profile.d/conda.sh
conda activate base

cat .ci_scripts/condarc > $HOME/.condarc

python build_cdt_recipes.py $1
