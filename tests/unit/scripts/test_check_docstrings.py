"""Unit tests for the documentation standard checker.

Covers one compliant file and one deliberately failing file per rule, so the checker is proven to
reject what it claims to reject rather than merely to pass a repository that already complies.
"""

import runpy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import check_docstrings

COMPLIANT_SOURCE = '''"""Compliant probe module.

Carries a module docstring of the required shape, one compliant class, and one compliant callable,
so the checker has a positive case that must produce no violations at all.
"""


class Probe(Exception):
    """Probe failure raised by the compliant example.

    Inherits from ``Exception`` and adds nothing, existing only so the checker sees a class with a
    base, a documented attribute, and a documented member.

    Attributes:
        code: Identifier the caller matches on.

    Members:
        describe: Return the identifier as text.
    """

    code = "probe"

    def describe(self) -> str:
        """Return the identifier as text.

        Exists so the compliant example carries a method, which is what makes the Members section
        required rather than optional.

        Arguments:
            None.

        Returns:
            The identifier.

        Raises:
            None.
        """
        return self.code


def probe(value: int) -> int:
    """Double one value.

    Exists so the compliant example carries a callable with an argument, a return, and no raises
    section, which is the shape the standard calls for.

    Arguments:
        value: The value to double.

    Returns:
        Twice the value.
    """
    return value * 2
'''

MODULE_DOCSTRING = (
    '"""Probe module.\n\n'
    "Carries a description of the required length so only the\n"
    "rule under test can fail.\n"
    '"""\n'
)


def _write(directory: Path, name: str, source: str) -> Path:
    """Write one probe file into a directory.

    Creates the file the checker will be pointed at, so each test states only the source it cares
    about rather than repeating the file handling.

    Arguments:
        directory: Directory to write into.
        name: File name to write.
        source: Contents of the file.

    Returns:
        The path that was written.

    Raises:
        None.
    """
    path = directory / name
    path.write_text(source, encoding="utf-8")

    return path


def _check(tmp_path: Path, source: str) -> list[check_docstrings.Violation]:
    """Check one probe source through the checker.

    Writes the source into a temporary directory and runs the file-level entry point against it, so
    a test reads as a source string and the violations it produces.

    Arguments:
        tmp_path: Temporary directory to write into.
        source: Contents of the probe file.

    Returns:
        Every violation the checker reported.

    Raises:
        SyntaxError: If the probe source cannot be parsed.
    """
    return check_docstrings.check_file(_write(tmp_path, "probe.py", source))


@pytest.mark.unit
def test_a_compliant_file_produces_no_violations(tmp_path: Path) -> None:
    """Accept a file that follows the standard.

    Confirms the checker stays silent on a file carrying every required section, because a checker
    that also rejects correct code would simply be turned off.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If any violation is reported.
    """
    assert _check(tmp_path, COMPLIANT_SOURCE) == []


@pytest.mark.unit
def test_a_comment_is_rejected(tmp_path: Path) -> None:
    """Reject ordinary commentary.

    Confirms a comment line is reported, which is the rule the whole standard rests on since every
    explanation is supposed to live in a docstring instead.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the comment is not reported.
    """
    violations = _check(tmp_path, f"{MODULE_DOCSTRING}\nVALUE = 1\n")
    assert violations == []

    violations = _check(tmp_path, f"{MODULE_DOCSTRING}\nVALUE = 1  # explain the value\n")

    assert [violation.rule for violation in violations] == ["comment"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "pragma",
    [
        "# noqa: E501",
        "# type: ignore[assignment]",
        "# pragma: no cover",
        "# ruff: noqa",
        "# mypy: disallow-any-explicit",
    ],
)
def test_an_allowed_pragma_is_accepted(tmp_path: Path, pragma: str) -> None:
    """Permit the pragmas the toolchain needs.

    Confirms each enumerated machine instruction survives the comment rule, since removing one
    would break the linter or the type checker it addresses.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.
        pragma: The pragma under test.

    Returns:
        None.

    Raises:
        AssertionError: If the pragma is reported as a comment.
    """
    violations = _check(tmp_path, f"{MODULE_DOCSTRING}\nVALUE = 1  {pragma}\n")

    assert violations == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "disguise",
    [
        "# noqa this explains the value",
        "# type: the value is an integer",
        "# pragma: explain why this exists",
        "# ruff: this is prose",
        "# mypy: this is prose!",
    ],
)
def test_prose_disguised_as_a_pragma_is_rejected(tmp_path: Path, disguise: str) -> None:
    """Stop the allowlist widening into commentary.

    Confirms a comment that merely opens with an allowed word is still rejected, because matching a
    prefix rather than a directive would let any explanation ride in behind one.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.
        disguise: The prose comment under test.

    Returns:
        None.

    Raises:
        AssertionError: If the disguised comment is accepted.
    """
    violations = _check(tmp_path, f"{MODULE_DOCSTRING}\nVALUE = 1  {disguise}\n")

    assert [violation.rule for violation in violations] == ["comment"]


