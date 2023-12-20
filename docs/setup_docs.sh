#!/bin/bash
# setup_docs.sh

# Navigate to the first directory inside /carthage/docs/
cd /carthage/docs

# Create a virtual environment
python3 -m virtualenv .venv

# Activate the virtual environment
source .venv/bin/activate

# Install dependencies from requirements.txt
pip3 install -r requirements.txt

# build the sphinx docs
sphinx-build -b html . ./build/
