"""Encryption helper for the per-environment files.

Wraps SOPS and age so an environment file can be committed in encrypted form and recovered on
another machine, and so an absent encryption tool produces a clear instruction rather than an
obscure failure from a missing binary.
"""

import argparse
import contextlib
import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SOPS_CONFIG_NAME = ".sops.yaml"
COMMAND_TIMEOUT_SECONDS = 120

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_TOOLING_ABSENT = 2
EXIT_NO_AGE_KEY = 3
EXIT_WOULD_OVERWRITE = 4

ENCRYPT = "encrypt"
DECRYPT = "decrypt"
REQUIRED_TOOLS = ("sops", "age")
ENVIRONMENT_FILES = {
    "development": ".env.development",
    "testing": ".env.testing",
}


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Outcome of running an external command.

    Carries the pieces the helper reports on, keeping the caller from handling a process object.
    Inherits nothing; it is a plain frozen data holder with no methods.

    Attributes:
        ok: Whether the command exited successfully.
        output: Whatever the command wrote to standard output.
        error: Whatever the command wrote to standard error.
    """

    ok: bool
    output: str
    error: str


class Environment(Protocol):
    """Host surface the encryption helper depends on.

    Isolates tool discovery, key discovery, and command execution, so every failure path can be
    exercised without SOPS or age installed. Inherits Protocol, so any object providing these three
    methods satisfies it structurally.

    Attributes:
        which: Report whether a tool is on the search path.
        age_key_file: Report the age key file SOPS would use.
        run: Run a command and report what it produced.
    """

    def which(self, tool: str) -> str | None:
        """Report whether a tool is on the search path.

        Resolves the tool by name so an absent binary is reported before a command is attempted.

        Arguments:
            tool: Executable name to resolve.

        Returns:
            The resolved path, or None when the tool is absent.
        """

    def age_key_file(self) -> Path | None:
        """Report the age key file SOPS would use.

        Looks where SOPS itself looks, so the helper's verdict about a missing key matches what
        SOPS would do when asked to decrypt.

        Returns:
            The path to an existing key file, or None when no key is available.
        """

    def run(self, command: Sequence[str]) -> CommandResult:
        """Run a command and report what it produced.

        Executes the argument vector without raising, so a failure is reported through the result
        rather than an exception.

        Arguments:
            command: Argument vector to execute.

        Returns:
            The outcome of the command.
        """


class HostEnvironment:
    """Host surface backed by the real search path, filesystem, and subprocesses.

    Implements the Environment surface against this machine, resolving the age key from the
    override SOPS honours before the platform default, and running SOPS from a bound repository so
    it discovers that repository's own recipient configuration. Inherits nothing; it satisfies
    Environment structurally.

    Attributes:
        root: Repository SOPS is run from, which is where it looks for its configuration.
        which: Report whether a tool is on the search path.
        age_key_file: Report the age key file SOPS would use.
        run: Run a command and report what it produced.
    """

    def __init__(self, root: Path = REPOSITORY_ROOT) -> None:
        """Bind the host surface to one repository.

        Takes the repository explicitly so the recipient configuration SOPS discovers is the one
        belonging to the tree being operated on.

        Arguments:
            root: Repository SOPS should run from.

        Returns:
            None.
        """
        self.root = root

    def which(self, tool: str) -> str | None:
        """Report whether a tool is on the search path.

        Delegates to the standard resolver, which honours the platform's executable extensions.

        Arguments:
            tool: Executable name to resolve.

        Returns:
            The resolved path, or None when the tool is absent.
        """
        return shutil.which(tool)

    def age_key_file(self) -> Path | None:
        """Report the age key file SOPS would use.

        Checks the explicit override first and then the per-platform default, returning the first
        that exists so the helper agrees with SOPS about whether a key is available. A home
        directory the operating system cannot resolve is skipped rather than raised, because an
        unresolvable home is indistinguishable from having no key there.

        Returns:
            The path to an existing key file, or None when no key is available.
        """
        candidates: list[Path] = []
        override = os.environ.get("SOPS_AGE_KEY_FILE")
        if override:
            candidates.append(Path(override))

        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "sops" / "age" / "keys.txt")

        with contextlib.suppress(RuntimeError):
            candidates.append(Path.home() / ".config" / "sops" / "age" / "keys.txt")

        return next((candidate for candidate in candidates if candidate.is_file()), None)

    def run(self, command: Sequence[str]) -> CommandResult:
        """Run a command and report what it produced.

        Captures both streams and treats an unstartable process or a timeout as an ordinary
        failure, so the helper always reports rather than raises. The executable is resolved before
        running, so an absent tool is reported rather than raised out of the process layer.

        Arguments:
            command: Argument vector to execute.

        Returns:
            The outcome of the command.
        """
        executable = shutil.which(command[0])
        if executable is None:
            return CommandResult(ok=False, output="", error=f"{command[0]} not found on PATH")

        try:
            completed = subprocess.run(
                [executable, *command[1:]],
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
                check=False,
                cwd=self.root,
            )
        except OSError as error:
            return CommandResult(ok=False, output="", error=str(error))
        except subprocess.SubprocessError as error:
            return CommandResult(ok=False, output="", error=str(error))

        return CommandResult(
            ok=completed.returncode == 0,
            output=completed.stdout,
            error=completed.stderr.strip(),
        )


def missing_tools(environment: Environment) -> tuple[str, ...]:
    """List the encryption tools that are not installed.

    Checks both tools before doing anything, so the message names everything to install rather than
    surfacing them one failed run at a time.

    Arguments:
        environment: Host surface to query.

    Returns:
        The names of the absent tools, in the documented order.
    """
    return tuple(tool for tool in REQUIRED_TOOLS if environment.which(tool) is None)


def plaintext_path(root: Path, environment_name: str) -> Path:
    """Locate the plaintext file for an environment.

    Derives the path from the environment name, so the two files of a pair can never be mismatched
    by a caller passing one and inferring the other differently.

    Arguments:
        root: Repository root holding the environment files.
        environment_name: Environment whose file is wanted.

    Returns:
        Path to the plaintext environment file.
    """
    return root / ENVIRONMENT_FILES[environment_name]


def encrypted_path(root: Path, environment_name: str) -> Path:
    """Locate the encrypted file for an environment.

    Appends the committed suffix to the plaintext name, which is what keeps the pair adjacent and
    obvious in a directory listing.

    Arguments:
        root: Repository root holding the environment files.
        environment_name: Environment whose file is wanted.

    Returns:
        Path to the encrypted environment file.
    """
    return root / f"{ENVIRONMENT_FILES[environment_name]}.sops"


def newer_than(candidate: Path, other: Path) -> bool:
    """Decide whether one file was modified more recently than another.

    Treats an absent file as older, so a first decryption onto a machine that has no plaintext yet
    is never mistaken for an overwrite.

    Arguments:
        candidate: File whose recency is in question.
        other: File to compare against.

    Returns:
        True when the candidate exists and is strictly newer than the other.
    """
    if not candidate.is_file() or not other.is_file():
        return False

    return candidate.stat().st_mtime > other.stat().st_mtime


def encrypt(environment: Environment, source: Path) -> CommandResult:
    """Encrypt a plaintext environment file.

    Runs SOPS in dotenv mode so the encrypted file stays a key-and-value document, with the
    recipient taken from the committed configuration rather than the command line.

    Arguments:
        environment: Host surface to run the command through.
        source: Plaintext file to encrypt.

    Returns:
        The outcome of the encryption, with the ciphertext in its output.
    """
    return environment.run(
        [
            "sops",
            "--encrypt",
            "--input-type",
            "dotenv",
            "--output-type",
            "dotenv",
            str(source),
        ],
    )


def decrypt(environment: Environment, source: Path) -> CommandResult:
    """Decrypt an encrypted environment file.

    Runs SOPS in dotenv mode and returns the plaintext rather than writing it, so the caller
    decides whether the result may replace what is already on disk.

    Arguments:
        environment: Host surface to run the command through.
        source: Encrypted file to decrypt.

    Returns:
        The outcome of the decryption, with the plaintext in its output.
    """
    return environment.run(
        [
            "sops",
            "--decrypt",
            "--input-type",
            "dotenv",
            "--output-type",
            "dotenv",
            str(source),
        ],
    )


def write_atomically(path: Path, text: str) -> None:
    """Replace a file's contents without ever truncating it.

    Stages the new contents beside the destination and replaces it in one step, so an interrupted
    or failed write leaves the previous file intact rather than half-written.

    Arguments:
        path: File to replace.
        text: Contents to write.

    Returns:
        None.

    Raises:
        OSError: If the file could not be staged or replaced.
    """
    temporary = path.with_name(f"{path.name}.partial")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Requires both the mode and the environment, because neither has a safe default when the
    operation either publishes or replaces a file full of credentials.

    Returns:
        The configured argument parser.
    """
    parser = argparse.ArgumentParser(
        prog="sops_env",
        description="Encrypt or decrypt a per-environment file with SOPS and age.",
    )
    parser.add_argument("--mode", choices=(ENCRYPT, DECRYPT), required=True)
    parser.add_argument("--environment", choices=sorted(ENVIRONMENT_FILES), required=True)
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow a decryption to replace a newer plaintext file",
    )

    return parser


