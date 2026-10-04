"""Unit tests for the prerequisite gate script.

Covers the verdict each prerequisite row reaches, the binding between the script and the checklist
document, both output formats, and the exit-code contract, using a fabricated machine so no test
depends on what happens to be installed.
"""

import importlib
import inspect
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Sequence, get_type_hints  # noqa: UP035
from unittest.mock import patch

import pytest

from scripts import preflight

PASSING_OUTPUTS: dict[tuple[str, ...], str] = {
    ("docker", "--version"): "Docker version 29.7.2, build a7dcaa6",
    ("docker", "compose", "version"): "Docker Compose version v5.5.1",
    ("git", "--version"): "git version 2.53.0.windows.4",
    ("uv", "--version"): "uv 0.12.1 (329541a50 2026-07-31)",
    ("kubectl", "version", "--client"): "Client Version: v1.37.0",
    ("sops", "--version"): "sops 3.13.3",
    ("age", "--version"): "v1.3.2",
    ("psql", "--version"): "psql (PostgreSQL) 16.4",
    ("kind", "version"): "kind v0.33.0 go1.25.1 windows/amd64",
}

HOST_PYTHON_COMMAND = ("python", "--version")
PASSING_CPU_COUNT = 16
PASSING_MEMORY_BYTES = 32 * preflight.GIBIBYTE
PASSING_FREE_BYTES = 500 * preflight.GIBIBYTE
EXPECTED_ROW_NUMBERS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13)
CHECKLIST_DOCUMENT = preflight.REPOSITORY_ROOT / "docs" / "build" / "prerequisites.md"
REPOSITORY_VOLUME = preflight.REPOSITORY_ROOT
OTHER_VOLUME = preflight.REPOSITORY_ROOT / "another-volume"
SCRIPT_PATHS = tuple(sorted(Path(preflight.__file__).parent.glob("*.py")))
OLDEST_SUPPORTED_MINOR = 12


def script_module(path: Path) -> ModuleType:
    """Import a script by its path.

    Maps a file in the scripts directory onto its importable module name, so a test can be
    parametrised over the directory rather than naming each script.

    Arguments:
        path: Script file to import.

    Returns:
        The imported module.
    """
    name = "scripts" if path.stem == "__init__" else f"scripts.{path.stem}"

    return importlib.import_module(name)


@dataclass
class FakeProbes:
    """Fabricated machine used in place of real host inspection.

    Implements the probe surface the prerequisite checks consume, so a passing and a failing machine
    can both be described as data rather than simulated with subprocesses. Inherits nothing; it
    satisfies the SystemProbes protocol structurally.

    Attributes:
        outputs: Ambient-search-path command output keyed by argument vector.
        host_outputs: Output for commands resolved with the project virtualenv removed.
        resources: Processor count and memory in bytes, or None for an unavailable engine.
        free_bytes: Free space in bytes keyed by path, defaulting to the fallback.
        fallback_free_bytes: Free space reported for any path not named in free_bytes.
        volumes: Volume identity keyed by path; an unlisted path reports its own string form.

    Members:
        capture: Return fabricated ambient command output.
        capture_outside_virtualenv: Return fabricated host command output.
        docker_resources: Return fabricated container engine resources.
        free_disk_bytes: Return fabricated free space for a volume.
        volume_identity: Return fabricated volume identity for a path.
    """

    outputs: dict[tuple[str, ...], str] = field(default_factory=dict)
    host_outputs: dict[tuple[str, ...], str] = field(default_factory=dict)
    resources: tuple[int, int] | None = None
    free_bytes: dict[Path, int | None] = field(default_factory=dict)
    fallback_free_bytes: int | None = None
    volumes: dict[Path, str] = field(default_factory=dict)

    def capture(self, command: Sequence[str]) -> str | None:
        """Return the fabricated output for a command.

        Looks the argument vector up in the ambient mapping, treating an unlisted command as a tool
        that is not installed on this machine.

        Arguments:
            command: Argument vector that would have been executed.

        Returns:
            The configured output, or None when the command is not configured.
        """
        return self.outputs.get(tuple(command))

    def capture_outside_virtualenv(self, command: Sequence[str]) -> str | None:
        """Return the fabricated output for a command resolved off the host search path.

        Reads a separate mapping from the ambient one, which is what lets a test describe a machine
        whose virtualenv interpreter and host interpreter differ.

        Arguments:
            command: Argument vector that would have been executed.

        Returns:
            The configured output, or None when the command is not configured.
        """
        return self.host_outputs.get(tuple(command))

    def docker_resources(self) -> tuple[int, int] | None:
        """Return the fabricated container engine resources.

        Reports whatever the fixture was configured with, including the absent case that stands in
        for a container engine that is not running.

        Arguments:
            None.

        Returns:
            The configured processor count and memory in bytes, or None.
        """
        return self.resources

    def free_disk_bytes(self, path: Path) -> int | None:
        """Return the fabricated free space for a volume.

        Prefers a per-path figure so a test can starve one volume while leaving the other ample,
        and falls back to a single figure for the common case.

        Arguments:
            path: Path whose volume would have been measured.

        Returns:
            The configured free space in bytes, or None.
        """
        if path in self.free_bytes:
            return self.free_bytes[path]

        return self.fallback_free_bytes

    def volume_identity(self, path: Path) -> str | None:
        """Return the fabricated volume identity for a path.

        Lets a test place two paths on one volume or on separate volumes, which is what the
        deduplication in the disk row is graded on.

        Arguments:
            path: Path whose volume would have been identified.

        Returns:
            The configured identity, or the path's own string form when none is configured.
        """
        return self.volumes.get(path, str(path))


