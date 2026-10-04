#!/usr/bin/env bash
set -eu

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -f "$repository_root/backend/pyproject.toml" ]; then
    project_root="$repository_root/backend"
else
    project_root="$repository_root"
fi

cd "$project_root"
exec uv run --project "$project_root" poe "$@"
