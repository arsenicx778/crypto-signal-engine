#!/bin/zsh

cd "$(dirname "$0")" || exit 1
exec caffeinate -dimsu python3 start_all.py