def passing_probes() -> FakeProbes:
    """Build a machine on which every requirement is satisfied.

    Supplies a version at or above every floor, ample resources on every volume, and the optional
    tooling, so the resulting report contains no informational item and no failure.

    Arguments:
        None.

    Returns:
        A probe surface describing a fully satisfactory machine.
    """
    interpreter = preflight.project_interpreter(preflight.REPOSITORY_ROOT / "backend")

    return FakeProbes(
        outputs={**PASSING_OUTPUTS, (str(interpreter), "--version"): "Python 3.14.6"},
        host_outputs={HOST_PYTHON_COMMAND: "Python 3.14.6"},
        resources=(PASSING_CPU_COUNT, PASSING_MEMORY_BYTES),
        fallback_free_bytes=PASSING_FREE_BYTES,
    )


def informational_probes() -> FakeProbes:
    """Build a machine that satisfies every required row and no optional one.

    Removes the host interpreter, the cluster tooling, the encryption tooling, and the database
    client, which is the state this machine is actually in before the secrets work begins.

    Arguments:
        None.

    Returns:
        A probe surface describing a machine that can build with optional informational items.
    """
    probes = passing_probes()
    for command in (("sops", "--version"), ("age", "--version"), ("psql", "--version")):
        del probes.outputs[command]

    del probes.outputs[("kind", "version")]
    probes.host_outputs = {HOST_PYTHON_COMMAND: "Python 3.12.10"}

    return probes


def failing_probes() -> FakeProbes:
    """Build a machine on which nothing is available.

    Reports every tool as absent, the container engine as unreachable, and every volume as
    unmeasurable, which drives required rows to failure and optional rows to information.

    Arguments:
        None.

    Returns:
        A probe surface describing a machine that cannot build the platform.
    """
    return FakeProbes()


def checklist_rows() -> dict[int, str]:
    """Read the prerequisite checklist out of its document.

    Parses the numbered table in Section 1 so the tests grade the script against the authoritative
    document rather than against a second hand-written copy of it.

    Arguments:
        None.

    Returns:
        The documented minimum for each row, keyed by row number.

    Raises:
        AssertionError: If the document carries no parsable checklist rows.
    """
    pattern = re.compile(r"^\|\s*(\d+)\s*\|[^|]+\|[^|]+\|([^|]+)\|")
    rows: dict[int, str] = {}
    for line in CHECKLIST_DOCUMENT.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match is not None:
            rows[int(match.group(1))] = (
                match.group(2).replace("`", "").replace("*", "").strip()
            )

    assert rows, f"no checklist rows parsed from {CHECKLIST_DOCUMENT}"

    return rows


@pytest.mark.unit
def test_the_script_covers_exactly_the_documented_checklist_rows() -> None:
    """Grade the script against the checklist document itself.

    Confirms the report covers every row the document defines and invents none, so adding a row to
    the document without implementing it fails the build instead of passing unnoticed.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the reported rows differ from the documented rows.
    """
    documented = checklist_rows()
    results = preflight.build_report(passing_probes())

    assert sorted(documented) == list(EXPECTED_ROW_NUMBERS)
    assert tuple(result.number for result in results) == tuple(sorted(documented))


@pytest.mark.unit
def test_every_version_floor_matches_the_checklist_document() -> None:
    """Keep the version floors in step with the document.

    Confirms each row graded by a bare version number uses the minimum the document records, so a
    floor cannot be raised in one place and left stale in the other.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any implemented floor differs from the documented one.
    """
    documented = checklist_rows()
    implemented = {
        check.requirement.number: check.requirement for check in preflight.TOOL_CHECKS
    }
    implemented[preflight.INTERPRETER_REQUIREMENT.number] = (
        preflight.INTERPRETER_REQUIREMENT
    )

    compared = 0
    for number, requirement in implemented.items():
        documented_minimum = documented[number]
        if re.fullmatch(r"[\d.]+", documented_minimum):
            assert requirement.minimum_label == documented_minimum
            compared += 1

    assert compared == len(implemented)


@pytest.mark.unit
def test_a_fully_equipped_machine_passes_every_check() -> None:
    """Report success when every requirement is met.

    Confirms a machine matching the recorded prerequisites produces no informational item and no
    failure, so a green report genuinely means nothing is outstanding.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any row is not a pass, or the exit code is non-zero.
    """
    results = preflight.build_report(passing_probes())

    assert [result.status for result in results] == [preflight.Status.OK] * len(results)
    assert preflight.exit_code(results) == 0


@pytest.mark.unit
def test_information_alone_does_not_block_the_build() -> None:
    """Let the build proceed when only optional rows are unsatisfied.

    Confirms a machine missing the host interpreter, the cluster tooling, and the encryption tooling
    still exits zero, which is the contract that keeps an optional tool from stopping the build.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the exit code is non-zero, or the summary miscounts optional items.
    """
    results = preflight.build_report(informational_probes())
    informational = [
        result for result in results if result.status is preflight.Status.INFO
    ]

    assert [result.number for result in informational] == [4, 10, 11, 12, 13]
    assert preflight.exit_code(results) == 0
    assert preflight.summary_line(results) == (
        "PASS: every required check passed, 5 informational item(s)."
    )


