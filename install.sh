#!/bin/sh
# Install TimeDay for the current user from any working directory.
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
    echo 'Для установки TimeDay нужен Python 3. Установите пакет python3.' >&2
    exit 1
fi
exec python3 "$project_dir/install.py" "$@"
