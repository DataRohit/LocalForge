"""Documentation standard enforcement.

Rejects comment lines outside a small pragma allowlist and checks that every module, class, and
callable carries a docstring with the sections the project's standard requires, so the style cannot
drift between one author and the next.
"""

import argparse
import ast
import io
import re
import tokenize
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_PATHS = ("backend/src", "backend/tests", "backend/scripts", ".github/scripts")
EXCLUDED_DIRECTORIES = frozenset({"__pycache__", "migrations", ".agents", ".venv"})

ALLOWED_PRAGMAS = re.compile(
    r"^(noqa(:\s*[A-Z]+[0-9]+(\s*,\s*[A-Z]+[0-9]+)*)?"
    r"|type:\s*ignore(\[[a-z0-9\-, ]+\])?"
    r"|pragma:\s*no cover"
    r"|ruff:\s*noqa(:\s*[A-Z]+[0-9]+(\s*,\s*[A-Z]+[0-9]+)*)?"
    r"|mypy:\s*[a-z0-9\-=, ]+)$"
)

SECTION_NAMES = ("Arguments:", "Returns:", "Yields:", "Raises:", "Attributes:", "Members:")

MINIMUM_DESCRIPTION_LINES = 2
MAXIMUM_DESCRIPTION_LINES = 3

EXIT_OK = 0
EXIT_VIOLATIONS = 1

Definition = ast.FunctionDef | ast.AsyncFunctionDef
NESTED_SCOPES = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda


@dataclass(frozen=True)
class Violation:
    """One rejected construct in one file.

    Carries everything the printed report needs, so reporting never has to reach back into the
    source that produced it.

    Attributes:
        path: File the violation was found in.
        line: One-based line number the violation starts at.
        rule: Short identifier of the rule that rejected it.
        detail: Human-readable explanation of what is wrong.

    Members:
        None beyond those the dataclass generates.
    """

    path: Path
    line: int
    rule: str
    detail: str


@dataclass(frozen=True)
class Docstring:
    """One docstring split into the parts the standard names.

    Separates the title from the description and records the content of each section, so a rule
    checks one part without reparsing the text and can tell a heading from a filled-in section.

    Attributes:
        title: The one-line summary.
        description: The non-empty lines between the title and the first section.
        sections: Section name mapped to its non-empty content lines.

    Members:
        None beyond those the dataclass generates.
    """

    title: str
    description: tuple[str, ...]
    sections: dict[str, tuple[str, ...]]

    def filled(self, section: str) -> bool:
        """Report whether one section is present and carries content.

        Treats a bare heading as absent, because a section with nothing under it documents no more
        than leaving it out and would otherwise satisfy the rule that demanded it.

        Arguments:
            section: Section name, including its colon.

        Returns:
            True when the section is present and has at least one content line.

        Raises:
            None.
        """
        return bool(self.sections.get(section))


def parse_docstring(text: str) -> Docstring:
    """Split a docstring into its title, description, and sections.

    Reads the first line as the title and every later non-empty line up to the first recognised
    section header as the description, collecting the lines that follow each header as its content.

    Arguments:
        text: The raw docstring.

    Returns:
        The parsed docstring.

    Raises:
        None.
    """
    lines = [line.strip() for line in text.strip().splitlines()]
    title = lines[0] if lines else ""

    description: list[str] = []
    sections: dict[str, tuple[str, ...]] = {}
    current: str | None = None

    for line in lines[1:]:
        if line in SECTION_NAMES:
            current = line
            sections[current] = ()
        elif not line:
            continue
        elif current is not None:
            sections[current] = (*sections[current], line)
        else:
            description.append(line)

    return Docstring(title=title, description=tuple(description), sections=sections)


def iter_python_files(paths: Sequence[str]) -> Iterator[Path]:
    """Yield every project Python file under the given roots.

    Walks each root in order, skipping generated and vendored directories, so the caller receives
    only files the project owns and is responsible for.

    Arguments:
        paths: Root directories or files, relative to the repository root.

    Yields:
        Each matching file, in walk order.

    Raises:
        FileNotFoundError: If a named root does not exist, which would otherwise turn the check off
            silently after a rename.
    """
    for entry in paths:
        root = REPOSITORY_ROOT / entry

        if root.is_file():
            yield root
            continue

        if not root.is_dir():
            message = f"no such path to check: {entry}"
            raise FileNotFoundError(message)

        for candidate in sorted(root.rglob("*.py")):
            if EXCLUDED_DIRECTORIES.isdisjoint(candidate.parts):
                yield candidate


