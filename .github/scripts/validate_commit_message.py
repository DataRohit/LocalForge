"""Validate repository commit messages.

Checks the staged message's title length and punctuation, its two-or-three line description, its
work-item bullets, body line lengths, and the attribution rules, then reports every violation at
once so the author fixes the message in one edit.
"""

import re
import sys
from pathlib import Path

TITLE_MAX_LENGTH = 72
MIN_MESSAGE_LINE_COUNT = 6
BODY_MAX_LENGTH = 100
EXPECTED_ARGUMENT_COUNT = 2
FORBIDDEN_REFERENCES = re.compile(r"copilot|claude|codex", re.IGNORECASE)


def fail(errors: list[str]) -> int:
    """Print validation errors and return failure.

    Renders every collected problem in the hook's stderr output, so Git shows one actionable
    report instead of stopping at the first invalid line.

    Arguments:
        errors: Human-readable validation failures to print.

    Returns:
        The process exit code for a rejected message.
    """
    details = "\n".join(f"- {error}" for error in errors)
    sys.stderr.write(
        f"Commit message validation failed:\n{details}\n"
        "See COMMIT_CONVENTION.md for the required format.\n"
    )
    return 1


def visible_lines(path: Path) -> list[str]:
    """Read the user-authored message lines.

    Drops Git editor comments and trims only trailing whitespace, so the validator grades the
    message Git will keep without treating status prose as part of the commit.

    Arguments:
        path: Commit message file Git asked the hook to validate.

    Returns:
        The visible message lines in their original order.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.rstrip() for line in lines if not line.startswith("#")]


def validate(lines: list[str]) -> list[str]:
    """Validate one visible commit message.

    Applies the repository's title, description, bullet-list, line-length, and attribution rules,
    returning every violation so the author can fix the message in one edit.

    Arguments:
        lines: Visible message lines to validate.

    Returns:
        Human-readable validation failures, or an empty list when the message is valid.
    """
    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        return ["the message is empty"]
    title = lines[0]
    errors = []
    if any(FORBIDDEN_REFERENCES.search(line) for line in lines):
        errors.append("the message must not contain Copilot, Claude, or Codex references")
    if title.startswith(("Merge ", 'Revert "')):
        return errors
    if len(title) > TITLE_MAX_LENGTH:
        errors.append("the title must be 72 characters or fewer")
    if title.endswith((".", "!", "?", ":", ";")):
        errors.append("the title must not end with punctuation")
    if title.startswith(("fixup! ", "squash! ")):
        errors.append("fixup and squash commits must be autosquashed before integration")
    if len(lines) < MIN_MESSAGE_LINE_COUNT:
        errors.append("the body must contain two or three description lines and a bullet list")
        return errors
    if lines[1] != "":
        errors.append("a blank line must follow the title")
    bullet_start = next(
        (index for index in range(2, len(lines)) if lines[index].startswith("- ")), None
    )
    if bullet_start is None:
        errors.append("the body must end with one or more '- ' work-item bullets")
        return errors
    description = lines[2:bullet_start]
    if not description or description[-1] != "":
        errors.append("a blank line must separate the description from the bullet list")
    else:
        description = description[:-1]
    if len(description) not in (2, 3) or any(not line for line in description):
        errors.append("the description must contain exactly two or three non-empty lines")
    bullets = lines[bullet_start:]
    if any(not re.fullmatch(r"- \S.*", line) for line in bullets):
        errors.append("every line after the description must be a non-empty '- ' bullet")
    if any(len(line) > BODY_MAX_LENGTH for line in lines[2:]):
        errors.append("body lines must be 100 characters or fewer")
    return errors


def main() -> int:
    """Validate the commit message file Git passed to the hook.

    Rejects calls that do not name exactly one file, then reports the message-level validation
    failures through the same path used for content errors.

    Arguments:
        None.

    Returns:
        Zero when the message is valid, one when it is rejected.
    """
    if len(sys.argv) != EXPECTED_ARGUMENT_COUNT:
        return fail(["a commit message file path is required"])
    errors = validate(visible_lines(Path(sys.argv[1])))
    return fail(errors) if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
