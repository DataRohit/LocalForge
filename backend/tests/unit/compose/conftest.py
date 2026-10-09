"""Fixtures for the Compose unit tests.

Supplies generated environment values built in a temporary directory, so no Compose test needs
to read the development environment file that belongs to the live stack.
"""

from pathlib import Path

import pytest
from scripts import gen_secrets

FIXTURE_BCRYPT_ROUNDS = 4


class NoTrackedFiles:
    """Version control stand-in that tracks nothing.

    Lets the generator write into a temporary directory without consulting Git. Inherits nothing
    and satisfies the generator's version control protocol structurally.

    Members:
        tracked_files: Report an empty index.
    """

    def tracked_files(self) -> frozenset[str]:
        """Report an empty index.

        Declares that no path in the temporary directory is under version control. Lets the
        generator write there without consulting Git.

        Arguments:
            None.

        Returns:
            An empty set.

        Raises:
            None.
        """
        return frozenset[str]()


@pytest.fixture(scope="session")
def fixture_environments(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, dict[str, str]]:
    """Generate both environment files into a temporary directory.

    Runs the real generator against the committed manifest with a low hashing cost, so tests can
    inspect development values without opening the live development file.

    Arguments:
        tmp_path_factory: Temporary directory factory supplied by the test framework.

    Returns:
        Parsed variables keyed by environment name.

    Raises:
        AssertionError: If the generator does not complete.
    """
    root = tmp_path_factory.mktemp("fixture-environments")
    (root / ".env.example").write_text(
        gen_secrets.MANIFEST_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(gen_secrets, "BCRYPT_ROUNDS", FIXTURE_BCRYPT_ROUNDS)
        code = gen_secrets.main([], root=root, version_control=NoTrackedFiles())

    assert code == gen_secrets.EXIT_OK

    return {
        name: gen_secrets.parse_env_text(
            Path(root / f".env.{name}").read_text(encoding="utf-8"),
        )
        for name in ("development", "testing")
    }