def check_comments(path: Path, source: str) -> list[Violation]:
    """Reject every comment that is not an allowed pragma.

    Tokenises the source and matches each comment against the shape of a real directive rather than
    its opening word, so ordinary prose cannot ride in behind an allowed prefix.

    Arguments:
        path: File being checked.
        source: Full text of that file.

    Returns:
        Every comment violation found, in source order.

    Raises:
        SyntaxError: If the source cannot be tokenised.
    """
    violations: list[Violation] = []
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)

    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue

        body = token.string.lstrip("#").strip()
        if ALLOWED_PRAGMAS.match(body):
            continue

        violations.append(
            Violation(
                path=path,
                line=token.start[0],
                rule="comment",
                detail=f"code carries no comments; move this into a docstring: {token.string}",
            )
        )

    return violations


def check_description(path: Path, line: int, rule: str, docstring: Docstring) -> list[Violation]:
    """Check one docstring's title and description.

    Applies the two shared requirements every level carries, so a title or a description of the
    wrong shape is reported the same way wherever it appears.

    Arguments:
        path: File being checked.
        line: Line the documented construct starts at.
        rule: Rule identifier to report under.
        docstring: The parsed docstring.

    Returns:
        Every title or description violation found.

    Raises:
        None.
    """
    violations: list[Violation] = []

    if not docstring.title:
        violations.append(Violation(path, line, rule, "the docstring needs a one-line title"))

    described = len(docstring.description)
    if not MINIMUM_DESCRIPTION_LINES <= described <= MAXIMUM_DESCRIPTION_LINES:
        violations.append(
            Violation(
                path,
                line,
                rule,
                f"the description must be {MINIMUM_DESCRIPTION_LINES} to "
                f"{MAXIMUM_DESCRIPTION_LINES} lines, found {described}",
            )
        )

    return violations


def check_module(path: Path, tree: ast.Module) -> list[Violation]:
    """Check the file-level docstring.

    Confirms the module carries a docstring and that its title and description match the standard,
    since the file docstring is what a reader meets first.

    Arguments:
        path: File being checked.
        tree: Parsed module.

    Returns:
        Every module-level violation found.

    Raises:
        None.
    """
    text = ast.get_docstring(tree)
    if text is None:
        return [Violation(path, 1, "file-docstring", "the module carries no docstring")]

    return check_description(path, 1, "file-docstring", parse_docstring(text))


def own_statements(node: ast.AST) -> Iterator[ast.AST]:
    """Yield the statements belonging to one scope.

    Walks the node's own body without descending into a nested function, lambda, or class, so a
    rule about a construct is never triggered by code that merely lives inside it.

    Arguments:
        node: The construct whose scope is being read.

    Yields:
        Each statement in that scope, outermost first.

    Raises:
        None.
    """
    for child in ast.iter_child_nodes(node):
        yield child

        if not isinstance(child, NESTED_SCOPES):
            yield from own_statements(child)


def base_names(node: ast.ClassDef) -> list[str]:
    """List the short names of a class's bases.

    Reduces each base expression to the identifier a reader would recognise, so a subscripted or
    dotted base such as ``logging.Formatter`` is matched by its final segment.

    Arguments:
        node: The class definition.

    Returns:
        The short name of every base, in declaration order.

    Raises:
        None.
    """
    names: list[str] = []

    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)

    return names


def class_requires_attributes(node: ast.ClassDef) -> bool:
    """Report whether a class has attributes to document.

    Counts assignments in the class body and assignments onto the instance inside its own methods,
    and nothing else, so a local variable in a method never demands a section the class cannot fill.

    Arguments:
        node: The class definition.

    Returns:
        True when the class defines at least one attribute.

    Raises:
        None.
    """
    if any(isinstance(statement, ast.AnnAssign | ast.Assign) for statement in node.body):
        return True

    methods = [
        statement
        for statement in node.body
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef)
    ]

    return any(
        isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
        for method in methods
        for statement in own_statements(method)
        if isinstance(statement, ast.Assign)
        for target in statement.targets
    )


