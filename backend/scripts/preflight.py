"""Prerequisite gate for the local backend platform build.

Probes every requirement listed in docs/build/prerequisites.md and renders a pass, warn, or fail
report, so one command answers whether this machine can build the platform and what to install when
it cannot.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
ACTIVE_PREFIX = Path(sys.prefix)
BASE_PREFIX = Path(sys.base_prefix)
USER_HOME = Path.home()
COMMAND_TIMEOUT_SECONDS = 30
GIBIBYTE = 1024**3
MINIMUM_CPU_COUNT = 4
MINIMUM_MEMORY_GIB = 8.0
MINIMUM_FREE_DISK_GIB = 20.0
DOCKER_RESOURCE_FIELD_COUNT = 2
REPORTED_STORAGE_TYPES = ("Images", "Local Volumes")
REQUIRED_NOW = "Required before the build starts."
SECRETS_TOOLING_NOTE = "Needed before the first commit of an encrypted env file."


class Status(StrEnum):
    """Outcome of a single prerequisite check.

    Separates a required failure, which blocks the build, from an informational optional shortfall
    that names what is absent and when it would start to matter. Inherits ``StrEnum`` for display.

    Attributes:
        OK: The requirement is satisfied, displayed as PASS.
        INFO: An optional requirement is unsatisfied and does not block the build.
        FAIL: A required requirement is unsatisfied and blocks the build.
    """

    OK = "PASS"
    INFO = "INFO"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class Requirement:
    """Identity and guidance for one row of the prerequisite checklist.

    Holds everything a row needs regardless of how it is measured, so the rows graded by version
    and the rows graded by quantity share one shape. Inherits nothing; it is a plain frozen data
    holder with no methods.

    Attributes:
        number: Row number in docs/build/prerequisites.md Section 1.
        title: Human-readable name of the requirement.
        minimum_label: Displayed form of the minimum acceptable version or quantity.
        required: Whether an unsatisfied row blocks the build.
        remediation: Command or action that resolves an unsatisfied row.
        matters_when: Point at which an unsatisfied optional row begins to block work.
    """

    number: int
    title: str
    minimum_label: str
    required: bool
    remediation: str
    matters_when: str


@dataclass(frozen=True, slots=True)
class ToolCheck:
    """Specification of a prerequisite row graded by version.

    Pairs a checklist row with the command whose output carries its version and the floor that
    version must reach. Inherits nothing; it is a plain frozen data holder with no methods.

    Attributes:
        requirement: Checklist row this check grades.
        command: Argument vector whose output carries the version.
        minimum: Lowest acceptable version, as a comparable tuple.
        outside_virtualenv: Whether to resolve the command against a search path with the project
            virtualenv removed.
    """

    requirement: Requirement
    command: tuple[str, ...]
    minimum: tuple[int, ...]
    outside_virtualenv: bool = False


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Evaluated outcome of one prerequisite row.

    Carries everything both the report and the machine-readable output need, so rendering never has
    to re-derive a verdict or look guidance up elsewhere. Inherits nothing; it is a plain frozen
    data holder with no methods.

    Attributes:
        number: Row number in docs/build/prerequisites.md Section 1.
        title: Human-readable name of the requirement.
        status: Verdict for this row.
        minimum: Minimum acceptable version or quantity, as displayed.
        observed: What this machine actually reports.
        remediation: Command or action that resolves an unsatisfied row.
        matters_when: Point at which an unsatisfied optional row begins to block work.
    """

    number: int
    title: str
    status: Status
    minimum: str
    observed: str
    remediation: str
    matters_when: str