@pytest.mark.unit
def test_every_informational_item_says_when_it_starts_to_matter() -> None:
    """Explain the consequence of leaving an optional row unsatisfied.

    Confirms each optional item names when it begins to block work and its remediation, so a reader
    can decide whether to act now or later.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an informational item omits either half of its guidance.
    """
    results = preflight.build_report(informational_probes())
    informational = [
        result for result in results if result.status is preflight.Status.INFO
    ]

    assert informational
    for result in informational:
        note = preflight.note_for(result)
        assert result.matters_when in note
        assert result.remediation in note


@pytest.mark.unit
def test_a_bare_machine_fails_required_checks_and_reports_optional_ones() -> None:
    """Separate blocking failures from optional shortfalls.

    Confirms an empty machine drives required rows to failure and optional rows to information,
    while returning the documented failure exit code.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the verdicts do not split by whether the row is required, or the exit
            code is not the documented failure code.
    """
    results = preflight.build_report(failing_probes())
    failed = [
        result.number for result in results if result.status is preflight.Status.FAIL
    ]
    informational = [
        result.number for result in results if result.status is preflight.Status.INFO
    ]

    assert failed == [1, 2, 3, 5, 6, 7, 8, 9]
    assert informational == [4, 10, 11, 12, 13]
    assert preflight.exit_code(results) == 1


@pytest.mark.unit
def test_every_failing_row_names_a_remediation() -> None:
    """Tell the developer what to do about each failure.

    Confirms no unsatisfied row is reported without the command or action that resolves it, since a
    verdict without a remedy leaves the reader no further forward.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any unsatisfied row carries an empty remediation or guidance line.
    """
    results = preflight.build_report(failing_probes())
    unsatisfied = [
        result for result in results if result.status is not preflight.Status.OK
    ]

    assert unsatisfied
    for result in unsatisfied:
        assert result.remediation
        assert result.remediation in preflight.note_for(result)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (1, "winget install --id Docker.DockerDesktop --exact"),
        (2, "winget install --id Docker.DockerDesktop --exact"),
        (3, "winget install --id Git.Git --exact"),
        (5, "uv venv --python 3.14 backend/.venv"),
        (6, "pipx install uv"),
        (9, "winget install --id Kubernetes.kubectl --exact"),
        (10, "winget install --id Kubernetes.kind --exact"),
        (11, "winget install --id SecretsOPerationS.SOPS --exact"),
        (12, "winget install --id FiloSottile.age --exact"),
        (13, "winget install --id PostgreSQL.PostgreSQL --exact"),
    ],
)
def test_installable_tools_name_an_executable_remediation(
    number: int, expected: str
) -> None:
    """Give a runnable command wherever one exists.

    Confirms every row whose remedy is an install reports exactly the command to run, with no prose
    appended that would be passed to the installer as an argument.

    Arguments:
        number: Checklist row to inspect.
        expected: Command the row must name.

    Returns:
        None.

    Raises:
        AssertionError: If the row does not name exactly the expected command.
    """
    results = {
        result.number: result for result in preflight.build_report(failing_probes())
    }

    assert results[number].remediation == expected


@pytest.mark.unit
@pytest.mark.parametrize("number", [4, 7, 8])
def test_rows_with_no_install_still_name_an_action(number: int) -> None:
    """Give actionable guidance where no command can resolve the row.

    Confirms the three rows whose remedy is an operator action rather than an install still say
    what to do, since neither raising an engine resource limit nor freeing disk space has a safe
    command on this shared machine, and the host interpreter needs no install at all.

    Arguments:
        number: Checklist row to inspect.

    Returns:
        None.

    Raises:
        AssertionError: If the row carries no actionable guidance.
    """
    results = {
        result.number: result for result in preflight.build_report(failing_probes())
    }

    assert results[number].remediation
    assert results[number].remediation in preflight.note_for(results[number])


@pytest.mark.unit
def test_a_satisfied_row_carries_no_guidance() -> None:
    """Leave the guidance empty when nothing needs doing.

    Confirms a passing row produces no note, so the report draws the reader's eye only to rows that
    require action.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a passing row produces guidance text.
    """
    results = preflight.build_report(passing_probes())

    assert all(preflight.note_for(result) == "" for result in results)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Docker version 29.7.2, build a7dcaa6", (29, 7, 2)),
        ("git version 2.53.0.windows.4", (2, 53, 0)),
        ("v1.3.2", (1, 3, 2)),
        ("no version here", None),
    ],
)
def test_version_parsing_reads_the_first_dotted_number(
    text: str,
    expected: tuple[int, ...] | None,
) -> None:
    """Read a version out of whatever shape a tool prints.

    Confirms the parser handles a name prefix, a trailing platform suffix, a bare v prefix, and
    output carrying no version at all, which is the full range the checklist tools produce.

    Arguments:
        text: Raw command output to parse.
        expected: Version expected from that output, or None.

    Returns:
        None.

    Raises:
        AssertionError: If the parsed version differs from the expectation.
    """
    assert preflight.parse_version(text) == expected


@pytest.mark.unit
def test_unrecognisable_version_output_is_reported_as_such() -> None:
    """Distinguish unreadable output from an absent tool.

    Confirms a tool that runs but prints no version is reported differently from one that is not
    installed, because the two need different remedies.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the observation does not name the unrecognised output.
    """
    probes = FakeProbes(outputs={("docker", "--version"): "not a version"})
    result = preflight.evaluate_tool(probes, preflight.TOOL_CHECKS[0])

    assert result.status is preflight.Status.FAIL
    assert result.observed == "unrecognised output: not a version"


