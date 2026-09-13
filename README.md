# LocalForge

LocalForge is a production-oriented, local-first Python project intended to run through Docker.

The repository currently contains governance, collaboration, formatting, and Git hygiene files only. Application
scaffolding, dependency metadata, container definitions, and runtime configuration will be added when the project
architecture is selected.

## Repository policies

- [Contributing](CONTRIBUTING.md)
- [Commit convention](COMMIT_CONVENTION.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)
- [Support policy](SUPPORT.md)
- [Governance](GOVERNANCE.md)
- [Changelog](CHANGELOG.md)

## Local repository setup

Install `pre-commit`, then enable both configured hook types:

```console
pre-commit install
git config commit.template .gitmessage
```

Validate the complete repository at any time:

```console
pre-commit run --all-files
```
