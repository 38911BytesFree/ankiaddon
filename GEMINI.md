# Anki Addon (photo2cards) Project Rules

## Mandatory Local CI Checks
Before completing any task, feature, or fix, always run the full verification suite locally:
```sh
python -m pytest
python -m ruff check src tests build.py
python build.py
```
All commands must pass cleanly before declaring completion or committing.
