#!/bin/sh
# py2app needs a *framework* build of Python; uv's managed interpreters
# (python-build-standalone) aren't one, so the build must run on Homebrew's.
exec uv run --python /opt/homebrew/opt/python@3.13/bin/python3.13 \
    --group build "$(dirname "$0")/make_app.py"
