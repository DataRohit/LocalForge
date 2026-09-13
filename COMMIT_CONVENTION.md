# Commit Convention

Every ordinary commit message uses this structure:

```text
Imperative summary in 72 characters or fewer

Description line one explaining intent or context
Description line two explaining impact or constraints
Optional third description line when more context is needed

- Completed work item
- Another completed work item
```

## Title

The title must be a single line, use the imperative mood, contain at most 72 characters, and not end in punctuation.
It should describe the result clearly when displayed as a GitHub commit title.

## Description

Leave one blank line after the title. Add exactly two or three non-empty description lines. Each body line must contain
at most 100 characters. Explain why the change was needed, the approach taken, and any meaningful consequence.

## Work items

Leave one blank line after the description. Add one or more non-empty bullets beginning with a hyphen and one space.
State concrete work that the commit completed. Do not add text after the bullet list.

## Special commits

Git-generated merge and revert messages are exempt. Fixup and squash commits must be autosquashed before integration.

## Local enforcement

The commit-message hook validates this structure. Enable the repository hooks and template with:

```console
pre-commit install
git config commit.template .gitmessage
```