@pytest.mark.unit
def test_a_version_below_the_floor_fails_with_the_observed_version() -> None:
    """Fail a tool that is present but too old.

    Confirms an installed tool below its minimum is reported with the version it actually has,
    which is what tells the reader an upgrade rather than an install is needed.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the row passes, or does not report the observed version.
    """
    probes = FakeProbes(outputs={("docker", "--version"): "Docker version 26.1.0"})
    result = preflight.evaluate_tool(probes, preflight.TOOL_CHECKS[0])

    assert result.status is preflight.Status.FAIL
    assert result.observed == "26.1.0"


@pytest.mark.unit
def test_the_host_interpreter_is_probed_without_the_project_virtualenv() -> None:
    """Detect the documented gap between the host and virtualenv interpreters.

    Confirms the host interpreter row reads a search path with the virtualenv removed, so running
    the gate through uv run cannot make the host row answer with the virtualenv's interpreter.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the host row does not warn while the virtualenv row passes.
    """
    results = {
        result.number: result
        for result in preflight.build_report(informational_probes())
    }

    assert results[4].status is preflight.Status.INFO
    assert results[4].observed == "3.12.10"
    assert results[5].status is preflight.Status.OK
    assert results[5].observed == "3.14.6"


@pytest.mark.unit
def test_a_base_interpreter_is_not_hidden_from_its_own_report() -> None:
    """Keep the host interpreter visible when no virtualenv is active.

    Confirms only the project's own virtualenv is excluded when the running interpreter is a base
    installation, so running the gate with the host interpreter still reports that interpreter
    rather than claiming it is absent.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a base interpreter's own prefix is excluded.
    """
    base = Path(sys.base_prefix)

    assert preflight.virtualenv_roots(base, base) == [
        (preflight.REPOSITORY_ROOT / "backend" / ".venv").resolve(),
    ]


@pytest.mark.unit
def test_an_active_virtualenv_is_excluded_alongside_the_project_one() -> None:
    """Exclude whichever virtualenv is currently active.

    Confirms a running interpreter whose prefix differs from its base has that prefix excluded as
    well as the project's own, using synthetic prefixes so the assertion cannot be satisfied by the
    project virtualenv that happens to be running the test.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the active virtualenv prefix is not excluded alongside the project one.
    """
    active = preflight.REPOSITORY_ROOT / "synthetic-virtualenv"
    base = preflight.REPOSITORY_ROOT / "synthetic-base-interpreter"

    assert preflight.virtualenv_roots(active, base) == [
        (preflight.REPOSITORY_ROOT / "backend" / ".venv").resolve(),
        active.resolve(),
    ]


@pytest.mark.unit
def test_the_host_search_path_drops_the_active_virtualenv() -> None:
    """Remove the virtualenv from the search path.

    Confirms entries inside the running virtualenv are dropped while unrelated entries survive in
    their original order, which is what makes the host interpreter reachable.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a virtualenv entry survives, or an unrelated entry is dropped.
    """
    inside = str(Path(sys.prefix) / "Scripts")
    outside = str(preflight.REPOSITORY_ROOT / "docs")

    with patch.dict("os.environ", {"PATH": os.pathsep.join(["", inside, outside])}):
        search_path = preflight.host_search_path()

    assert search_path == outside


@pytest.mark.unit
def test_an_unresolvable_search_path_entry_is_treated_as_unrelated() -> None:
    """Survive a search path entry the operating system rejects.

    Confirms an entry that cannot be resolved is kept rather than crashing the gate, since a broken
    entry in the developer's own search path is not this script's problem to fail on.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an unresolvable entry is reported as belonging to a root.
    """
    with patch.object(Path, "resolve", side_effect=OSError):
        assert preflight.within_any("whatever", [preflight.REPOSITORY_ROOT]) is False


@pytest.mark.unit
def test_the_windows_virtualenv_layout_is_preferred_when_present(
    tmp_path: Path,
) -> None:
    """Resolve the interpreter the Windows virtualenv provides.

    Confirms the Scripts layout is chosen when it exists, which is the layout on the machine this
    platform is built on.

    Arguments:
        tmp_path: Temporary directory standing in for a repository root.

    Returns:
        None.

    Raises:
        AssertionError: If the resolved path is not the Windows layout.
    """
    interpreter = tmp_path / ".venv" / "Scripts" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()

    assert preflight.project_interpreter(tmp_path) == interpreter


@pytest.mark.unit
def test_the_posix_virtualenv_layout_is_used_when_it_is_the_one_present(
    tmp_path: Path,
) -> None:
    """Resolve the interpreter a POSIX virtualenv provides.

    Confirms the bin layout is chosen when the Scripts layout is absent, so the same check works
    inside the Linux container image as on the Windows host.

    Arguments:
        tmp_path: Temporary directory standing in for a repository root.

    Returns:
        None.

    Raises:
        AssertionError: If the resolved path is not the POSIX layout.
    """
    interpreter = tmp_path / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()

    assert preflight.project_interpreter(tmp_path) == interpreter


