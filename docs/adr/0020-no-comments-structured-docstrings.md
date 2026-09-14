---
status: accepted
date: 2026-09-13
---

# No code comments, structured docstrings everywhere

Project Python files contain **no comments**. Every explanation lives in a docstring, and docstrings follow a fixed
section structure at three levels. This applies to application code and tests alike, and it is enforced
mechanically rather than by review.

## The structure

The worked reference, with one correct example per level, is
[../platform/documentation-standard.md](../platform/documentation-standard.md).

| Level | Required sections |
|---|---|
| File | One-line title, then a 2–3 line description |
| Class | One-line title, 2–3 line description, what it inherits, its attributes and members |
| Function / method | One-line title, 2–3 line description, arguments, returns, raises |

A callable that raises nothing omits the raises section rather than carrying an empty one; the same applies to a
class with no attributes. The checker allows that; it does not force ceremony.

Clarified 2026-09-14, when the checker was written: "allows" is the operative word in both directions. The checker
demands a section only when the construct has something to put in it — a callable that raises gets a required
`Raises:`, a class with attributes a required `Attributes:` — and it rejects a heading with nothing underneath. It
does **not** forbid an explicit `None.`, which is the form this repository already uses throughout and which
distinguishes "this cannot fail" from an author who forgot. A generator documents `Yields:` in place of `Returns:`.

## Why a ban rather than a guideline

A comment and a docstring are not interchangeable. A docstring is reachable at runtime through `help()`, is
extracted by documentation tooling, is surfaced by editors and language servers at the call site, and is visible to
an agent reading a symbol without opening the file. A comment is visible only to whoever is already looking at that
line.

Allowing both produces a codebase where the explanation for a given behaviour is in one of two places depending on
who wrote it. Banning one removes the choice, which is the point: the rule exists to make the location of an
explanation predictable, not because comments are inherently bad.

The stated structure does the same job for the docstring's *contents*. "Document your functions" is a guideline
people satisfy with a restated signature. "Arguments, returns, raises, and a description that is two to three
lines" is checkable.

## Enforcement

A guideline nobody checks becomes a guideline nobody follows, so:

- The linter's docstring rules run with a chosen convention, replacing the blanket ignore currently in the
  repository configuration.
- Missing docstrings on modules, classes, functions, and methods are errors.
- A checker rejects comment lines in project Python files, allowing only the pragmas the toolchain genuinely needs,
  which are enumerated in one place.
- A checker enforces the section structure per level.
- Both run in the quality gate and in the pre-commit hooks.

The enumerated pragma exception matters: type-checker and linter directives are machine instructions, not prose,
and removing them would break the tooling. They are permitted by name rather than by pattern, so the exception
cannot widen into ordinary commentary.

## Considered options

**Comments allowed for "why", docstrings for "what".** The conventional split, and it fails the predictability
test: two authors disagree about which a given sentence is, and the explanation ends up in both places or neither.

**Docstrings required only on public symbols.** Cheaper, but this codebase is read by agents that reach a private
helper directly from a traceback or a symbol search, with no surrounding context. The distinction is not useful
here.

**Free-form docstrings with no required sections.** Rejected: unenforceable, and it reliably degrades into a
one-line restatement of the function name.

## Consequences

- Bringing existing files into compliance is part of the ticket that turns the rules on, so the gate starts green
  rather than accumulating a backlog.
- The checkers are project code and therefore need their own tests, including a compliant file and one failing file
  per rule.
- Generated files and third-party vendored code are out of scope and excluded by path.
