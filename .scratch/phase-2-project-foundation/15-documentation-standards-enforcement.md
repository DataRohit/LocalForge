# 15: Documentation and comment standards, enforced

**What to build:** a mechanical gate that makes the project's documentation style impossible to drift from. Code
carries no comments, every module, class, and callable carries a structured docstring, and CI fails when one is
missing or malformed.

**Blocked by:**

- [13](13-dependency-baseline.md)

**Governing sources:**

- [Root instructions](../../AGENTS.md)
- [Build plan](../../docs/build/plan.md)
- [Platform conventions](../../docs/platform/conventions.md)
- [Documentation standard](../../docs/platform/documentation-standard.md)
- [Architecture decision index](../../docs/adr/README.md)

**Status:** done

- [x] The linter's docstring rules are switched on with a chosen convention, replacing the blanket ignore currently
      in place.
- [x] Missing docstrings on modules, classes, functions, and methods are errors, in application code and in tests
      alike.
- [x] A check rejects comment lines in project Python files, permitting only the pragmas the toolchain genuinely
      requires, which are enumerated.
- [x] A check enforces the section structure: file docstrings carry a one-line title and a two-to-three line
      description; class docstrings add what they inherit and their attributes and members; callable docstrings add
      arguments, returns, and raises.
- [x] A callable that raises nothing and a class with no attributes are handled without forcing empty sections.
- [x] The checks run in the existing quality gate and in the pre-commit hooks.
- [x] The standard is documented with one correct example each for a module, a class, and a method.
- [x] Every file the project already owns is brought into compliance in this ticket, so the gate starts green.
- [x] The checker is itself covered by tests, including a compliant file and one failing file per rule.
