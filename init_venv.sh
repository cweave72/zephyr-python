if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo "Error: Script must be sourced."
    exit
fi

# SCRIPTPATH will be set to the path of this script.
SCRIPTPATH=$(dirname $(realpath "${BASH_SOURCE[0]}"))

# A workspace checkout sets PROTO_BASE. A standalone checkout uses the proto
# submodule. An uninitialized submodule is an empty directory, thus look for a
# proto file.
if [ -z "${PROTO_BASE:-}" ]; then
    if compgen -G "$SCRIPTPATH/proto/*/*.proto" > /dev/null; then
        export PROTO_BASE="$SCRIPTPATH/proto"
        echo "Set PROTO_BASE=$PROTO_BASE"
    else
        echo "Warning: PROTO_BASE is not set and the proto submodule is empty."
        echo "Run 'git submodule update --init' or source workspace-env.sh."
    fi
fi

function activate_env {
    uv sync
    source $1/bin/activate 2>/dev/null
    if [ ! $? -eq 0 ]; then
        return 1
    fi
}

VENV_DIR=.venv

# Activate the virtual env.
activate_env $VENV_DIR
if [ $? -eq 0 ]; then
    echo "Successfully activated venv."
    return 0
fi
