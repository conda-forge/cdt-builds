#!/bin/bash
set -e

if [ "$CONFIG" == "linux_python3.7" ]
then
    pyver="3.7"
    os="Linux"
elif [ "$CONFIG" == "osx_python3.7" ]
then
    pyver="3.7"
    os="MacOSX"
fi

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
  ruamel.yaml

conda list

conda info

echo "condarc:"
cat $HOME/.condarc
