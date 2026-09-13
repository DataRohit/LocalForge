"""Unit tests for the environment encryption helper.

Covers the tooling and key preconditions, the overwrite refusal, every documented exit code, and
the command the helper builds, using a fabricated host so no test requires SOPS or age.
"""

import os
import runpy
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from scripts import sops_env

if TYPE_CHECKING:
    from collections.abc import Sequence

CIPHERTEXT = "NAME=ENC[AES256_GCM,data:abc,type:str]\n"
PLAINTEXT = "NAME=value\n"
DOCUMENTED_EXIT_CODES = {
    "ok": 0,
    "failed": 1,
    "tooling_absent": 2,
    "no_age_key": 3,
    "would_overwrite": 4,
}


@dataclass
class FakeHost:
    """Fabricated host for the encryption helper.

    Describes which tools are installed, whether an age key exists, and what a command produces, so
    every failure path can be exercised without SOPS or age. Inherits nothing; it satisfies the
    Environment protocol structurally.

    Attributes:
        tools: Names of the tools to report as installed.
        key_file: Age key file to report, or None when no key exists.
        result: Outcome every command reports.
        commands: Argument vectors the helper asked to run, in order.
    """

    tools: frozenset[str] = field(default_factory=lambda: frozenset(sops_env.REQUIRED_TOOLS))
    key_file: Path | None = Path("keys.txt")
    result: sops_env.CommandResult = field(
        default_factory=lambda: sops_env.CommandResult(ok=True, output=CIPHERTEXT, error=""),
    )
    commands: list[tuple[str, ...]] = field(default_factory=list)

    def which(self, tool: str) -> str | None:
        """Report whether a tool is installed on the fabricated host.

        Reads the configured set, treating an unlisted tool as absent.

        Arguments:
            tool: Executable name to resolve.

        Returns:
            A path-like string when the tool is configured, and None otherwise.
        """
        return tool if tool in self.tools else None

    def age_key_file(self) -> Path | None:
        """Report the fabricated age key file.

        Returns whatever the fixture was configured with, including the absent case that stands in
        for a machine that has never generated a key.

        Returns:
            The configured key file, or None.
        """
        return self.key_file

    def run(self, command: Sequence[str]) -> sops_env.CommandResult:
        """Record a command and report the fabricated outcome.

        Keeps the argument vector so a test can assert on the command the helper built, which is
        what pins the dotenv mode the round trip depends on.

        Arguments:
            command: Argument vector the helper asked to run.

        Returns:
            The configured outcome.
        """
        self.commands.append(tuple(command))

        return self.result


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Build a temporary repository with both environment files and a recipient configuration.

    Writes a plaintext file, an encrypted file, and the configuration the helper requires, so the
    helper acts entirely within the temporary tree and never reaches the developer's own.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        The temporary repository root.
    """
    (tmp_path / ".env.development").write_text(PLAINTEXT, encoding="utf-8")
    (tmp_path / ".env.development.sops").write_text(CIPHERTEXT, encoding="utf-8")
    (tmp_path / sops_env.SOPS_CONFIG_NAME).write_text(
        "creation_rules:\n  - path_regex: test\n    age: age1test\n",
        encoding="utf-8",
    )

    return tmp_path


@pytest.mark.unit
def test_absent_tooling_is_reported_with_its_install_command(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Say what to install rather than failing obscurely.

    Confirms a machine without the encryption tools stops with the documented code and names both
    install commands, which is the difference between a clear message and a missing-binary error.

    Arguments:
        repository: Temporary repository holding the environment files.
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the run does not stop with the documented code and guidance.
    """
    host = FakeHost(tools=frozenset())

    code = sops_env.main(
        ["--mode", "encrypt", "--environment", "development"],
        root=repository,
        environment=host,
    )
    printed = capsys.readouterr().out

    assert code == sops_env.EXIT_TOOLING_ABSENT
    assert "sops, age not found" in printed
    assert "SecretsOPerationS.SOPS" in printed
    assert "FiloSottile.age" in printed


