if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo "Error: Script must be sourced."
    exit
fi

# SCRIPTPATH will be set to the path of this script.
SCRIPTPATH=$(dirname $(realpath "${BASH_SOURCE[0]}"))

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