@pytest.mark.unit
def test_a_missing_virtualenv_still_names_a_concrete_path(tmp_path: Path) -> None:
    """Name a path even when no virtualenv exists.

    Confirms an absent virtualenv resolves to a concrete candidate rather than raising, so the row
    reports a missing interpreter instead of crashing the whole report.

    Arguments:
        tmp_path: Temporary directory standing in for a repository root.

    Returns:
        None.

    Raises:
        AssertionError: If the resolved path is not the documented Windows candidate.
    """
    expected = tmp_path / ".venv" / "Scripts" / "python.exe"

    assert preflight.project_interpreter(tmp_path) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("resources", "expected"),
    [
        (None, preflight.Status.FAIL),
        ((2, 32 * preflight.GIBIBYTE), preflight.Status.FAIL),
        ((16, 4 * preflight.GIBIBYTE), preflight.Status.FAIL),
        ((16, 32 * preflight.GIBIBYTE), preflight.Status.OK),
    ],
)
def test_host_resources_fail_when_either_quantity_is_short(
    resources: tuple[int, int] | None,
    expected: preflight.Status,
) -> None:
    """Grade processors and memory together.

    Confirms an unreachable engine, too few processors, and too little memory each fail the row on
    their own, since either shortfall stalls the stack.

    Arguments:
        resources: Fabricated engine resources, or None for an unreachable engine.
        expected: Verdict the row should reach.

    Returns:
        None.

    Raises:
        AssertionError: If the row does not reach the expected verdict.
    """
    result = preflight.evaluate_host_resources(FakeProbes(resources=resources))

    assert result.status is expected


@pytest.mark.unit
def test_disk_space_is_graded_on_the_container_engine_volume_too() -> None:
    """Fail when the container engine's volume is short, whatever the repository volume holds.

    Confirms a machine with a spacious repository drive and a full system drive fails, because the
    pinned images and every named volume land on the latter.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the row passes, or does not report both volumes.
    """
    probes = FakeProbes(
        free_bytes={
            REPOSITORY_VOLUME: 1500 * preflight.GIBIBYTE,
            OTHER_VOLUME: 2 * preflight.GIBIBYTE,
        },
    )

    result = preflight.evaluate_free_disk(probes, REPOSITORY_VOLUME, OTHER_VOLUME)

    assert result.status is preflight.Status.FAIL
    assert "1500.0 GiB" in result.observed
    assert "2.0 GiB" in result.observed


@pytest.mark.unit
def test_one_volume_is_measured_once_when_two_paths_share_it() -> None:
    """Avoid double-reporting a machine that keeps everything on one volume.

    Confirms two distinct paths reported on the same volume produce a single measurement, which is
    what stops a POSIX host whose paths all sit under one root being counted twice.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the volume is reported more than once.
    """
    probes = FakeProbes(
        fallback_free_bytes=PASSING_FREE_BYTES,
        volumes={REPOSITORY_VOLUME: "vol-1", OTHER_VOLUME: "vol-1"},
    )

    result = preflight.evaluate_free_disk(probes, REPOSITORY_VOLUME, OTHER_VOLUME)

    assert result.status is preflight.Status.OK
    assert result.observed.count("GiB") == 1


@pytest.mark.unit
def test_the_disk_row_reports_what_the_container_engine_already_holds() -> None:
    """Report engine storage alongside free space.

    Confirms the row carries the engine's own accounting of the storage the checklist names, and
    omits the categories it does not, which is what keeps the observation both complete and narrow
    enough to read.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the engine's usage is absent, or carries an unnamed category.
    """
    probes = FakeProbes(
        outputs={
            (
                "docker",
                "system",
                "df",
                "--format",
                "{{.Type}} {{.Size}}",
            ): "Images 2.2GB\nContainers 4.8MB\n\nLocal Volumes 3.3GB\nBuild Cache 0B",
        },
        fallback_free_bytes=PASSING_FREE_BYTES,
    )

    result = preflight.evaluate_free_disk(probes, REPOSITORY_VOLUME, REPOSITORY_VOLUME)

    assert result.status is preflight.Status.OK
    assert result.observed.endswith("; engine holds Images 2.2GB, Local Volumes 3.3GB")


@pytest.mark.unit
@pytest.mark.parametrize(
    "output",
    [None, "", "Containers 4.8MB\nBuild Cache 0B"],
)
def test_engine_storage_is_omitted_when_it_carries_nothing_reportable(
    output: str | None,
) -> None:
    """Leave the engine accounting out rather than reporting an empty one.

    Confirms an unreachable engine, an empty answer, and an answer holding only categories the
    checklist does not name all yield no usage summary, so the disk row never appends a meaningless
    fragment.

    Arguments:
        output: Fabricated output of the engine storage query.

    Returns:
        None.

    Raises:
        AssertionError: If a usage summary is produced.
    """
    outputs: dict[tuple[str, ...], str] = (
        {}
        if output is None
        else {("docker", "system", "df", "--format", "{{.Type}} {{.Size}}"): output}
    )

    assert preflight.docker_usage(FakeProbes(outputs=outputs)) is None


@pytest.mark.unit
def test_the_engine_data_root_is_graded_when_the_host_can_measure_it() -> None:
    """Grade the engine's own data root on a native engine.

    Confirms a data root the host can measure is the path graded, which is the correct answer
    wherever the engine runs directly on this machine rather than in a virtual machine.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the reported data root is not the one the engine named.
    """
    data_root = Path("/var/lib/docker")
    probes = FakeProbes(
        outputs={("docker", "info", "--format", "{{.DockerRootDir}}"): str(data_root)},
        fallback_free_bytes=PASSING_FREE_BYTES,
    )

    assert preflight.docker_data_volume(probes, OTHER_VOLUME) == data_root


