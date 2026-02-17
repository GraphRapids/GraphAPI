# Release Process

This project uses Semantic Versioning and tagged GitHub releases.

## Release Checklist

1. Ensure `main` is green (`CI`, `Tests`, `Gitleaks`).
2. Update `CHANGELOG.md`.
3. Bump `version` in `pyproject.toml`.
4. Tag and push `vX.Y.Z`.
5. `Release` workflow publishes the GitHub release.
