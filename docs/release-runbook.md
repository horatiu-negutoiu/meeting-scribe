# Release runbook

Use this procedure for every public release. The release workflow uses PyPI
Trusted Publishing and GitHub Actions OIDC; never create or store a PyPI API
token for this project.

## One-time setup

1. Create GitHub environments named `testpypi` and `pypi`. Require an
   approver for `pypi` so production publishing is deliberate.
2. On TestPyPI, add a pending publisher with these values:

   | Setting | Value |
   | --- | --- |
   | PyPI project name | `meeting-transcribe` |
   | GitHub owner | `horatiu-negutoiu` |
   | GitHub repository | `meeting-scribe` |
   | Workflow filename | `publish.yml` |
   | Environment name | `testpypi` |

3. Add the same pending publisher on PyPI, using environment name `pypi`.
   After the first successful upload, each pending publisher becomes an active
   trusted publisher automatically.

## Release

1. Confirm `main` is current, clean, and all intended changes have merged.
2. Choose the next version and update `project.version` in `pyproject.toml`.
   Run `uv lock` if the metadata or dependency set changed, then commit the
   release version.
3. Run the local verification:

   ```console
   uv sync --extra dev --locked
   uv run pytest
   uv build --out-dir dist
   uv run --with twine twine check dist/*
   ```

4. Create and push an annotated tag matching the package version exactly:

   ```console
   git tag -a vX.Y.Z -m "Release vX.Y.Z"
   git push origin vX.Y.Z
   ```

5. Watch the `Publish to PyPI` GitHub Actions run. It verifies the tag,
   tests, builds, validates artifacts, uploads them to TestPyPI, then installs
   the TestPyPI package in a clean environment and runs
   `meeting-scribe --help`.
6. Review the TestPyPI job. The production job also checks immediately before
   upload that `meeting-transcribe` is still unclaimed on PyPI. Approve the
   `pypi` environment only after both checks pass.
7. Once the PyPI job succeeds, verify the public installation in a new virtual
   environment:

   ```console
   python -m venv /tmp/meeting-transcribe-release-check
   /tmp/meeting-transcribe-release-check/bin/python -m pip install --upgrade pip
   /tmp/meeting-transcribe-release-check/bin/python -m pip install meeting-transcribe==X.Y.Z
   /tmp/meeting-transcribe-release-check/bin/meeting-scribe --help
   ```

8. Open the project page at `https://pypi.org/project/meeting-transcribe/` and
   confirm its version, README, and package metadata. Create the corresponding
   GitHub Release from the signed tag.

## Rollback and remediation

PyPI releases cannot be replaced or deleted in ordinary operation. If a
release is faulty, stop further promotion, revoke the GitHub environment
approval if it is still pending, and publish a corrected version with a new
version number. If a credential, repository, or workflow is compromised,
disable the affected trusted publisher in PyPI/TestPyPI and the GitHub Actions
workflow, investigate, then create a replacement publisher after remediation.
If the project name is claimed before production approval, do not publish;
cancel the release and choose a different distribution name.