@pytest.mark.unit
def test_an_unmeasurable_engine_data_root_falls_back_to_the_host_path() -> None:
    """Fall back when the engine's data root lives inside a virtual machine.

    Confirms a data root the host cannot measure, which is what Docker Desktop reports, is replaced
    by the host path that actually backs it, rather than failing the row.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the fallback path is not used.
    """
    data_root = Path("/var/lib/docker")
    probes = FakeProbes(
        outputs={("docker", "info", "--format", "{{.DockerRootDir}}"): str(data_root)},
        free_bytes={data_root: None},
        fallback_free_bytes=PASSING_FREE_BYTES,
    )

    assert preflight.docker_data_volume(probes, OTHER_VOLUME) == OTHER_VOLUME


@pytest.mark.unit
def test_an_unreachable_engine_falls_back_to_the_host_path() -> None:
    """Fall back when the engine cannot be asked at all.

    Confirms an engine that is not running leaves the disk row grading the host path, so the row
    still reports something useful instead of nothing.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the fallback path is not used.
    """
    assert preflight.docker_data_volume(FakeProbes(), OTHER_VOLUME) == OTHER_VOLUME


@pytest.mark.unit
def test_an_unmeasurable_volume_fails_the_disk_row() -> None:
    """Fail rather than guess when a volume cannot be measured.

    Confirms a volume the operating system refuses to measure fails the row and names which volume
    it was, instead of silently grading the remaining one.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the row does not fail, or does not report the unavailable volume.
    """
    probes = FakeProbes(free_bytes={REPOSITORY_VOLUME: None})

    result = preflight.evaluate_free_disk(probes, REPOSITORY_VOLUME, REPOSITORY_VOLUME)

    assert result.status is preflight.Status.FAIL
    assert result.observed.endswith("unavailable")


@pytest.mark.unit
def test_the_volume_identity_probe_reads_the_device_the_path_sits_on() -> None:
    """Identify the volume behind a real path.

    Confirms the probe reports the device identifier the operating system records, which is what
    lets two paths on one volume be recognised as one.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the identity is absent or differs between paths on one volume.
    """
    identity = preflight.REAL_PROBES.volume_identity(preflight.REPOSITORY_ROOT)

    assert identity is not None
    assert identity == preflight.REAL_PROBES.volume_identity(Path(preflight.__file__))


@pytest.mark.unit
def test_an_uninspectable_path_has_no_volume_identity() -> None:
    """Report no identity rather than raising for an uninspectable path.

    Confirms a path the operating system refuses to stat yields an absent identity, so the disk row
    falls back to treating it as its own volume instead of crashing.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the probe does not report an absent identity.
    """
    with patch.object(Path, "stat", side_effect=OSError):
        assert preflight.REAL_PROBES.volume_identity(preflight.REPOSITORY_ROOT) is None


@pytest.mark.unit
def test_either_cluster_tool_satisfies_the_optional_row() -> None:
    """Accept whichever local cluster tool is installed.

    Confirms minikube alone satisfies the row when kind is present but too old, because the
    requirement is one usable cluster tool rather than a specific one.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the row does not pass, or does not name the tool that satisfied it.
    """
    probes = FakeProbes(
        outputs={
            ("kind", "version"): "kind v0.20.0",
            ("minikube", "version"): "minikube version: v1.39.0",
        },
    )

    result = preflight.evaluate_cluster_tooling(probes)

    assert result.status is preflight.Status.OK
    assert result.observed == "minikube 1.39.0"


@pytest.mark.unit
def test_an_outdated_cluster_tool_reports_its_version_not_its_absence() -> None:
    """Distinguish an upgrade from an install.

    Confirms a cluster tool that is installed but below its floor is reported with the version it
    has, so the reader knows to upgrade rather than to install.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the observation does not separate the outdated tool from the absent one.
    """
    probes = FakeProbes(outputs={("kind", "version"): "kind v0.20.0"})

    result = preflight.evaluate_cluster_tooling(probes)

    assert result.status is preflight.Status.INFO
    assert result.observed == "kind 0.20.0, minikube not present"


@pytest.mark.unit
def test_absent_cluster_tooling_informs_and_says_when_it_matters() -> None:
    """Inform rather than fail when no cluster tool is installed.

    Confirms the row reports both tools as absent and explains that Kubernetes is reasoning-only,
    which is why its absence does not block the build.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the row fails, or does not report both tools.
    """
    result = preflight.evaluate_cluster_tooling(FakeProbes())

    assert result.status is preflight.Status.INFO
    assert result.observed == "kind not present, minikube not present"
    assert "reasoning-only" in result.matters_when


@pytest.mark.unit
def test_the_report_aligns_every_row_and_ends_with_a_summary() -> None:
    """Render an aligned report with a verdict line.

    Confirms the rendered report carries a header, a separator, one line per row, and a closing
    summary, with no guidance section when nothing needs doing.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the report is missing its header, separator, or summary.
    """
    results = preflight.build_report(passing_probes())
    lines = preflight.render_report(results).splitlines()

    assert lines[0].startswith("#")
    assert set(lines[1]) <= {"-", " "}
    assert lines[-1] == "PASS: every required check passed, 0 informational item(s)."
    assert "Guidance:" not in lines