@pytest.mark.unit
def test_a_missing_module_docstring_is_rejected(tmp_path: Path) -> None:
    """Reject a file with no docstring.

    Confirms the file level is checked, because the module docstring is the first thing a reader
    or an agent meets when opening the file.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the missing docstring is not reported.
    """
    violations = _check(tmp_path, "VALUE = 1\n")

    assert [violation.rule for violation in violations] == ["file-docstring"]


@pytest.mark.unit
@pytest.mark.parametrize("description", ["One line only.", "One.\nTwo.\nThree.\nFour."])
def test_a_description_of_the_wrong_length_is_rejected(tmp_path: Path, description: str) -> None:
    """Reject a description that is too short or too long.

    Confirms both bounds are enforced, since a one-line description restates the title and a long
    one is where prose that belongs elsewhere accumulates.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.
        description: The description body under test.

    Returns:
        None.

    Raises:
        AssertionError: If the description length is not reported.
    """
    violations = _check(tmp_path, f'"""Probe module.\n\n{description}\n"""\n')

    assert [violation.rule for violation in violations] == ["file-docstring"]
    assert "description" in violations[0].detail


@pytest.mark.unit
def test_a_missing_title_is_rejected() -> None:
    """Reject a docstring with no title.

    Confirms an empty summary line is reported, because tooling that surfaces a symbol shows the
    title alone and an empty one leaves the reader with nothing.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the missing title is not reported.
    """
    violations = check_docstrings.check_description(
        Path("probe.py"),
        1,
        "file-docstring",
        check_docstrings.parse_docstring(""),
    )

    assert any("title" in violation.detail for violation in violations)


@pytest.mark.unit
def test_a_class_without_a_docstring_is_rejected(tmp_path: Path) -> None:
    """Reject an undocumented class.

    Confirms the class level is checked, since a class reached from a traceback is read without any
    surrounding context.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the missing docstring is not reported.
    """
    violations = _check(tmp_path, f"{MODULE_DOCSTRING}\n\nclass Probe:\n    value = 1\n")

    assert [violation.rule for violation in violations] == ["class-docstring"]


@pytest.mark.unit
def test_a_class_that_hides_its_parent_is_rejected(tmp_path: Path) -> None:
    """Reject a subclass that does not say what it inherits.

    Confirms a class with a base is required to name it, because the behaviour a subclass inherits
    is invisible at the call site otherwise.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the omission is not reported.
    """
    source = (
        f"{MODULE_DOCSTRING}\n\nclass Probe(Exception):\n"
        '    """Probe failure.\n\n'
        "    Says nothing about where it comes from, which is exactly what the rule is meant to\n"
        "    catch in a subclass.\n"
        '    """\n'
    )

    violations = _check(tmp_path, source)

    assert any("inherits" in violation.detail for violation in violations)


@pytest.mark.unit
def test_a_class_that_names_its_parent_differently_is_accepted(tmp_path: Path) -> None:
    """Accept any wording that names the base.

    Confirms the rule matches the base class rather than one phrasing of it, so an accurate
    docstring is not rejected for saying "extends" where another says "inherits".

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the accurate wording is reported.
    """
    source = (
        f"{MODULE_DOCSTRING}\n\nclass Probe(Exception):\n"
        '    """Probe failure.\n\n'
        "    Extends Exception and adds nothing, existing only to prove the rule matches the base\n"
        "    class name rather than one particular way of writing the sentence.\n"
        '    """\n'
    )

    assert _check(tmp_path, source) == []


