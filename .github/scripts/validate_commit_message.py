import re
import sys
from pathlib import Path

TITLE_MAX_LENGTH = 72
MIN_MESSAGE_LINE_COUNT = 6
BODY_MAX_LENGTH = 100
EXPECTED_ARGUMENT_COUNT = 2
FORBIDDEN_REFERENCES = re.compile(r"copilot|claude|codex", re.IGNORECASE)


def fail(errors: list[str]) -> int:
    details = "\n".join(f"- {error}" for error in errors)
    sys.stderr.write(
        f"Commit message validation failed:\n{details}\n"
        "See COMMIT_CONVENTION.md for the required format.\n"
    )
    return 1


def visible_lines(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.rstrip() for line in lines if not line.startswith("#")]


def validate(lines: list[str]) -> list[str]:
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
    if len(sys.argv) != EXPECTED_ARGUMENT_COUNT:
        return fail(["a commit message file path is required"])
    errors = validate(visible_lines(Path(sys.argv[1])))
    return fail(errors) if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