@pytest.mark.unit
def test_guidance_is_listed_below_the_table_rather_than_in_a_column() -> None:
    """Keep the table readable at a normal terminal width.

    Confirms remediation text is listed under the table rather than widening a column, and that
    every unsatisfied row appears there exactly once.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If guidance is missing, or the table rows carry the remediation text.
    """
    results = preflight.build_report(failing_probes())
    rendered = preflight.render_report(results)
    table, guidance = rendered.split("Guidance:")

    assert "winget install" not in table
    for result in results:
        assert guidance.count(f"{result.number}. {result.title}:") == 1


@pytest.mark.unit
def test_the_summary_counts_failures_and_informational_items() -> None:
    """State the counts behind a failing verdict.

    Confirms a failing report summarises required failures and optional informational items, so the
    outcome is legible without reading the table.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the summary does not report a failure with both counts.
    """
    results = preflight.build_report(failing_probes())

    assert preflight.summary_line(results) == (
        "FAIL: 8 required check(s) failed, 5 informational item(s)."
    )


@pytest.mark.unit
def test_json_output_carries_every_field_of_every_result() -> None:
    """Emit the report machine-readably without losing anything.

    Confirms the JSON document reproduces each evaluated result field for field, so a caller
    consuming it sees exactly what a reader of the table would.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the document does not mirror the evaluated report.
    """
    results = preflight.build_report(informational_probes())
    payload = json.loads(preflight.render_json(results))

    assert payload["ok"] is True
    assert payload["exit_code"] == 0
    assert payload["summary"] == preflight.summary_line(results)
    assert payload["checks"] == [
        {
            "number": result.number,
            "requirement": result.title,
            "status": str(result.status),
            "minimum": result.minimum,
            "observed": result.observed,
            "remediation": result.remediation,
            "matters_when": result.matters_when,
            "note": preflight.note_for(result),
        }
        for result in results
    ]


@pytest.mark.unit
def test_the_default_run_prints_the_report_and_returns_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Print the table when no format is requested.

    Confirms the default invocation renders the human-readable report and returns the success exit
    code on a machine that meets every requirement.

    Arguments:
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the report is not printed, or the exit code is non-zero.
    """
    code = preflight.main([], probes=passing_probes())
    printed = capsys.readouterr().out

    assert code == 0
    assert "Requirement" in printed
    assert printed.rstrip().endswith("0 informational item(s).")


@pytest.mark.unit
def test_the_json_flag_prints_a_document_and_returns_the_failure_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Print JSON when the flag is given.

    Confirms the machine-readable switch emits a parsable document and still returns the documented
    failure code when a required check is unsatisfied.

    Arguments:
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the output is not parsable JSON, or the exit code is not one.
    """
    code = preflight.main(["--json"], probes=failing_probes())
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["ok"] is False


