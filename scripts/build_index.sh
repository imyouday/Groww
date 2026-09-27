#!/usr/bin/env bash
# Builds the offline index and asks one question, so a fresh machine can be verified in one step.
# Usage: ./scripts/build_index.sh "What is the expense ratio of the HDFC Large Cap fund?" [extractive] [--refresh]

set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
    echo "python not found at $PYTHON; create the environment with 'python3 -m venv .venv' and install requirements.txt" >&2
    exit 1
fi

QUESTION="${1:?usage: build_index.sh <question> [provider] [--refresh|--rebuild]}"
PROVIDER="${2:-extractive}"

BUILD_ARGS=(build)
for flag in "${@:3}"; do
    case "$flag" in
        --refresh|--rebuild) BUILD_ARGS+=("$flag") ;;
        *) echo "unknown option: $flag" >&2; exit 2 ;;
    esac
done

echo '== build =='
"$PYTHON" -m src.pipeline "${BUILD_ARGS[@]}"
echo '== ask =='
"$PYTHON" -m src.pipeline ask "$QUESTION" --provider "$PROVIDER" --debug
