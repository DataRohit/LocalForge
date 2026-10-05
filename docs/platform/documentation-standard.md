# Documentation standard

Authoritative for: what a docstring in this repository must contain, which comments are permitted, and how both
are enforced.

The decision behind this is [../adr/0020-no-comments-structured-docstrings.md](../adr/0020-no-comments-structured-docstrings.md);
this file is the reference an author works from.

## 1. The rule in one line

**Project Python files carry no comments.** Every explanation lives in a docstring, and every module, class, and
callable has one — application code and tests alike.

## 2. Required sections

| Level | Must contain |
| --- | --- |
| File | One-line title, a blank line, then a description of 2–3 lines |
| Class | Title, 2–3 line description saying what it inherits, then `Attributes:` and `Members:` |
| Function / method | Title, 2–3 line description, then `Arguments:`, `Returns:`, and `Raises:` |

Two sections are conditional, so nothing carries an empty heading:

- `Raises:` is required when the callable raises, and otherwise optional — omit it, or state `None.` explicitly.
- `Attributes:` is required when the class defines attributes; `Members:` is required when it defines methods.
  Where neither applies, the section may be omitted or may state what the absence means.

The checker demands a section only when there is something to put in it, and it rejects a heading with nothing
underneath. It does not forbid an explicit statement of absence, which is the house style throughout this
repository: `Raises:` followed by `None.` says "this cannot fail" in a way that an omitted section cannot
distinguish from an author who forgot.

A generator documents what it produces under `Yields:` rather than `Returns:`, and the checker requires that
instead when the callable's own body yields.

A callable that takes no arguments still writes `Arguments:` with the single line `None.`, for the same reason.

The description says **why the thing exists, or what failure it prevents**. A description that restates the
signature satisfies no one — that is the failure mode this standard exists to stop, and 2–3 lines is the length at
which restating a signature becomes visibly inadequate.

## 3. One correct example per level

### 3.1 Module

```python
"""Structured log formatting.

Renders every log record as a single JSON object on one line, so the collector that ships container
stdout into the log store can index fields rather than parse prose.
"""
```

### 3.2 Class

```python
class StructuredFormatter(logging.Formatter):
    """Format log records as single-line JSON objects.

    Inherits from ``logging.Formatter`` and replaces its text rendering with a JSON document
    carrying the fields a log query needs, plus any extra attribute the caller attached to the
    record.

    Attributes:
        None beyond those the base formatter defines.

    Members:
        format: Render one record as a JSON document.
    """
```

### 3.3 Method

```python
    def format(self, record: logging.LogRecord) -> str:
        """Render one log record as a JSON document.

        Lays down any caller-supplied attributes first so the fields a log query depends on cannot
        be overwritten by one, appends exception and stack text, and falls back to a representation
        of each value when the document itself cannot be serialised.

        Arguments:
            record: The log record to render.

        Returns:
            The record as a single-line JSON document.

        Raises:
            None.
        """
```

## 4. Permitted comments

Only machine instructions, named individually rather than matched by pattern, so the exception cannot widen into
ordinary commentary:

| Prefix | Purpose |
| --- | --- |
| `# noqa`, `# noqa: E501` | Ruff suppression |
| `# type: ignore[...]` | Type-checker directive |
| `# pragma: no cover` | Coverage directive |
| `# ruff: noqa` | File-level Ruff configuration |
| `# mypy: ...` | File-level mypy configuration |

Each is matched against the **shape of the real directive**, not its opening word, so
`# pragma: an explanation smuggled in behind a prefix` is rejected like any other comment. Anything else is a
violation, including a commented-out line of code.

## 5. Enforcement

| Check | What it covers |
| --- | --- |
| Ruff `D` rules, google convention | Presence of docstrings, summary formatting, and that every named parameter appears under `Arguments:` |
| `backend/scripts/check_docstrings.py` | The comment ban and the section structure Ruff cannot express |

Both run in `make check` and in the pre-commit hooks. The checker's contract is in
[conventions.md](./conventions.md) Section 4.10.

## 6. What is out of scope

Generated and vendored code, excluded by path: anything under a `migrations/` directory, `__pycache__`, `.venv`,
and `.agents`. A Django migration is written by `makemigrations`, so holding it to a hand-written standard would
mean editing every generated file forever.