@pytest.mark.unit
def test_the_script_guard_runs_the_gate_and_exits(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run the gate through its script guard.

    Executes the module under the name Python assigns to a directly executed script with every tool
    made unresolvable, which exercises the guard an ordinary import never reaches.

    Arguments:
        capsys: Fixture capturing standard output.

    Returns:
        None.

    Raises:
        AssertionError: If the script does not exit with the documented failure code.
    """
    module_path = Path(preflight.__file__)

    with (
        patch.object(sys, "argv", ["preflight"]),
        patch.object(shutil, "which", return_value=None),
        patch.object(shutil, "disk_usage", side_effect=OSError),
        pytest.raises(SystemExit) as raised,
    ):
        runpy.run_path(str(module_path), run_name="__main__")

    capsys.readouterr()

    assert raised.value.code == 1


@pytest.mark.unit
def test_the_interpreter_guards_cover_every_script() -> None:
    """Guard every script in the directory, not only this one.

    Confirms the discovery that feeds the two interpreter guards actually finds the scripts, so a
    directory-wide linter exemption is backed by a directory-wide guarantee.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the discovered scripts do not include the gate itself.
    """
    assert Path(preflight.__file__) in SCRIPT_PATHS


@pytest.mark.unit
@pytest.mark.parametrize("module_path", SCRIPT_PATHS, ids=lambda path: path.name)
def test_every_annotation_resolves_from_the_module_namespace(module_path: Path) -> None:
    """Keep every annotation evaluable at runtime.

    Resolves the type hints of every class and function each script defines, which fails when a
    name used in an annotation is imported only for type checking and would raise on an interpreter
    that evaluates annotations eagerly.

    Arguments:
        module_path: Script whose annotations should resolve.

    Returns:
        None.

    Raises:
        AssertionError: If any annotation fails to resolve to a mapping.
        NameError: If an annotation names something absent from the module namespace.
    """
    module = script_module(module_path)
    inspected: list[object] = []
    for member in vars(module).values():
        if getattr(member, "__module__", None) != module.__name__:
            continue

        if inspect.isclass(member):
            inspected.append(member)
            inspected.extend(
                attribute
                for attribute in vars(member).values()
                if inspect.isfunction(attribute)
            )
        elif inspect.isfunction(member):
            inspected.append(member)

    assert all(isinstance(get_type_hints(member), dict) for member in inspected)


@pytest.mark.unit
@pytest.mark.parametrize("module_path", SCRIPT_PATHS, ids=lambda path: path.name)
def test_the_gate_parses_under_the_interpreter_it_exists_to_diagnose(
    module_path: Path,
) -> None:
    """Stay readable by an older interpreter.

    Compiles each script under the oldest syntax the repository might meet on a developer's search
    path, because a script that raises a syntax error diagnoses nothing.

    Arguments:
        module_path: Script whose source should compile.

    Returns:
        None.

    Raises:
        AssertionError: If the script uses syntax an older interpreter cannot parse.
    """
    source = module_path.read_text(encoding="utf-8")

    compile(
        source,
        str(module_path),
        "exec",
        flags=0,
        dont_inherit=True,
        optimize=0,
        _feature_version=OLDEST_SUPPORTED_MINOR,
    )


@pytest.mark.unit
def test_an_absent_executable_is_captured_as_no_output() -> None:
    """Treat an unresolvable executable as an absent tool.

    Confirms the host inspection layer resolves the executable before running it, so a missing tool
    is reported rather than raised out of the probe.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the probe does not report an absent tool.
    """
    with patch.object(shutil, "which", return_value=None):
        assert preflight.REAL_PROBES.capture(("nonexistent-tool", "--version")) is None


@pytest.mark.unit
def test_the_host_probe_resolves_against_the_stripped_search_path() -> None:
    """Resolve the host interpreter off a search path without the virtualenv.

    Confirms the outside-virtualenv probe passes the stripped search path to the resolver, which is
    the mechanism the host interpreter row depends on.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the resolver is not given the stripped search path.
    """
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="Python 3.12.10", stderr=""
    )

    with (
        patch.object(shutil, "which", return_value="python") as which,
        patch.object(subprocess, "run", return_value=completed),
    ):
        output = preflight.REAL_PROBES.capture_outside_virtualenv(
            ("python", "--version")
        )

    assert output == "Python 3.12.10"
    assert which.call_args.kwargs["path"] == preflight.host_search_path()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("completed", "expected"),
    [
        (
            subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="boom"
            ),
            None,
        ),
        (
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=" out \n", stderr=""
            ),
            "out",
        ),
        (
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=" err \n"
            ),
            "err",
        ),
    ],
)
def test_command_output_prefers_standard_output_and_rejects_failures(
    completed: subprocess.CompletedProcess[str],
    expected: str | None,
) -> None:
    """Read a version from whichever stream carries it.

    Confirms a non-zero exit yields no output, standard output is preferred when present, and
    standard error is used as the fallback for tools that report their version there.

    Arguments:
        completed: Fabricated result of running the command.
        expected: Output the probe should report.

    Returns:
        None.

    Raises:
        AssertionError: If the probe does not report the expected output.
    """
    with (
        patch.object(shutil, "which", return_value="tool"),
        patch.object(subprocess, "run", return_value=completed),
    ):
        assert preflight.REAL_PROBES.capture(("tool", "--version")) == expected


@pytest.mark.unit
@pytest.mark.parametrize("failure", [OSError, subprocess.TimeoutExpired("tool", 1)])
def test_a_command_that_cannot_run_is_captured_as_no_output(
    failure: type[Exception] | Exception,
) -> None:
    """Collapse every execution failure into an absent result.

    Confirms a process that cannot start and one that exceeds its timeout are both reported as no
    output, so a hung tool cannot stall the gate or crash it.

    Arguments:
        failure: Exception the fabricated execution raises.

    Returns:
        None.

    Raises:
        AssertionError: If the probe does not report an absent result.
    """
    with (
        patch.object(shutil, "which", return_value="tool"),
        patch.object(subprocess, "run", side_effect=failure),
    ):
        assert preflight.REAL_PROBES.capture(("tool", "--version")) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (None, None),
        ("16", None),
        ("many lots", None),
        ("16 33600000000", (16, 33600000000)),
    ],
)
def test_container_engine_resources_are_rejected_unless_they_parse(
    output: str | None,
    expected: tuple[int, int] | None,
) -> None:
    """Accept engine resources only when the answer parses.

    Confirms an unreachable engine, a truncated answer, and a non-numeric answer are all rejected,
    so the resource row never grades against a misread value.

    Arguments:
        output: Fabricated output of the engine query.
        expected: Resources the probe should report.

    Returns:
        None.

    Raises:
        AssertionError: If the probe does not report the expected resources.
    """
    with patch.object(preflight.RealProbes, "capture", return_value=output):
        assert preflight.REAL_PROBES.docker_resources() == expected


@pytest.mark.unit
def test_free_space_is_read_from_the_requested_volume() -> None:
    """Measure the volume the caller names.

    Confirms the probe reports the free space the operating system gives for the requested path,
    without substituting a different volume.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the reported figure or the measured path is not the requested one.
    """
    usage = SimpleNamespace(total=3, used=2, free=1)

    with patch.object(shutil, "disk_usage", return_value=usage) as disk_usage:
        free_bytes = preflight.REAL_PROBES.free_disk_bytes(preflight.REPOSITORY_ROOT)

    assert free_bytes == 1
    assert disk_usage.call_args.args == (preflight.REPOSITORY_ROOT,)


@pytest.mark.unit
def test_an_unmeasurable_volume_is_captured_as_no_result() -> None:
    """Report an unmeasurable volume without raising.

    Confirms a volume the operating system refuses to measure yields an absent result, so the row
    fails with an explanation rather than aborting the report.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the probe does not report an absent result.
    """
    with patch.object(shutil, "disk_usage", side_effect=OSError):
        assert preflight.REAL_PROBES.free_disk_bytes(preflight.REPOSITORY_ROOT) is None