@pytest.mark.unit
def test_a_subscripted_base_demands_no_particular_wording(tmp_path: Path) -> None:
    """Ask nothing of a base the checker cannot name.

    Confirms a generic base yields no name to match, so the inheritance rule stays silent rather
    than demanding wording for a base it could only report as an expression.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If a violation is reported.
    """
    source = (
        f"{MODULE_DOCSTRING}\n"
        "from collections.abc import Sequence\n\n\n"
        "class Probe(Sequence[int]):\n"
        '    """Probe collection.\n\n'
        "    Carries a subscripted base, which reduces to an expression rather than a name and is\n"
        "    therefore outside what the inheritance rule can check.\n"
        '    """\n'
    )

    assert _check(tmp_path, source) == []


@pytest.mark.unit
def test_a_dotted_base_is_matched_by_its_final_segment(tmp_path: Path) -> None:
    """Match a base written with its module prefix.

    Confirms a class inheriting from a dotted name is satisfied by naming the final segment, which
    is the form a reader recognises and the form the house style already uses.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the dotted base is not matched.
    """
    source = (
        f"{MODULE_DOCSTRING}\n"
        "import logging\n\n\n"
        "class Probe(logging.Formatter):\n"
        '    """Probe formatter.\n\n'
        "    Inherits from Formatter and changes nothing, existing to prove a dotted base is\n"
        "    matched by the name a reader would actually write.\n"
        '    """\n'
    )

    assert _check(tmp_path, source) == []


@pytest.mark.unit
def test_a_class_with_attributes_needs_that_section(tmp_path: Path) -> None:
    """Reject a class that leaves its attributes undocumented.

    Confirms a class defining an attribute must carry the section listing it, which is what lets a
    reader learn its shape without reading the body.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the missing section is not reported.
    """
    source = (
        f"{MODULE_DOCSTRING}\n\nclass Probe:\n"
        '    """Probe value holder.\n\n'
        "    Defines one attribute and documents neither it nor the section it belongs in, which\n"
        "    is the omission under test here.\n"
        '    """\n\n'
        "    value = 1\n"
    )

    violations = _check(tmp_path, source)

    assert any("Attributes" in violation.detail for violation in violations)


@pytest.mark.unit
def test_a_class_with_methods_needs_that_section(tmp_path: Path) -> None:
    """Reject a class that leaves its members unlisted.

    Confirms a class defining a method must list it, so the docstring answers what the class can do
    without the reader scrolling its body.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the missing section is not reported.
    """
    source = (
        f"{MODULE_DOCSTRING}\n\nclass Probe:\n"
        '    """Probe behaviour holder.\n\n'
        "    Defines one method and never lists it, which is the omission the members rule exists\n"
        "    to catch in a class like this.\n"
        '    """\n\n'
        "    def run(self) -> None:\n"
        '        """Do nothing at all.\n\n'
        "        Exists only so the class under test defines a member, which is what makes the\n"
        "        members section required.\n\n"
        "        Arguments:\n            None.\n\n"
        "        Returns:\n            None.\n"
        '        """\n'
    )

    violations = _check(tmp_path, source)

    assert any("Members" in violation.detail for violation in violations)


@pytest.mark.unit
def test_a_class_with_nothing_to_list_needs_no_sections(tmp_path: Path) -> None:
    """Accept a class that has nothing to list.

    Confirms a class with neither attributes nor methods is not forced to carry empty sections,
    which is the ceremony the standard deliberately refuses.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If any violation is reported.
    """
    source = (
        f"{MODULE_DOCSTRING}\n\nclass Probe:\n"
        '    """Probe marker.\n\n'
        "    Defines nothing at all, existing only to prove the checker demands no section from a\n"
        "    class that has nothing to put in one.\n"
        '    """\n'
    )

    assert _check(tmp_path, source) == []


@pytest.mark.unit
def test_a_method_local_assignment_demands_no_attributes_section(tmp_path: Path) -> None:
    """Ignore a local variable when deciding what a class documents.

    Confirms an assignment inside a method does not make the checker demand an Attributes section,
    which is the false positive that would force exactly the ceremony the standard refuses.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If an Attributes section is demanded.
    """
    source = (
        f"{MODULE_DOCSTRING}\n\nclass Probe:\n"
        '    """Probe calculator.\n\n'
        "    Holds no attributes at all and computes its result in a local variable, which is the\n"
        "    case the attribute rule must not mistake for a documented field.\n\n"
        "    Members:\n        run: Return a computed total.\n"
        '    """\n\n'
        "    def run(self) -> int:\n"
        '        """Return a computed total.\n\n'
        "        Assigns a local variable and returns it, so the class under test has a method\n"
        "        body carrying an assignment and no attribute.\n\n"
        "        Arguments:\n            None.\n\n"
        "        Returns:\n            The total.\n"
        '        """\n'
        "        total = 1\n\n"
        "        return total\n"
    )

    assert _check(tmp_path, source) == []


