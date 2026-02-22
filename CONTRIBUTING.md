# Contributing

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Running Checks

```bash
python -m pytest -q
python -m py_compile main.py src/graphapi/__init__.py src/graphapi/__main__.py src/graphapi/app.py
```

## Pull Requests

1. Keep changes focused and atomic.
2. Add or update tests for behavioral changes.
3. Update docs (`README.md`, `CHANGELOG.md`) when relevant.
4. Ensure workflows are green (`CI`, `Tests`, `Gitleaks`).
