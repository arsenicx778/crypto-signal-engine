#!/bin/zsh

cd "$(dirname "$0")" || exit 1
exec caffeinate -dimsu /Users/edom/crypto-signal-engine/.venv/bin/python -m experiment.runner
