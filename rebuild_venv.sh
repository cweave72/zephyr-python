#!/usr/bin/env bash
#
# Create the python tools venv again. This script also generates all
# protobuf RPC bindings again.
#
# A plain `uv sync` does not generate the bindings again. It only examines the
# installed packages. The --reinstall option starts the proto_builder backend.
# The backend deletes and generates the lib directory of each package.
#
# Run this script. Do not source this script. Then source init_venv.sh to
# activate the venv.

set -euo pipefail

cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")"

# A workspace checkout sets PROTO_BASE. A standalone checkout uses the proto
# submodule. An uninitialized submodule is an empty directory, thus look for a
# proto file.
if [[ -z "${PROTO_BASE:-}" ]]; then
    if compgen -G "proto/*/*.proto" > /dev/null; then
        export PROTO_BASE="$(pwd)/proto"
    else
        echo "Error: PROTO_BASE is not set and the proto submodule is empty."
        echo "In a workspace, set up the workspace environment. Refer to the"
        echo "workspace README."
        echo "In a standalone checkout, initialize the submodule:"
        echo "    git submodule update --init"
        exit 1
    fi
fi

if [[ ! -d "$PROTO_BASE" ]]; then
    echo "Error: PROTO_BASE points at a missing directory: $PROTO_BASE"
    exit 1
fi

echo "PROTO_BASE = $PROTO_BASE"

echo "-- Delete the venv and the build files"
rm -rf .venv
find . -maxdepth 2 -type d \( -name '*.egg-info' -o -name build \) -prune -exec rm -rf {} +
rm -f ./*/*.log

echo "-- Install the packages again. This starts the proto_builder backend."
uv sync --reinstall

echo
echo "Done. Activate with:  source init_venv.sh"