@pytest.mark.unit
@pytest.mark.parametrize(
    ("installed", "expected"),
    [
        (frozenset(), ("sops", "age")),
        (frozenset({"sops"}), ("age",)),
        (frozenset({"age"}), ("sops",)),
        (frozenset({"sops", "age"}), ()),
    ],
)
def test_every_absent_tool_is_named_at_once(
    installed: frozenset[str],
    expected: tuple[str, ...],
) -> None:
    """Name everything missing in one message.

    Confirms both tools are checked before anything runs, so a developer installs once rather than
    discovering the second absence after fixing the first.

    Arguments:
        installed: Tools the fabricated host reports as present.
        expected: Tools the helper should report as absent.

    Returns:
        None.

    Raises:
        AssertionError: If the reported absences differ from the expectation.
    """
    assert sops_env.missing_tools(FakeHost(tools=installed)) == expected


@pytest.mark.unit
def test_encryption_writes_the_ciphertext_beside_the_plaintext(repository: Path) -> None:
    """Produce a committable encrypted file.

    Confirms a successful encryption writes the ciphertext to the committed name, which is the
    artifact that reaches version control in place of the credentials.

    Arguments:
        repository: Temporary repository holding the environment files.

    Returns:
        None.

    Raises:
        AssertionError: If the encrypted file is not written.
    """
    host = FakeHost()

    code = sops_env.main(
        ["--mode", "encrypt", "--environment", "development"],
        root=repository,
        environment=host,
    )

    assert code == sops_env.EXIT_OK
    assert (repository / ".env.development.sops").read_text(encoding="utf-8") == CIPHERTEXT


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["encrypt", "decrypt"])
def test_both_directions_use_the_dotenv_format(repository: Path, mode: str) -> None:
    """Keep the file a key-and-value document in both directions.

    Confirms the helper pins the dotenv input and output types, without which the tool rewrites the
    file as a different format and a round trip no longer returns the original.

    Arguments:
        repository: Temporary repository holding the environment files.
        mode: Direction to run.

    Returns:
        None.

    Raises:
        AssertionError: If either format flag is missing from the command.
    """
    host = FakeHost()

    sops_env.main(
        ["--mode", mode, "--environment", "development"],
        root=repository,
        environment=host,
    )

    assert host.commands
    command = host.commands[0]

    assert command[:2] == ("sops", f"--{mode}")
    assert command[2:4] == ("--input-type", "dotenv")
    assert command[4:6] == ("--output-type", "dotenv")


@pytest.mark.unit
def test_a_failed_encryption_writes_nothing(repository: Path) -> None:
    """Leave no half-written encrypted file behind.

    Confirms a failing tool stops with the documented code and leaves the existing ciphertext
    untouched, since a truncated encrypted file still looks committable.

    Arguments:
        repository: Temporary repository holding the environment files.

    Returns:
        None.

    Raises:
        AssertionError: If the run succeeds, or the encrypted file changes.
    """
    host = FakeHost(result=sops_env.CommandResult(ok=False, output="", error="no recipient"))

    code = sops_env.main(
        ["--mode", "encrypt", "--environment", "development"],
        root=repository,
        environment=host,
    )

    assert code == sops_env.EXIT_FAILED
    assert (repository / ".env.development.sops").read_text(encoding="utf-8") == CIPHERTEXT


@pytest.mark.unit
def test_decryption_writes_the_plaintext(repository: Path) -> None:
    """Recover the credentials from the committed file.

    Confirms a successful decryption writes the plaintext environment file, which is how a fresh
    clone reaches a working configuration.

    Arguments:
        repository: Temporary repository holding the environment files.

    Returns:
        None.

    Raises:
        AssertionError: If the plaintext is not written.
    """
    (repository / ".env.development").unlink()
    host = FakeHost(result=sops_env.CommandResult(ok=True, output=PLAINTEXT, error=""))

    code = sops_env.main(
        ["--mode", "decrypt", "--environment", "development"],
        root=repository,
        environment=host,
    )

    assert code == sops_env.EXIT_OK
    assert (repository / ".env.development").read_text(encoding="utf-8") == PLAINTEXT