def run_encrypt(environment: Environment, root: Path, name: str) -> int:
    """Encrypt one environment file.

    Writes the ciphertext only when SOPS succeeds, so a failed run never leaves a truncated
    encrypted file that would look committable.

    Arguments:
        environment: Host surface to run the command through.
        root: Repository root holding the environment files.
        name: Environment to encrypt.

    Returns:
        Zero on success, or the documented failure code.
    """
    source = plaintext_path(root, name)
    if not source.is_file():
        print(f"nothing to encrypt: {source.name} does not exist")
        return EXIT_FAILED

    target = encrypted_path(root, name)
    result = encrypt(environment, source)
    if not result.ok:
        print(f"sops could not encrypt {source.name}; run sops directly to see why")
        return EXIT_FAILED

    try:
        write_atomically(target, result.output)
    except OSError as error:
        print(f"could not write {target.name}: {error}")
        return EXIT_FAILED

    print(f"encrypted {source.name} to {target.name}")

    return EXIT_OK


def run_decrypt(environment: Environment, root: Path, name: str, *, force: bool) -> int:
    """Decrypt one environment file.

    Refuses to replace a plaintext file that is newer than the ciphertext unless told to, because
    that plaintext usually holds a change nobody has encrypted yet.

    Arguments:
        environment: Host surface to run the command through.
        root: Repository root holding the environment files.
        name: Environment to decrypt.
        force: Whether to replace a newer plaintext file.

    Returns:
        Zero on success, or the documented failure code.
    """
    source = encrypted_path(root, name)
    if not source.is_file():
        print(f"nothing to decrypt: {source.name} does not exist")
        return EXIT_FAILED

    target = plaintext_path(root, name)
    if not force and newer_than(target, source):
        print(f"refusing to overwrite {target.name}, which is newer than {source.name}")
        return EXIT_WOULD_OVERWRITE

    if environment.age_key_file() is None:
        print("no age key found; generate one with age-keygen and point SOPS_AGE_KEY_FILE at it")
        return EXIT_NO_AGE_KEY

    result = decrypt(environment, source)
    if not result.ok:
        print(f"sops could not decrypt {source.name}; run sops directly to see why")
        return EXIT_FAILED

    try:
        write_atomically(target, result.output)
    except OSError as error:
        print(f"could not write {target.name}: {error}")
        return EXIT_FAILED

    print(f"decrypted {source.name} to {target.name}")

    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    root: Path = REPOSITORY_ROOT,
    environment: Environment | None = None,
) -> int:
    """Encrypt or decrypt a per-environment file.

    Checks the tooling before anything else, so a machine without SOPS or age is told what to
    install rather than shown a failure from a missing binary.

    Arguments:
        argv: Command-line arguments, or None to read them from the process.
        root: Repository root holding the environment files and the recipient configuration.
        environment: Host surface, or None to act on the repository at root.

    Returns:
        Zero on success, or the documented failure code.
    """
    arguments = build_parser().parse_args(argv)
    host = environment if environment is not None else HostEnvironment(root)

    absent = missing_tools(host)
    if absent:
        print(
            f"{', '.join(absent)} not found on PATH; install with "
            "winget install --id SecretsOPerationS.SOPS --exact and "
            "winget install --id FiloSottile.age --exact",
        )
        return EXIT_TOOLING_ABSENT

    config = root / SOPS_CONFIG_NAME
    if not config.is_file():
        print(f"no {SOPS_CONFIG_NAME}; the age recipient is read from it")
        return EXIT_FAILED

    if arguments.mode == ENCRYPT:
        return run_encrypt(host, root, arguments.environment)

    return run_decrypt(host, root, arguments.environment, force=arguments.force)


if __name__ == "__main__":
    raise SystemExit(main())
