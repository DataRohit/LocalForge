#!/usr/bin/env bash
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$repository_root/backend/pyproject.toml" ]; then
    project_root="$repository_root/backend"
else
    project_root="$repository_root"
fi

cd "$repository_root"
export PYTHONPATH="$repository_root:$repository_root/backend:$repository_root/backend/src${PYTHONPATH:+:$PYTHONPATH}"
exec uv run --project "$project_root" poe -C "$project_root" "$@"