class SystemProbes(Protocol):
    """Machine inspection surface used by the prerequisite checks.

    Isolates every interaction with the host behind four operations, so the checks can be evaluated
    against a fabricated machine without running a subprocess or contacting a daemon. Inherits
    Protocol, so any object providing these four methods satisfies it structurally.

    Members:
        capture: Run a command on the ambient search path and return its output.
        capture_outside_virtualenv: Run a command with the virtualenv removed.
        docker_resources: Report the processors and memory the container engine offers.
        free_disk_bytes: Report free space on the volume holding a path.
        volume_identity: Report which volume a path lives on.
    """

    def capture(self, command: Sequence[str]) -> str | None:
        """Run a command and return its output.

        Executes the given argument vector on the ambient search path, treating an absent
        executable, a non-zero exit, and a timeout alike as no output.

        Arguments:
            command: Argument vector to execute.

        Returns:
            The captured output with surrounding whitespace removed, or None when the command could
            not be run successfully.
        """

    def capture_outside_virtualenv(self, command: Sequence[str]) -> str | None:
        """Run a command resolved without the project virtualenv.

        Executes the given argument vector against a search path with the active virtualenv removed,
        which is what lets the host interpreter be inspected from inside that virtualenv.

        Arguments:
            command: Argument vector to execute.

        Returns:
            The captured output with surrounding whitespace removed, or None when the command could
            not be run successfully.
        """

    def docker_resources(self) -> tuple[int, int] | None:
        """Report the processors and memory the container engine offers.

        Queries the container engine for the processor count and total memory it makes available to
        containers, which is what the platform is actually limited by.

        Arguments:
            None.

        Returns:
            A pair of processor count and total memory in bytes, or None when the engine could not
            be queried.
        """

    def free_disk_bytes(self, path: Path) -> int | None:
        """Report free space on the volume holding a path.

        Measures whichever volume the given path lives on, so the repository volume and the
        container engine's data volume can each be graded.

        Arguments:
            path: Any path on the volume of interest.

        Returns:
            Free space in bytes, or None when the volume could not be measured.
        """

    def volume_identity(self, path: Path) -> str | None:
        """Report which volume a path lives on.

        Identifies the underlying volume rather than the path, so two paths that share storage are
        recognised as one volume on every platform rather than only where drive letters differ.

        Arguments:
            path: Path whose volume should be identified.

        Returns:
            An opaque identifier stable across paths on one volume, or None when it is unavailable.
        """


