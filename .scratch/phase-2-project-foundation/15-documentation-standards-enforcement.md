# 15: Documentation and comment standards, enforced

**What to build:** a mechanical gate that makes the project's documentation style impossible to drift from. Code
carries no comments, every module, class, and callable carries a structured docstring, and CI fails when one is
missing or malformed.

**Blocked by:** 13.

**Status:** ready-for-agent

- [ ] The linter's docstring rules are switched on with a chosen convention, replacing the blanket ignore currently
      in place.
- [ ] Missing docstrings on modules, classes, functions, and methods are errors, in application code and in tests
      alike.
- [ ] A check rejects comment lines in project Python files, permitting only the pragmas the toolchain genuinely
      requires, which are enumerated.
- [ ] A check enforces the section structure: file docstrings carry a one-line title and a two-to-three line
      description; class docstrings add what they inherit and their attributes and members; callable docstrings add
      arguments, returns, and raises.
- [ ] A callable that raises nothing and a class with no attributes are handled without forcing empty sections.
- [ ] The checks run in the existing quality gate and in the pre-commit hooks.
- [ ] The standard is documented with one correct example each for a module, a class, and a method.
- [ ] Every file the project already owns is brought into compliance in this ticket, so the gate starts green.
- [ ] The checker is itself covered by tests, including a compliant file and one failing file per rule.