@pytest.mark.unit
def test_an_instance_attribute_demands_an_attributes_section(tmp_path: Path) -> None:
    """Notice an attribute assigned onto the instance.

    Confirms a field set in a constructor counts as an attribute, since restricting the rule to the
    class body would let the commonest way of declaring state go undocumented.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the missing section is not reported.
    """
    source = (
        f"{MODULE_DOCSTRING}\n\nclass Probe:\n"
        '    """Probe value holder.\n\n'
        "    Stores one value on the instance and documents neither it nor the section it belongs\n"
        "    in, which is the omission under test.\n\n"
        "    Members:\n        __init__: Store the value.\n"
        '    """\n\n'
        "    def __init__(self) -> None:\n"
        '        """Store the value.\n\n'
        "        Assigns the instance attribute the class never documents, which is what the rule\n"
        "        under test is expected to notice.\n\n"
        "        Arguments:\n            None.\n\n"
        "        Returns:\n            None.\n"
        '        """\n'
        "        self.value = 1\n"
    )

    violations = _check(tmp_path, source)

    assert any("Attributes" in violation.detail for violation in violations)


@pytest.mark.unit
def test_a_raising_callable_needs_a_raises_section(tmp_path: Path) -> None:
    """Demand the raises section from a callable that raises.

    Confirms a callable containing a raise statement must document it, because the failure a caller
    has to handle is invisible from the signature.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the missing section is not reported.
    """
    source = (
        f"{MODULE_DOCSTRING}\n\ndef probe() -> None:\n"
        '    """Fail every time.\n\n'
        "    Raises without documenting it, which is the omission the raises rule exists to catch\n"
        "    in a callable like this one.\n\n"
        "    Arguments:\n        None.\n\n"
        "    Returns:\n        None.\n"
        '    """\n'
        "    raise ValueError\n"
    )

    violations = _check(tmp_path, source)

    assert any("Raises" in violation.detail for violation in violations)


@pytest.mark.unit
def test_a_raise_inside_a_nested_callable_is_not_attributed_outward(tmp_path: Path) -> None:
    """Attribute a raise to the callable that performs it.

    Confirms a raise inside a nested function does not make the enclosing one document it, since
    the outer callable does not raise and has nothing to write.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the enclosing callable is asked for a raises section.
    """
    source = (
        f"{MODULE_DOCSTRING}\n\ndef probe() -> object:\n"
        '    """Build a failing callable.\n\n'
        "    Returns an inner callable that raises, so the raise belongs to the inner scope and\n"
        "    never to this one.\n\n"
        "    Arguments:\n        None.\n\n"
        "    Returns:\n        The inner callable.\n"
        '    """\n\n'
        "    def inner() -> None:\n"
        '        """Fail every time.\n\n'
        "        Raises unconditionally, documenting the failure the enclosing callable merely\n"
        "        hands back to its caller.\n\n"
        "        Arguments:\n            None.\n\n"
        "        Returns:\n            None.\n\n"
        "        Raises:\n            ValueError: Always.\n"
        '        """\n'
        "        raise ValueError\n\n"
        "    return inner\n"
    )

    assert _check(tmp_path, source) == []


@pytest.mark.unit
def test_a_generator_documents_what_it_yields(tmp_path: Path) -> None:
    """Demand a yields section from a generator.

    Confirms a callable that yields is asked for `Yields:` rather than `Returns:`, because a
    generator returns an iterator and describing that as a return value misleads the reader.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the yields section is not demanded.
    """
    source = (
        f"{MODULE_DOCSTRING}\n\ndef probe() -> object:\n"
        '    """Produce one value.\n\n'
        "    Yields a single value while documenting a return, which is the mismatch the rule\n"
        "    under test is expected to reject.\n\n"
        "    Arguments:\n        None.\n\n"
        "    Returns:\n        Nothing at all.\n"
        '    """\n'
        "    yield 1\n"
    )

    violations = _check(tmp_path, source)

    assert any("Yields" in violation.detail for violation in violations)