class RealProbes:
    """Machine inspection backed by real subprocesses and the filesystem.

    Implements the SystemProbes surface against this host, keeping every failure mode of running an
    external command collapsed into a single absent-result value. Inherits nothing; it satisfies
    SystemProbes structurally.

    Members:
        run: Resolve and run a command on a given search path.
        capture: Run a command on the ambient search path and return its output.
        capture_outside_virtualenv: Run a command with the virtualenv removed.
        docker_resources: Report the processors and memory the container engine offers.
        free_disk_bytes: Report free space on the volume holding a path.
        volume_identity: Report which volume a path lives on.
    """

    def run(self, command: Sequence[str], search_path: str | None) -> str | None:
        """Resolve and run a command, returning whatever it printed.

        Resolves the executable before running it so an absent tool is reported rather than raised,
        and prefers standard output while falling back to standard error for tools that report
        their version there.

        Arguments:
            command: Argument vector to execute.
            search_path: Search path used to resolve the executable, or None for the ambient one.

        Returns:
            The captured output with surrounding whitespace removed, or None when the executable is
            absent, exits non-zero, times out, or cannot be started.
        """
        executable = shutil.which(command[0], path=search_path)
        if executable is None:
            return None

        try:
            completed = subprocess.run(
                [executable, *command[1:]],
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except OSError:
            return None
        except subprocess.SubprocessError:
            return None

        if completed.returncode != 0:
            return None

        standard_output = completed.stdout.strip()
        if standard_output:
            return standard_output

        return completed.stderr.strip()

    def capture(self, command: Sequence[str]) -> str | None:
        """Run a command and return its output.

        Resolves the command on the ambient search path, which is the right answer for every tool
        whose location the developer's own environment decides.

        Arguments:
            command: Argument vector to execute.

        Returns:
            The captured output, or None when the command could not be run successfully.
        """
        return self.run(command, None)

    def capture_outside_virtualenv(self, command: Sequence[str]) -> str | None:
        """Run a command resolved without the project virtualenv.

        Strips the active virtualenv from the search path first, so the host interpreter is
        inspected even though this script is itself running inside that virtualenv.

        Arguments:
            command: Argument vector to execute.

        Returns:
            The captured output, or None when the command could not be run successfully.
        """
        return self.run(command, host_search_path())

    def docker_resources(self) -> tuple[int, int] | None:
        """Report the processors and memory the container engine offers.

        Asks the container engine for its processor count and total memory in one formatted query,
        and rejects any answer that does not parse as two integers.

        Arguments:
            None.

        Returns:
            A pair of processor count and total memory in bytes, or None when the engine could not
            be queried or answered unexpectedly.
        """
        output = self.capture(("docker", "info", "--format", "{{.NCPU}} {{.MemTotal}}"))
        if output is None:
            return None

        fields = output.split()
        if len(fields) != DOCKER_RESOURCE_FIELD_COUNT:
            return None

        try:
            return (int(fields[0]), int(fields[1]))
        except ValueError:
            return None

    def free_disk_bytes(self, path: Path) -> int | None:
        """Report free space on the volume holding a path.

        Measures the volume through the standard library rather than the container engine, so the
        answer is available even when the engine is not running.

        Arguments:
            path: Any path on the volume of interest.

        Returns:
            Free space in bytes, or None when the volume could not be measured.
        """
        try:
            return shutil.disk_usage(path).free
        except OSError:
            return None

    def volume_identity(self, path: Path) -> str | None:
        """Report which volume a path lives on.

        Reads the device identifier the operating system records for the path, which is the volume
        serial number on Windows and the device number on POSIX.

        Arguments:
            path: Path whose volume should be identified.

        Returns:
            The device identifier as text, or None when the path cannot be inspected.
        """
        try:
            return str(path.stat().st_dev)
        except OSError:
            return None


REAL_PROBES = RealProbes()

TOOL_CHECKS: tuple[ToolCheck, ...] = (
    ToolCheck(
        requirement=Requirement(
            number=1,
            title="Docker Engine",
            minimum_label="27.0",
            required=True,
            remediation="winget install --id Docker.DockerDesktop --exact",
            matters_when=REQUIRED_NOW,
        ),
        command=("docker", "--version"),
        minimum=(27, 0),
    ),
    ToolCheck(
        requirement=Requirement(
            number=2,
            title="Docker Compose",
            minimum_label="2.24",
            required=True,
            remediation="winget install --id Docker.DockerDesktop --exact",
            matters_when=REQUIRED_NOW,
        ),
        command=("docker", "compose", "version"),
        minimum=(2, 24),
    ),
    ToolCheck(
        requirement=Requirement(
            number=3,
            title="Git",
            minimum_label="2.40",
            required=True,
            remediation="winget install --id Git.Git --exact",
            matters_when=REQUIRED_NOW,
        ),
        command=("git", "--version"),
        minimum=(2, 40),
    ),
    ToolCheck(
        requirement=Requirement(
            number=4,
            title="Python on PATH",
            minimum_label="3.14",
            required=False,
            remediation="No install needed; run every project command through uv run",
            matters_when="Not needed: uv run resolves the pinned interpreter regardless of PATH.",
        ),
        command=("python", "--version"),
        minimum=(3, 14),
        outside_virtualenv=True,
    ),
    ToolCheck(
        requirement=Requirement(
            number=6,
            title="uv",
            minimum_label="0.5",
            required=True,
            remediation="pipx install uv",
            matters_when=REQUIRED_NOW,
        ),
        command=("uv", "--version"),
        minimum=(0, 5),
    ),
    ToolCheck(
        requirement=Requirement(
            number=9,
            title="kubectl",
            minimum_label="1.30",
            required=True,
            remediation="winget install --id Kubernetes.kubectl --exact",
            matters_when=REQUIRED_NOW,
        ),
        command=("kubectl", "version", "--client"),
        minimum=(1, 30),
    ),
    ToolCheck(
        requirement=Requirement(
            number=11,
            title="SOPS",
            minimum_label="3.13",
            required=False,
            remediation="winget install --id SecretsOPerationS.SOPS --exact",
            matters_when=SECRETS_TOOLING_NOTE,
        ),
        command=("sops", "--version"),
        minimum=(3, 13),
    ),
    ToolCheck(
        requirement=Requirement(
            number=12,
            title="age",
            minimum_label="1.3",
            required=False,
            remediation="winget install --id FiloSottile.age --exact",
            matters_when=SECRETS_TOOLING_NOTE,
        ),
        command=("age", "--version"),
        minimum=(1, 3),
    ),
    ToolCheck(
        requirement=Requirement(
            number=13,
            title="psql on the host",
            minimum_label="16",
            required=False,
            remediation="winget install --id PostgreSQL.PostgreSQL --exact",
            matters_when="Not needed: every documented command reaches PostgreSQL by docker exec.",
        ),
        command=("psql", "--version"),
        minimum=(16,),
    ),
)

INTERPRETER_REQUIREMENT = Requirement(
    number=5,
    title="Project virtualenv interpreter",
    minimum_label="3.14",
    required=True,
    remediation="uv venv --python 3.14 backend/.venv",
    matters_when=REQUIRED_NOW,
)

RESOURCE_REQUIREMENT = Requirement(
    number=7,
    title="Host resources",
    minimum_label=f"{MINIMUM_CPU_COUNT} CPU, {MINIMUM_MEMORY_GIB:.0f} GiB",
    required=True,
    remediation="Raise the CPU and memory limits in Docker Desktop Settings, Resources",
    matters_when=REQUIRED_NOW,
)

DISK_REQUIREMENT = Requirement(
    number=8,
    title="Free disk space",
    minimum_label=f"{MINIMUM_FREE_DISK_GIB:.0f} GiB",
    required=True,
    remediation="Free space on the reported volume, or move the Docker data root",
    matters_when=REQUIRED_NOW,
)

CLUSTER_TOOL_CHECKS: tuple[tuple[str, tuple[str, ...], tuple[int, ...]], ...] = (
    ("kind", ("kind", "version"), (0, 33)),
    ("minikube", ("minikube", "version"), (1, 39)),
)

CLUSTER_REQUIREMENT = Requirement(
    number=10,
    title="kind or minikube",
    minimum_label=" or ".join(
        f"{name} {'.'.join(str(part) for part in minimum)}"
        for name, _, minimum in CLUSTER_TOOL_CHECKS
    ),
    required=False,
    remediation="winget install --id Kubernetes.kind --exact",
    matters_when="Not needed: Kubernetes is reasoning-only for this platform.",
)


def parse_version(text: str) -> tuple[int, ...] | None:
    """Extract a comparable version from command output.

    Reads the first dotted numeric run in the text, which covers every prerequisite tool whether it
    prefixes its version with a name, a v, or nothing at all.

    Arguments:
        text: Raw output captured from a version command.

    Returns:
        The version as a tuple of integers, or None when the text carries no version.
    """
    match = re.search(r"(\d+(?:\.\d+)*)", text)
    if match is None:
        return None

    return tuple(int(part) for part in match.group(1).split("."))


def format_version(version: tuple[int, ...]) -> str:
    """Render a parsed version for display.

    Rejoins the numeric components so the report shows the version in the form a developer would
    recognise from the tool's own output.

    Arguments:
        version: Version components in order.

    Returns:
        The dotted string form of the version.
    """
    return ".".join(str(part) for part in version)


def within_any(entry: str, roots: Iterable[Path]) -> bool:
    """Decide whether a search path entry lives under one of several roots.

    Resolves the entry before comparing, so an entry reached through a relative path or a link is
    still recognised, and treats an unresolvable entry as unrelated.

    Arguments:
        entry: Single search path entry.
        roots: Directories the entry might live under.

    Returns:
        True when the entry is one of the roots or sits inside one, and False otherwise.
    """
    try:
        resolved = Path(entry).resolve()
    except OSError:
        return False

    return any(resolved == root or root in resolved.parents for root in roots)


def virtualenv_roots(prefix: Path, base_prefix: Path) -> list[Path]:
    """List the directories that belong to a virtualenv rather than to the host.

    Always includes the project's own virtualenv, and includes the running prefix only when that
    prefix is itself a virtualenv, so a script executed by a base interpreter does not hide that
    interpreter from its own report.

    Arguments:
        prefix: Prefix of the running interpreter.
        base_prefix: Prefix of the interpreter the running one was created from.

    Returns:
        The directories to exclude from the host search path.
    """
    roots = [(REPOSITORY_ROOT / "backend" / ".venv").resolve()]
    if prefix != base_prefix:
        roots.append(prefix.resolve())

    return roots


def host_search_path(prefix: Path = ACTIVE_PREFIX, base_prefix: Path = BASE_PREFIX) -> str:
    """Build a search path with the project virtualenv removed.

    Drops every entry belonging to an active virtualenv or to the repository's own, which is what
    stops uv run from answering the question about the interpreter on the host search path.

    Arguments:
        prefix: Prefix of the running interpreter.
        base_prefix: Prefix of the interpreter the running one was created from.

    Returns:
        The remaining search path entries joined in their original order.
    """
    roots = virtualenv_roots(prefix, base_prefix)
    entries = [
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and not within_any(entry, roots)
    ]

    return os.pathsep.join(entries)


def satisfied(requirement: Requirement, observed: str) -> CheckResult:
    """Build a passing result for a checklist row.

    Records the observed value and drops the remediation, since nothing needs doing when the row is
    already satisfied.

    Arguments:
        requirement: Checklist row the result belongs to.
        observed: Value this machine reports.

    Returns:
        A passing result for the row.
    """
    return CheckResult(
        number=requirement.number,
        title=requirement.title,
        status=Status.OK,
        minimum=requirement.minimum_label,
        observed=observed,
        remediation="",
        matters_when=requirement.matters_when,
    )


def unsatisfied(requirement: Requirement, observed: str) -> CheckResult:
    """Build a failing or informational result for a checklist row.

    Grades the shortfall by whether the row is required, so an optional tool never turns into a
    build warning or blocker while still being reported.

    Arguments:
        requirement: Checklist row the result belongs to.
        observed: What this machine reports in place of an acceptable value.

    Returns:
        A failing result for a required row, or an informational result for an optional one.
    """
    return CheckResult(
        number=requirement.number,
        title=requirement.title,
        status=Status.FAIL if requirement.required else Status.INFO,
        minimum=requirement.minimum_label,
        observed=observed,
        remediation=requirement.remediation,
        matters_when=requirement.matters_when,
    )


def evaluate_tool(probes: SystemProbes, check: ToolCheck) -> CheckResult:
    """Evaluate one checklist row graded by version.

    Runs the check's command on the search path the row calls for and compares the version it
    reports against the floor, treating an absent tool and unrecognisable output as distinct
    observations so the report explains itself.

    Arguments:
        probes: Machine inspection surface to query.
        check: Specification to evaluate.

    Returns:
        The evaluated result for the row.
    """
    output = (
        probes.capture_outside_virtualenv(check.command)
        if check.outside_virtualenv
        else probes.capture(check.command)
    )
    if output is None:
        return unsatisfied(check.requirement, "not present")

    version = parse_version(output)
    if version is None:
        return unsatisfied(check.requirement, f"unrecognised output: {output}")

    if version < check.minimum:
        return unsatisfied(check.requirement, format_version(version))

    return satisfied(check.requirement, format_version(version))


def project_interpreter(root: Path) -> Path:
    """Locate the interpreter inside the project virtualenv.

    Prefers whichever platform layout actually exists so the check never depends on the interpreter
    that happens to be first on the search path, and falls back to the Windows layout for the error
    message when the virtualenv is absent entirely.

    Arguments:
        root: Repository root holding the backend project.

    Returns:
        Path to the virtualenv interpreter.
    """
    candidates = (
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]


def evaluate_project_interpreter(probes: SystemProbes, root: Path) -> CheckResult:
    """Evaluate the interpreter inside the project virtualenv.

    Builds a version check whose command is the resolved virtualenv interpreter rather than the
    name python, so the answer cannot come from whatever interpreter the search path resolves first.

    Arguments:
        probes: Machine inspection surface to query.
        root: Repository root holding the .venv directory.

    Returns:
        The evaluated result for the virtualenv interpreter.
    """
    interpreter = project_interpreter(root / "backend")
    check = ToolCheck(
        requirement=INTERPRETER_REQUIREMENT,
        command=(str(interpreter), "--version"),
        minimum=(3, 14),
    )

    return evaluate_tool(probes, check)


def evaluate_host_resources(probes: SystemProbes) -> CheckResult:
    """Evaluate the processors and memory available to containers.

    Reads both quantities from the container engine in one query, because the limit that matters is
    what the engine offers rather than what the host physically has.

    Arguments:
        probes: Machine inspection surface to query.

    Returns:
        The evaluated result for host resources.
    """
    resources = probes.docker_resources()
    if resources is None:
        return unsatisfied(RESOURCE_REQUIREMENT, "container engine unavailable")

    cpu_count, memory_bytes = resources
    memory_gib = memory_bytes / GIBIBYTE
    observed = f"{cpu_count} CPU, {memory_gib:.1f} GiB"
    if cpu_count < MINIMUM_CPU_COUNT or memory_gib < MINIMUM_MEMORY_GIB:
        return unsatisfied(RESOURCE_REQUIREMENT, observed)

    return satisfied(RESOURCE_REQUIREMENT, observed)


def docker_data_volume(probes: SystemProbes, fallback: Path) -> Path:
    """Locate a host path on the volume holding the container engine's data.

    Asks the engine where its data root is and uses it when the host can measure that path, which
    is the case for a native engine; a Docker Desktop data root lives inside a virtual machine and
    is unmeasurable from the host, so the fallback stands in for it.

    Arguments:
        probes: Machine inspection surface to query.
        fallback: Host path to grade when the engine's own data root is unusable.

    Returns:
        A host path on the volume holding the engine's data.
    """
    output = probes.capture(("docker", "info", "--format", "{{.DockerRootDir}}"))
    if output is None:
        return fallback

    candidate = Path(output)
    if probes.free_disk_bytes(candidate) is None:
        return fallback

    return candidate


def docker_usage(probes: SystemProbes) -> str | None:
    """Summarise what the container engine already holds.

    Reads the engine's own accounting of the storage the checklist names, images and local volumes,
    which is the figure row 8 reports alongside free space.

    Arguments:
        probes: Machine inspection surface to query.

    Returns:
        A one-line summary of engine storage, or None when the engine could not be queried.
    """
    output = probes.capture(("docker", "system", "df", "--format", "{{.Type}} {{.Size}}"))
    if output is None:
        return None

    entries = [
        line.strip()
        for line in output.splitlines()
        if line.strip().startswith(REPORTED_STORAGE_TYPES)
    ]
    if not entries:
        return None

    return ", ".join(entries)


def volumes_to_grade(probes: SystemProbes, paths: Iterable[Path]) -> dict[str, Path]:
    """Select the distinct volumes the build consumes space on.

    Keys each path by the volume it lives on, so a machine keeping the repository and the container
    engine data on one volume is measured once rather than twice, on any platform.

    Arguments:
        probes: Machine inspection surface to query.
        paths: Paths whose volumes should be graded.

    Returns:
        One representative path per distinct volume, keyed by volume identity.
    """
    selected: dict[str, Path] = {}
    for path in paths:
        selected.setdefault(probes.volume_identity(path) or str(path), path)

    return selected


def evaluate_free_disk(probes: SystemProbes, root: Path, docker_data_root: Path) -> CheckResult:
    """Evaluate free space on every volume the build consumes.

    Grades the repository volume and the container engine data volume together, because image
    layers, named volumes, and build output can each stop the build when short on space. Reports
    what the engine already holds alongside the free figures, as the checklist does.

    Arguments:
        probes: Machine inspection surface to query.
        root: Repository root.
        docker_data_root: Host path on the volume holding the container engine's data.

    Returns:
        The evaluated result for free disk space.
    """
    measurements: list[tuple[str, float]] = []
    for path in volumes_to_grade(probes, (root, docker_data_root)).values():
        free_bytes = probes.free_disk_bytes(path)
        label = path.anchor or str(path)
        if free_bytes is None:
            return unsatisfied(DISK_REQUIREMENT, f"{label} unavailable")

        measurements.append((label, free_bytes / GIBIBYTE))

    observed = ", ".join(f"{label} {free_gib:.1f} GiB" for label, free_gib in measurements)
    usage = docker_usage(probes)
    if usage is not None:
        observed = f"{observed}; engine holds {usage}"

    if min(free_gib for _, free_gib in measurements) < MINIMUM_FREE_DISK_GIB:
        return unsatisfied(DISK_REQUIREMENT, observed)

    return satisfied(DISK_REQUIREMENT, observed)


def evaluate_cluster_tooling(probes: SystemProbes) -> CheckResult:
    """Evaluate the optional local Kubernetes tooling.

    Accepts either cluster tool, since the requirement is satisfied by whichever one is present,
    and reports what each tool actually offers so an upgrade is distinguishable from an install.

    Arguments:
        probes: Machine inspection surface to query.

    Returns:
        The evaluated result for local cluster tooling.
    """
    observations: list[str] = []
    for name, command, minimum in CLUSTER_TOOL_CHECKS:
        output = probes.capture(command)
        version = None if output is None else parse_version(output)
        if version is None:
            observations.append(f"{name} not present")
            continue

        if version >= minimum:
            return satisfied(CLUSTER_REQUIREMENT, f"{name} {format_version(version)}")

        observations.append(f"{name} {format_version(version)}")

    return unsatisfied(CLUSTER_REQUIREMENT, ", ".join(observations))


def build_report(
    probes: SystemProbes,
    root: Path = REPOSITORY_ROOT,
    docker_data_root: Path = USER_HOME,
) -> tuple[CheckResult, ...]:
    """Evaluate every prerequisite row.

    Runs the version-graded rows together with the four that need their own logic, then orders the
    results by row number so the output matches docs/build/prerequisites.md.

    Arguments:
        probes: Machine inspection surface to query.
        root: Repository root, used for the virtualenv and disk rows.
        docker_data_root: Host path graded when the container engine's own data root cannot be
            measured from this host.

    Returns:
        Every evaluated result, ordered by row number.
    """
    results = [evaluate_tool(probes, check) for check in TOOL_CHECKS]
    results.append(evaluate_project_interpreter(probes, root))
    results.append(evaluate_host_resources(probes))
    results.append(evaluate_free_disk(probes, root, docker_data_volume(probes, docker_data_root)))
    results.append(evaluate_cluster_tooling(probes))

    return tuple(sorted(results, key=lambda result: result.number))


def note_for(result: CheckResult) -> str:
    """Compose the guidance line for one result.

    Returns nothing for a satisfied row, the remediation alone for a blocking failure, and both the
    remediation and the point at which it starts to matter for an optional shortfall.

    Arguments:
        result: Result to describe.

    Returns:
        The guidance text for the result, which is empty when nothing needs doing.
    """
    if result.status is Status.OK:
        return ""

    if result.status is Status.INFO:
        return f"{result.matters_when} Remediation: {result.remediation}"

    return f"Remediation: {result.remediation}"


def summary_line(results: Sequence[CheckResult]) -> str:
    """Summarise the report in one line.

    States the overall verdict together with the counts behind it, so the outcome is legible
    without reading every row.

    Arguments:
        results: Evaluated results to summarise.

    Returns:
        A single-line summary of the report.
    """
    failed = sum(1 for result in results if result.status is Status.FAIL)
    informational = sum(1 for result in results if result.status is Status.INFO)
    if failed:
        return f"FAIL: {failed} required check(s) failed, {informational} informational item(s)."

    return f"PASS: every required check passed, {informational} informational item(s)."


def render_report(results: Sequence[CheckResult]) -> str:
    """Render the report as an aligned table with guidance beneath it.

    Sizes every column to its widest cell so the table stays readable at a normal terminal width,
    and lists the remediation for each unsatisfied row separately rather than in a column that
    would force the table to wrap.

    Arguments:
        results: Evaluated results to render.

    Returns:
        The rendered report, including its summary and any guidance.
    """
    headers = ("#", "Requirement", "Status", "Minimum", "Observed")
    rows = [headers]
    rows.extend(
        (
            str(result.number),
            result.title,
            str(result.status),
            result.minimum,
            result.observed,
        )
        for result in results
    )

    widths = [max(len(row[column]) for row in rows) for column in range(len(headers))]
    lines = [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in rows
    ]
    lines.insert(1, "  ".join("-" * width for width in widths))
    lines.extend(("", summary_line(results)))

    guidance = [result for result in results if result.status is not Status.OK]
    if guidance:
        lines.extend(("", "Guidance:"))
        lines.extend(
            f"  {result.number}. {result.title}: {note_for(result)}" for result in guidance
        )

    return "\n".join(lines)


def render_json(results: Sequence[CheckResult]) -> str:
    """Render the report as machine-readable output.

    Emits the same verdicts, observations, and guidance the table carries, so a caller can consume
    the result without parsing aligned columns.

    Arguments:
        results: Evaluated results to render.

    Returns:
        The report as an indented JSON document.
    """
    payload = {
        "ok": exit_code(results) == 0,
        "exit_code": exit_code(results),
        "summary": summary_line(results),
        "checks": [
            {
                "number": result.number,
                "requirement": result.title,
                "status": str(result.status),
                "minimum": result.minimum,
                "observed": result.observed,
                "remediation": result.remediation,
                "matters_when": result.matters_when,
                "note": note_for(result),
            }
            for result in results
        ],
    }

    return json.dumps(payload, indent=2)


def exit_code(results: Sequence[CheckResult]) -> int:
    """Derive the process exit code from the report.

    Treats only a required failure as blocking, so an optional shortfall is reported without
    stopping a build that can legitimately proceed.

    Arguments:
        results: Evaluated results to grade.

    Returns:
        Zero when every required check passed, and one otherwise.
    """
    return 1 if any(result.status is Status.FAIL for result in results) else 0


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Exposes the single output-format switch, keeping the default human-readable so the script is
    useful without arguments.

    Arguments:
        None.

    Returns:
        The configured argument parser.
    """
    parser = argparse.ArgumentParser(
        prog="preflight",
        description="Check that this machine can build the local backend platform.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the report as JSON instead of a table",
    )

    return parser


def main(argv: Sequence[str] | None = None, probes: SystemProbes = REAL_PROBES) -> int:
    """Run the prerequisite gate.

    Evaluates every requirement, prints the report in the requested format, and returns the exit
    code that reflects whether anything required is missing.

    Arguments:
        argv: Command-line arguments, or None to read them from the process.
        probes: Machine inspection surface, replaced in tests with a fabricated machine.

    Returns:
        Zero when every required check passed, and one otherwise.
    """
    arguments = build_parser().parse_args(argv)
    results = build_report(probes)
    print(render_json(results) if arguments.json else render_report(results))

    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