@pytest.mark.unit
def test_a_newer_plaintext_is_not_overwritten(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Refuse to discard an unencrypted change.

    Confirms a plaintext file newer than its ciphertext stops the run with the documented code,
    because that plaintext usually holds a change nobody has encrypted yet.

    Arguments:
        repository: Temporary repository holding the environment files.
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the run does not refuse, or the plaintext changes.
    """
    encrypted = repository / ".env.development.sops"
    plaintext = repository / ".env.development"
    plaintext.write_text("NAME=newer\n", encoding="utf-8")
    encrypted_time = encrypted.stat().st_mtime
    os.utime(plaintext, (encrypted_time + 10, encrypted_time + 10))

    code = sops_env.main(
        ["--mode", "decrypt", "--environment", "development"],
        root=repository,
        environment=FakeHost(),
    )

    assert code == sops_env.EXIT_WOULD_OVERWRITE
    assert "refusing to overwrite" in capsys.readouterr().out
    assert plaintext.read_text(encoding="utf-8") == "NAME=newer\n"


@pytest.mark.unit
def test_a_newer_plaintext_is_overwritten_when_forced(repository: Path) -> None:
    """Allow the refusal to be overridden deliberately.

    Confirms the force switch replaces a newer plaintext, which is what recovers a machine whose
    local file is known to be wrong.

    Arguments:
        repository: Temporary repository holding the environment files.

    Returns:
        None.

    Raises:
        AssertionError: If the plaintext is not replaced.
    """
    plaintext = repository / ".env.development"
    encrypted_time = (repository / ".env.development.sops").stat().st_mtime
    plaintext.write_text("NAME=newer\n", encoding="utf-8")
    os.utime(plaintext, (encrypted_time + 10, encrypted_time + 10))

    host = FakeHost(result=sops_env.CommandResult(ok=True, output=PLAINTEXT, error=""))
    code = sops_env.main(
        ["--mode", "decrypt", "--environment", "development", "--force"],
        root=repository,
        environment=host,
    )

    assert code == sops_env.EXIT_OK
    assert plaintext.read_text(encoding="utf-8") == PLAINTEXT


@pytest.mark.unit
def test_decryption_without_a_key_is_reported_clearly(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Explain a missing age key rather than failing inside the tool.

    Confirms a machine with no age key stops with the documented code and says how to make one,
    since this is the expected state of a fresh clone.

    Arguments:
        repository: Temporary repository holding the environment files.
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the run does not stop with the documented code and guidance.
    """
    (repository / ".env.development").unlink()

    code = sops_env.main(
        ["--mode", "decrypt", "--environment", "development"],
        root=repository,
        environment=FakeHost(key_file=None),
    )
    printed = capsys.readouterr().out

    assert code == sops_env.EXIT_NO_AGE_KEY
    assert "age-keygen" in printed


@pytest.mark.unit
def test_a_failing_decryption_is_reported(repository: Path) -> None:
    """Report a tool that cannot decrypt.

    Confirms a failing decryption stops with the documented code and leaves the existing plaintext
    alone rather than truncating it.

    Arguments:
        repository: Temporary repository holding the environment files.

    Returns:
        None.

    Raises:
        AssertionError: If the run succeeds, or the plaintext changes.
    """
    plaintext = repository / ".env.development"
    encrypted_time = (repository / ".env.development.sops").stat().st_mtime
    os.utime(plaintext, (encrypted_time - 10, encrypted_time - 10))

    host = FakeHost(result=sops_env.CommandResult(ok=False, output="", error="no matching key"))
    code = sops_env.main(
        ["--mode", "decrypt", "--environment", "development"],
        root=repository,
        environment=host,
    )

    assert code == sops_env.EXIT_FAILED
    assert plaintext.read_text(encoding="utf-8") == PLAINTEXT


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["encrypt", "decrypt"])
def test_an_absent_source_file_is_reported(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    """Say what is missing rather than producing an empty file.

    Confirms both directions stop when their source does not exist, so an encryption with no
    plaintext and a decryption with no ciphertext are both explained.

    Arguments:
        repository: Temporary repository holding the recipient configuration.
        capsys: Fixture capturing standard output.
        mode: Direction to run.

    Returns:
        None.

    Raises:
        AssertionError: If the run does not stop with the documented code.
    """
    (repository / ".env.development").unlink()
    (repository / ".env.development.sops").unlink()

    code = sops_env.main(
        ["--mode", mode, "--environment", "development"],
        root=repository,
        environment=FakeHost(),
    )

    assert code == sops_env.EXIT_FAILED
    assert "does not exist" in capsys.readouterr().out


@pytest.mark.unit
def test_an_absent_configuration_file_is_reported(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stop when the recipient configuration is missing.

    Confirms an absent configuration file stops the run, because the age recipient is read from it
    and encrypting without one would produce a file nobody can decrypt.

    Arguments:
        tmp_path: Temporary directory carrying no configuration.
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the run does not stop with the documented code.
    """
    (tmp_path / ".env.development").write_text(PLAINTEXT, encoding="utf-8")

    code = sops_env.main(
        ["--mode", "encrypt", "--environment", "development"],
        root=tmp_path,
        environment=FakeHost(),
    )

    assert code == sops_env.EXIT_FAILED
    assert sops_env.SOPS_CONFIG_NAME in capsys.readouterr().out


@pytest.mark.unit
def test_the_committed_configuration_names_an_age_recipient() -> None:
    """Keep the recipient configuration committed and complete.

    Confirms the repository's own configuration exists and names an age recipient, so a fresh clone
    can encrypt without anyone re-deriving the setup.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the configuration is absent or names no recipient.
    """
    text = (sops_env.REPOSITORY_ROOT / sops_env.SOPS_CONFIG_NAME).read_text(encoding="utf-8")

    assert "creation_rules:" in text
    assert "age1" in text


@pytest.mark.unit
def test_an_absent_file_is_never_considered_newer(tmp_path: Path) -> None:
    """Treat a missing file as older than anything.

    Confirms a first decryption onto a machine with no plaintext is not mistaken for an overwrite,
    which is the ordinary case on a fresh clone.

    Arguments:
        tmp_path: Temporary directory standing in for a repository.

    Returns:
        None.

    Raises:
        AssertionError: If an absent file is reported as newer.
    """
    present = tmp_path / "present"
    present.write_text("x", encoding="utf-8")
    absent = tmp_path / "absent"

    assert sops_env.newer_than(absent, present) is False
    assert sops_env.newer_than(present, absent) is False


@pytest.mark.unit
def test_the_host_resolves_tools_through_the_search_path() -> None:
    """Ask the operating system where a tool is.

    Confirms the host surface delegates tool discovery to the standard resolver, so the helper
    honours whatever the developer has installed.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the resolver is not consulted.
    """
    with patch("shutil.which", return_value="C:/tools/sops.exe") as which:
        assert sops_env.HostEnvironment().which("sops") == "C:/tools/sops.exe"

    which.assert_called_once_with("sops")


@pytest.mark.unit
def test_the_host_prefers_the_key_file_override() -> None:
    """Look where the encryption tool itself looks.

    Confirms the explicit override is preferred over the platform default, so the helper agrees
    with the tool about which key would be used.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the override is not preferred.
    """
    with (
        patch.dict("os.environ", {"SOPS_AGE_KEY_FILE": "C:/keys/age.txt"}),
        patch.object(Path, "is_file", return_value=True),
    ):
        assert sops_env.HostEnvironment().age_key_file() == Path("C:/keys/age.txt")


@pytest.mark.unit
def test_the_host_reports_no_key_when_none_exists() -> None:
    """Report an absent key rather than a path that does not exist.

    Confirms every candidate location being empty yields no key, which is what drives the clear
    message about generating one.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a key is reported when none exists.
    """
    with (
        patch.dict("os.environ", {}, clear=True),
        patch.object(Path, "is_file", return_value=False),
    ):
        assert sops_env.HostEnvironment().age_key_file() is None


@pytest.mark.unit
def test_the_host_finds_the_platform_default_key_file() -> None:
    """Fall back to the location the tool uses by default.

    Confirms the per-platform default is consulted when no override is set, since that is where the
    key generator writes by default.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the default location is not consulted.
    """
    with (
        patch.dict("os.environ", {"APPDATA": "C:/AppData"}, clear=True),
        patch.object(Path, "is_file", return_value=True),
    ):
        assert sops_env.HostEnvironment().age_key_file() == Path("C:/AppData/sops/age/keys.txt")


@pytest.mark.unit
def test_the_host_runs_a_command_and_reports_its_output() -> None:
    """Report what a command produced.

    Confirms a successful command yields its output, which is the ciphertext or plaintext the
    helper then writes.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the outcome does not carry the command's output.
    """
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=CIPHERTEXT, stderr="")
    root = Path("C:/somewhere/else")

    with (
        patch("shutil.which", return_value="C:/tools/sops.exe"),
        patch.object(subprocess, "run", return_value=completed) as run,
    ):
        result = sops_env.HostEnvironment(root).run(["sops", "--encrypt"])

    assert result.ok is True
    assert result.output == CIPHERTEXT
    assert run.call_args.args[0][0] == "C:/tools/sops.exe"
    assert run.call_args.kwargs["cwd"] == root


@pytest.mark.unit
def test_the_host_reports_a_failing_command_with_its_error() -> None:
    """Surface the tool's own explanation.

    Confirms a failing command yields its error text, so the helper can report why the tool refused
    rather than only that it did.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the outcome does not carry the command's error.
    """
    completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=" boom \n")

    with (
        patch("shutil.which", return_value="C:/tools/sops.exe"),
        patch.object(subprocess, "run", return_value=completed),
    ):
        result = sops_env.HostEnvironment().run(["sops", "--decrypt"])

    assert result.ok is False
    assert result.error == "boom"


@pytest.mark.unit
@pytest.mark.parametrize("failure", [OSError("denied"), subprocess.TimeoutExpired("sops", 1)])
def test_a_command_that_cannot_run_is_reported_rather_than_raised(
    failure: Exception,
) -> None:
    """Report an unstartable or hanging tool.

    Confirms a process that cannot start and one that exceeds its timeout are both reported through
    the result, so the helper always explains rather than raising.

    Arguments:
        failure: Exception the fabricated execution raises.

    Returns:
        None.

    Raises:
        AssertionError: If the failure is not reported through the result.
    """
    with (
        patch("shutil.which", return_value="C:/tools/sops.exe"),
        patch.object(subprocess, "run", side_effect=failure) as run,
    ):
        result = sops_env.HostEnvironment().run(["sops", "--encrypt"])

    run.assert_called_once()

    assert result.ok is False
    assert result.error


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["encrypt", "decrypt"])
def test_tool_error_text_never_reaches_the_output(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    """Keep the tool's own error text out of the terminal.

    Confirms a failure whose error text quotes a line of the file does not print that line, because
    the encryption tool reports an unparsable dotenv line by including it, and the file it is
    reading is a file of credentials.

    Arguments:
        repository: Temporary repository holding the environment files.
        capsys: Fixture capturing standard output.
        mode: Direction to run.

    Returns:
        None.

    Raises:
        AssertionError: If the error text reaches the output.
    """
    leaked = "invalid dotenv input line: POSTGRES_PASSWORD=hunter2"
    host = FakeHost(result=sops_env.CommandResult(ok=False, output="", error=leaked))

    code = sops_env.main(
        ["--mode", mode, "--environment", "development", "--force"],
        root=repository,
        environment=host,
    )
    printed = capsys.readouterr().out

    assert code == sops_env.EXIT_FAILED
    assert "hunter2" not in printed
    assert "POSTGRES_PASSWORD" not in printed


@pytest.mark.unit
def test_the_documented_exit_codes_are_the_ones_implemented() -> None:
    """Keep the exit codes in step with the document.

    Confirms each code the helper returns is the one the conventions document assigns it, so a
    caller branching on an exit code reads the same contract the helper implements.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an implemented code differs from the documented one.
    """
    document = (sops_env.REPOSITORY_ROOT / "docs" / "platform" / "conventions.md").read_text(
        encoding="utf-8"
    )

    assert DOCUMENTED_EXIT_CODES["ok"] == sops_env.EXIT_OK
    assert DOCUMENTED_EXIT_CODES["failed"] == sops_env.EXIT_FAILED
    assert DOCUMENTED_EXIT_CODES["tooling_absent"] == sops_env.EXIT_TOOLING_ABSENT
    assert DOCUMENTED_EXIT_CODES["no_age_key"] == sops_env.EXIT_NO_AGE_KEY
    assert DOCUMENTED_EXIT_CODES["would_overwrite"] == sops_env.EXIT_WOULD_OVERWRITE
    assert "`1` the operation" in document
    assert "`2` `sops` or `age` not on `PATH`" in document


@pytest.mark.unit
def test_an_unresolvable_tool_is_reported_without_being_run() -> None:
    """Report an absent tool from the process layer too.

    Confirms a command whose executable cannot be resolved is reported through the result rather
    than attempted, which keeps the failure identical whichever layer notices it first.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the command is attempted, or the absence is not reported.
    """
    with (
        patch("shutil.which", return_value=None),
        patch.object(subprocess, "run") as run,
    ):
        result = sops_env.HostEnvironment().run(["sops", "--encrypt"])

    run.assert_not_called()

    assert result.ok is False
    assert "not found on PATH" in result.error


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mode", "output"),
    [("encrypt", CIPHERTEXT), ("decrypt", PLAINTEXT)],
)
def test_a_write_failure_leaves_the_destination_intact(
    repository: Path,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    output: str,
) -> None:
    """Never truncate a file the write could not replace.

    Confirms a failure while staging the new contents leaves the existing file exactly as it was
    and removes the partial file, so an interrupted run cannot destroy the only copy of a
    credential.

    Arguments:
        repository: Temporary repository holding the environment files.
        capsys: Fixture capturing standard output.
        mode: Direction to run.
        output: Contents the fabricated tool produces.

    Returns:
        None.

    Raises:
        AssertionError: If the destination changes, or a partial file survives.
    """
    host = FakeHost(result=sops_env.CommandResult(ok=True, output=output, error=""))
    destinations = {
        "encrypt": repository / ".env.development.sops",
        "decrypt": repository / ".env.development",
    }
    before = destinations[mode].read_bytes()

    with patch.object(Path, "write_text", side_effect=OSError("disk full")):
        code = sops_env.main(
            ["--mode", mode, "--environment", "development", "--force"],
            root=repository,
            environment=host,
        )

    assert code == sops_env.EXIT_FAILED
    assert "could not write" in capsys.readouterr().out
    assert destinations[mode].read_bytes() == before
    assert not list(repository.glob("*.partial"))


@pytest.mark.unit
def test_a_staged_file_is_removed_when_replacement_fails(tmp_path: Path) -> None:
    """Leave no partial file behind when replacement fails.

    Confirms the staged file is cleaned up when the atomic replacement itself fails, so a failed
    write never leaves a stray copy of a credential beside the real one.

    Arguments:
        tmp_path: Temporary directory standing in for a repository.

    Returns:
        None.

    Raises:
        AssertionError: If the staged file survives, or the failure is swallowed.
    """
    destination = tmp_path / ".env.development"
    destination.write_text(PLAINTEXT, encoding="utf-8")

    with (
        patch.object(Path, "replace", side_effect=OSError("replacement refused")),
        pytest.raises(OSError, match="replacement refused"),
    ):
        sops_env.write_atomically(destination, "NAME=other\n")

    assert destination.read_text(encoding="utf-8") == PLAINTEXT
    assert not list(tmp_path.glob("*.partial"))


@pytest.mark.unit
def test_the_script_guard_runs_the_helper(capsys: pytest.CaptureFixture[str]) -> None:
    """Run the helper through its script guard.

    Executes the module under the name Python assigns to a directly executed script with no tools
    installed, which exercises the guard an ordinary import never reaches.

    Arguments:
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the script does not exit with the documented code.
    """
    module_path = Path(sops_env.__file__)

    with (
        patch.object(sys, "argv", ["sops_env", "--mode", "encrypt", "--environment", "testing"]),
        patch("shutil.which", return_value=None),
        pytest.raises(SystemExit) as raised,
    ):
        runpy.run_path(str(module_path), run_name="__main__")

    capsys.readouterr()

    assert raised.value.code == sops_env.EXIT_TOOLING_ABSENT