@pytest.mark.unit
def test_a_bare_section_heading_is_rejected(tmp_path: Path) -> None:
    """Reject a section with nothing underneath.

    Confirms an empty heading does not satisfy a required section, since it documents no more than
    leaving the section out and would otherwise defeat every section rule at once.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the empty section is accepted.
    """
    source = (
        f"{MODULE_DOCSTRING}\n\ndef probe() -> None:\n"
        '    """Do nothing at all.\n\n'
        "    Carries both required headings with nothing written under either, which is the shape\n"
        "    the content rule exists to reject.\n\n"
        "    Arguments:\n\n"
        "    Returns:\n"
        '    """\n'
        "    return None\n"
    )

    violations = _check(tmp_path, source)

    assert len(violations) == 2  # noqa: PLR2004


@pytest.mark.unit
def test_a_missing_root_stops_the_run(tmp_path: Path) -> None:
    """Refuse to check a root that does not exist.

    Confirms a mistyped or renamed root raises rather than reporting a clean run, because a silent
    empty walk would turn the whole gate off without any signal.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the missing root does not raise.
    """
    with (
        patch.object(check_docstrings, "REPOSITORY_ROOT", tmp_path),
        pytest.raises(FileNotFoundError, match="absent"),
    ):
        list(check_docstrings.iter_python_files(["absent"]))


@pytest.mark.unit
def test_an_unparsable_file_is_reported_not_raised(tmp_path: Path) -> None:
    """Report a file that cannot be parsed.

    Confirms a syntax error becomes an ordinary violation line, so the report stays readable and
    the run still fails rather than ending in a traceback.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the failure is not reported as a violation.
    """
    violations = _check(tmp_path, "def (:\n")

    assert [violation.rule for violation in violations] == ["unreadable"]


@pytest.mark.unit
def test_a_callable_without_a_docstring_is_rejected(tmp_path: Path) -> None:
    """Reject an undocumented callable.

    Confirms the callable level is checked including private helpers, since an agent reaching a
    helper from a symbol search sees only its docstring.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the missing docstring is not reported.
    """
    violations = _check(tmp_path, f"{MODULE_DOCSTRING}\n\ndef probe() -> None:\n    return None\n")

    assert [violation.rule for violation in violations] == ["callable-docstring"]


@pytest.mark.unit
@pytest.mark.parametrize("section", ["Arguments", "Returns"])
def test_a_callable_missing_a_required_section_is_rejected(tmp_path: Path, section: str) -> None:
    """Reject a callable missing a required section.

    Confirms both mandatory sections are enforced, because a docstring without them degrades into
    the restated signature the standard exists to prevent.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.
        section: The section deliberately omitted.

    Returns:
        None.

    Raises:
        AssertionError: If the omission is not reported.
    """
    sections = {
        "Arguments": "        Returns:\n            None.\n",
        "Returns": "        Arguments:\n            None.\n",
    }
    source = (
        f"{MODULE_DOCSTRING}\n\ndef probe() -> None:\n"
        '    """Do nothing at all.\n\n'
        "    Omits one required section so the rule under test is the only one that can fail on\n"
        "    this callable.\n\n"
        f"{sections[section]}"
        '    """\n'
        "    return None\n"
    )

    violations = _check(tmp_path, source)

    assert any(section in violation.detail for violation in violations)


@pytest.mark.unit
def test_a_callable_that_raises_nothing_needs_no_raises_section(tmp_path: Path) -> None:
    """Accept a callable with no raises section.

    Confirms the raises section stays optional, which is the second place the standard refuses to
    force an empty heading.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If a violation is reported.
    """
    source = (
        f"{MODULE_DOCSTRING}\n\ndef probe() -> None:\n"
        '    """Do nothing at all.\n\n'
        "    Carries both required sections and no raises section, which is the shape a callable\n"
        "    that cannot fail is supposed to have.\n\n"
        "    Arguments:\n        None.\n\n"
        "    Returns:\n        None.\n"
        '    """\n'
        "    return None\n"
    )

    assert _check(tmp_path, source) == []


@pytest.mark.unit
@pytest.mark.parametrize("excluded", ["migrations", "__pycache__", ".venv", ".agents"])
def test_generated_and_vendored_paths_are_skipped(tmp_path: Path, excluded: str) -> None:
    """Skip code the project did not write.

    Confirms each excluded directory is never visited, because a file written by a framework
    command or installed by a package manager cannot be held to a hand-written standard.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.
        excluded: Name of the excluded directory under test.

    Returns:
        None.

    Raises:
        AssertionError: If an excluded file is returned.
    """
    (tmp_path / "package").mkdir()
    (tmp_path / "package" / excluded).mkdir()
    owned = _write(tmp_path / "package", "owned.py", MODULE_DOCSTRING)
    _write(tmp_path / "package" / excluded, "generated.py", "VALUE = 1\n")

    with patch.object(check_docstrings, "REPOSITORY_ROOT", tmp_path):
        assert list(check_docstrings.iter_python_files(["package"])) == [owned]


