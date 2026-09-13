# LocalForge

LocalForge is a local Django project using SQLite and `uv` for dependency management.

## Requirements

- Python 3.14 or newer
- `uv`

## Setup

Create the Python 3.14 virtual environment, install all dependency groups, and initialize the database:

```console
uv venv --python 3.14 .venv
uv sync --all-groups
uv run poe migrate
```

Package downloads use the Microsoft package feed configured in `pyproject.toml`, with system certificate verification
enabled. The lockfile records this feed's artifact URLs so `uv sync --all-groups --frozen` does not attempt direct
downloads from `files.pythonhosted.org`, which fails TLS negotiation on this managed network.

Changing the default index alone does not redirect URLs already recorded in `uv.lock`. After an approved index change,
run `uv lock` and then `uv sync --all-groups --frozen`. Do not disable TLS certificate verification.

Start the development server:

```console
uv run poe dev
```

Open `http://127.0.0.1:8000/admin/` to verify that Django is running.

## Quality checks

The `.agents` directory is excluded from pre-commit file checks, Markdown/YAML linting, Python linting and formatting,
type-check discovery, and test discovery. Its files remain available and can still be tracked in Git.

Run linting and formatting checks:

```console
uv run poe lint
uv run poe format-check
```

Run both type checkers:

```console
uv run poe typecheck
```

The ty task runs the locked release from uv's trusted tool cache, which also works on Windows systems that block
executables launched directly from a project virtual environment.

Run unit and integration tests with branch coverage:

```console
uv run poe test
uv run poe test-parallel
```

Run a focused group without applying the whole-suite coverage threshold:

```console
uv run poe test-unit
uv run poe test-integration
```

The test command requires 100% line and branch coverage. It writes the browsable report to `htmlcov/index.html` and
the machine-readable reports to `coverage.xml` and `test-results/pytest.xml`. No source lines are excluded from
measurement.

Run every local quality check with one command:

```console
uv run poe check
```

List all configured commands and their descriptions with `uv run poe`.

## License

LocalForge is available under the [MIT License](LICENSE).

## Repository policies

- [Contributing](CONTRIBUTING.md)
- [Commit convention](COMMIT_CONVENTION.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)
- [Support policy](SUPPORT.md)
- [Governance](GOVERNANCE.md)
- [Changelog](CHANGELOG.md)

## Local repository setup

Install the dependencies, then enable all configured hook types:

```console
uv sync --all-groups
uv run pre-commit install
```

The installation enables pre-commit, commit-message, and pre-push hooks. Pre-push runs mypy, ty, and the full test suite.
Configure the commit template and repository-local identity using [the commit convention](COMMIT_CONVENTION.md).

Validate the complete repository at any time:

```console
uv run pre-commit run --all-files
```