def check_class(path: Path, node: ast.ClassDef) -> list[Violation]:
    """Check one class docstring.

    Confirms the class names what it inherits, documents its attributes when it has any, and lists
    its members when it defines any, which is what makes a class readable from its docstring alone.

    Arguments:
        path: File being checked.
        node: The class definition.

    Returns:
        Every class-level violation found.

    Raises:
        None.
    """
    text = ast.get_docstring(node)
    if text is None:
        return [
            Violation(path, node.lineno, "class-docstring", f"class {node.name} has no docstring")
        ]

    docstring = parse_docstring(text)
    violations = check_description(path, node.lineno, "class-docstring", docstring)

    names = base_names(node)
    if names and not any(name in text for name in names):
        violations.append(
            Violation(
                path,
                node.lineno,
                "class-docstring",
                f"class {node.name} must name what it inherits: {', '.join(names)}",
            )
        )

    if class_requires_attributes(node) and not docstring.filled("Attributes:"):
        violations.append(
            Violation(
                path,
                node.lineno,
                "class-docstring",
                f"class {node.name} defines attributes and needs an Attributes section",
            )
        )

    defines_members = any(
        isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef) for statement in node.body
    )
    if defines_members and not docstring.filled("Members:"):
        violations.append(
            Violation(
                path,
                node.lineno,
                "class-docstring",
                f"class {node.name} defines members and needs a Members section",
            )
        )

    return violations


def check_callable(path: Path, node: Definition) -> list[Violation]:
    """Check one function or method docstring.

    Confirms the callable documents its arguments, what it produces, and what it raises when it
    raises anything, leaving the raises section out when there is nothing to put in it.

    Arguments:
        path: File being checked.
        node: The function or method definition.

    Returns:
        Every callable-level violation found.

    Raises:
        None.
    """
    text = ast.get_docstring(node)
    if text is None:
        return [
            Violation(
                path,
                node.lineno,
                "callable-docstring",
                f"callable {node.name} has no docstring",
            )
        ]

    docstring = parse_docstring(text)
    violations = check_description(path, node.lineno, "callable-docstring", docstring)

    statements = list(own_statements(node))
    yields = any(isinstance(statement, ast.Yield | ast.YieldFrom) for statement in statements)
    raises = any(isinstance(statement, ast.Raise) for statement in statements)

    required = ["Arguments:", "Yields:" if yields else "Returns:"]
    if raises:
        required.append("Raises:")

    violations.extend(
        Violation(
            path,
            node.lineno,
            "callable-docstring",
            f"callable {node.name} needs a {section[:-1]} section with content",
        )
        for section in required
        if not docstring.filled(section)
    )

    return violations


def check_file(path: Path) -> list[Violation]:
    """Check one file against every rule.

    Parses the file once and applies the comment rule and each docstring rule to it, reporting a
    file it cannot read or parse as a violation rather than letting it escape as a traceback.

    Arguments:
        path: File to check.

    Returns:
        Every violation found in that file, in source order.

    Raises:
        None.
    """
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        violations = check_comments(path, source)
    except (OSError, SyntaxError, UnicodeDecodeError) as error:
        return [Violation(path, 1, "unreadable", f"the file could not be checked: {error}")]

    violations.extend(check_module(path, tree))

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            violations.extend(check_class(path, node))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            violations.extend(check_callable(path, node))

    return sorted(violations, key=lambda violation: violation.line)


def format_violation(violation: Violation) -> str:
    """Render one violation as a report line.

    Prints the path relative to the repository root when possible, so the report reads the same
    wherever the checker is run from.

    Arguments:
        violation: The violation to render.

    Returns:
        The report line.

    Raises:
        None.
    """
    try:
        location = violation.path.relative_to(REPOSITORY_ROOT)
    except ValueError:
        location = violation.path

    return f"FAIL {location}:{violation.line} {violation.rule} {violation.detail}"


def main(argv: Sequence[str] | None = None) -> int:
    """Check every selected file and report the result.

    Parses the arguments, checks each file, prints one line per violation, and returns the exit
    code the quality gate and the pre-commit hook read.

    Arguments:
        argv: Command-line arguments, or None to read them from the process.

    Returns:
        Zero when every file complies, one when any violation was found.

    Raises:
        FileNotFoundError: If a selected root does not exist.
    """
    parser = argparse.ArgumentParser(description="Check the project documentation standard.")
    parser.add_argument("paths", nargs="*", default=list(DEFAULT_PATHS))
    arguments = parser.parse_args(argv)

    selected = arguments.paths or list(DEFAULT_PATHS)

    violations: list[Violation] = []
    for path in iter_python_files(selected):
        violations.extend(check_file(path))

    for violation in violations:
        print(format_violation(violation))

    if violations:
        print(f"{len(violations)} violation(s)")
        return EXIT_VIOLATIONS

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
