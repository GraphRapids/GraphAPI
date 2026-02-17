# Python Repository Template

Template for consistent public Python repositories.

## Features

- Standard repository governance files
- Shared reusable CI/test/security/release workflows
- Pyproject-based packaging and pytest setup
- Branch protection baseline file

## Requirements

- Python `>=3.10`
- GitHub Actions enabled

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Quick Start

1. Use this repository as a GitHub template.
2. Rename package path from `src/samplepkg` to your package name.
3. Update `pyproject.toml` project name, description, URLs, and authors.
4. Update security advisory links in issue template config and `SECURITY.md`.
5. Keep workflow files and branch protection contexts aligned (`CI`, `Tests`, `Gitleaks`).

## CLI Reference

```bash
python -m samplepkg
```

## Python API

```python
from samplepkg import hello

print(hello())
```

## Input Expectations

Customize this section for your project domain.

## Settings

Customize this section for project-specific settings.

## Troubleshooting

### Tests fail in CI but pass locally

Recreate the CI environment with a fresh virtualenv and run `python -m pytest -q`.

## Development

```bash
python -m pytest -q
python -m py_compile main.py src/samplepkg/__init__.py src/samplepkg/__main__.py
```

## Project Layout

```text
main.py
src/samplepkg/
tests/
examples/
.github/workflows/
```

## Governance and Community

- Security policy: `SECURITY.md`
- Contribution guide: `CONTRIBUTING.md`
- Code of conduct: `CODE_OF_CONDUCT.md`
- Changelog: `CHANGELOG.md`
- Release process: `RELEASE.md`

## Automation

- CI build and sanity checks: `.github/workflows/ci.yml`
- Test matrix + coverage gate: `.github/workflows/test.yml`
- Secret scanning (gitleaks): `.github/workflows/gitleaks.yml`
- Tagged releases: `.github/workflows/release.yml`
- Dependency updates: `.github/dependabot.yml`

## Acknowledgements

- Python Packaging ecosystem (PyPA)
- Pytest
- GitHub Actions

## Third-Party Notices

See `THIRD_PARTY_NOTICES.md`.

## License

Licensed under Apache License 2.0. See `LICENSE`.
