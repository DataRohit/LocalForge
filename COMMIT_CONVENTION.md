# Commit Convention

Every commit message uses a [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) title
and the following repository-specific body:

```text
type(scope): imperative summary

Description line one explaining intent or context
Description line two explaining impact or constraints
Optional third description line when more context is needed

- Completed work item
- Another completed work item
```

## Title

Use `type: summary` or `type(scope): summary`, with an optional `!` immediately before the colon for breaking changes.
The type must be one of `build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`, `style`, or `test`.
An optional scope starts with a lowercase letter or digit and contains only lowercase letters, digits, `.`, `_`, `/`,
or `-`.

The title must be a single line, use the imperative mood, contain at most 72 characters including the prefix, and not
end in punctuation. Use the standard colon syntax (`docs: ...`), not a parenthesized type (`(docs) ...`).

Examples:

- `docs: clarify local setup`
- `feat(admin): add account management`
- `fix(auth): reject expired sessions`
- `refactor(api)!: simplify the response format`

## Description

Leave one blank line after the title. Add exactly two or three non-empty description lines. Each body line must contain
at most 100 characters. Explain why the change was needed, the approach taken, and any meaningful consequence.

## Work items

Leave one blank line after the description. Add one or more non-empty bullets beginning with a hyphen and one space.
State concrete work that the commit completed. Do not add text after the bullet list.

## Attribution

Describe the repository change, its purpose, and completed work only. Messages must not contain agent references,
including Copilot, Claude, Codex, or agent/session credits. This applies to the title, description, and bullets,
regardless of capitalization. Omit co-author and generation trailers, including `Co-authored-by:`, `Generated-by:`,
and `Assisted-by:`.

## Repository identity

Use `Rohit Vilas Ingole <rohit.vilas.ingole@gmail.com>` for both the author and committer of every commit.
Configure this identity locally so other repositories retain their own settings. When rewriting history, update both
identities while preserving commit dates and file contents.

## Special commits

Merge and revert commits follow the same format; edit Git's generated messages before committing.
Use a `revert: ...` title for a revert. Fixup and squash commits must be autosquashed before integration.

## Local enforcement

The commit-message hook rejects Copilot, Claude, and Codex references regardless of capitalization, including in
co-author trailers and Git-generated merge or revert messages. It also validates ordinary commit bodies.
Follow the title and identity rules above when composing messages. Enable the repository hooks and template with:

```console
make pre-commit-install
git config --local commit.template .gitmessage
git config --local commit.cleanup strip
git config --local user.name "Rohit Vilas Ingole"
git config --local user.email rohit.vilas.ingole@gmail.com
```

Use `commit.cleanup strip` so comment lines beginning with `#`, which the validator ignores, are also removed by Git.
This keeps editor-generated status comments out of the committed message, including when using `git commit -m`.

Before finalizing a history rewrite, check every rewritten message against this policy and verify both identities
across all retained refs.