@pytest.mark.unit
def test_a_named_file_is_checked_directly(tmp_path: Path) -> None:
    """Accept a file path as well as a directory.

    Confirms a single named file is checked, which is what lets an author run the checker against
    the one file they are editing.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the named file is not returned.
    """
    _write(tmp_path, "probe.py", MODULE_DOCSTRING)

    with patch.object(check_docstrings, "REPOSITORY_ROOT", tmp_path):
        assert list(check_docstrings.iter_python_files(["probe.py"])) == [tmp_path / "probe.py"]


@pytest.mark.unit
def test_a_violation_outside_the_repository_reports_its_full_path() -> None:
    """Report a path the repository does not contain.

    Confirms rendering falls back to the absolute path rather than failing, so a checker pointed at
    a file elsewhere still produces a readable report.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the rendered line does not carry the path.
    """
    outside = Path("//probe-host/share/probe.py")
    violation = check_docstrings.Violation(outside, 3, "comment", "detail")

    assert str(outside) in check_docstrings.format_violation(violation)


@pytest.mark.unit
def test_the_entry_point_reports_a_clean_run(tmp_path: Path) -> None:
    """Exit zero when every file complies.

    Confirms a clean run returns the success code the quality gate and the pre-commit hook read,
    which is the only signal either of them acts on.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the exit code is not zero.
    """
    _write(tmp_path, "probe.py", COMPLIANT_SOURCE)

    with patch.object(check_docstrings, "REPOSITORY_ROOT", tmp_path):
        assert check_docstrings.main(["probe.py"]) == check_docstrings.EXIT_OK


@pytest.mark.unit
def test_the_entry_point_reports_violations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit non-zero and print every violation.

    Confirms a failing run reports each violation on its own line and returns the failure code, so
    the hook blocks the commit and the author sees what to fix.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.
        capsys: Capture fixture supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the exit code or the report is wrong.
    """
    _write(tmp_path, "probe.py", "VALUE = 1\n")

    with patch.object(check_docstrings, "REPOSITORY_ROOT", tmp_path):
        code = check_docstrings.main(["probe.py"])

    captured = capsys.readouterr()

    assert code == check_docstrings.EXIT_VIOLATIONS
    assert "FAIL" in captured.out
    assert "1 violation(s)" in captured.out


@pytest.mark.unit
def test_the_entry_point_defaults_to_the_project_roots() -> None:
    """Check the whole project when given no path.

    Confirms an argument-free run selects the configured roots, which is how the quality gate and
    the pre-commit hook invoke it.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the default roots are not selected.
    """
    selected: list[str] = []

    def record(paths: list[str]) -> list[Path]:
        """Record the roots the entry point selected.

        Stands in for the file walk so the test observes the selection without checking every file
        in the repository again.

        Arguments:
            paths: Roots the entry point passed in.

        Returns:
            An empty list of files.

        Raises:
            None.
        """
        selected.extend(paths)

        return []

    with patch.object(check_docstrings, "iter_python_files", record):
        assert check_docstrings.main([]) == check_docstrings.EXIT_OK

    assert selected == list(check_docstrings.DEFAULT_PATHS)


@pytest.mark.unit
def test_the_script_guard_runs_the_entry_point(tmp_path: Path) -> None:
    """Run the checker as a script.

    Executes the module under the name Python gives a directly executed script and confirms it
    surfaces the entry point's exit code, which is the path every hook and task invocation takes.

    Arguments:
        tmp_path: Temporary directory supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the script guard does not surface the exit code.
    """
    _write(tmp_path, "probe.py", "VALUE = 1\n")
    module_path = Path(str(check_docstrings.__file__))

    with (
        patch.object(sys, "argv", ["check_docstrings.py", str(tmp_path / "probe.py")]),
        pytest.raises(SystemExit) as raised,
    ):
        runpy.run_path(str(module_path), run_name="__main__")

    assert raised.value.code == check_docstrings.EXIT_VIOLATIONS
