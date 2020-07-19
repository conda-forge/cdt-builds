#!/bin/bash
set -e

# it seems azure holds onto info across runs?
# this solves build issues
rm -rf ${HOME}/miniforge3

curl -L https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh > miniforge3.sh
chmod u+x miniforge3.sh
bash miniforge3.sh -b -p ${HOME}/miniforge3
source $HOME/miniforge3/etc/profile.d/conda.sh
conda activate base

cat .ci_scripts/condarc > $HOME/.condarc

conda install -yq \
  python=3.7 \
  conda-build \
  anaconda-client \
  conda-forge-pinning \
  shyaml \
  tqdm \
  click \
  ruamel.yaml \
  ruamel.yaml.jinja2 \
  wurlitzer \
  rpm-tools \
  jinja2

conda list

conda info

echo "condarc:"
cat $HOME/.condarc
