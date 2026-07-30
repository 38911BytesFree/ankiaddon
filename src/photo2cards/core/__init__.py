"""Pure logic: no `anki` or `aqt` imports anywhere under this package.

That rule is what makes the add-on testable — everything here runs under plain
pytest without launching Anki. See tests/ for the harness.
"""
