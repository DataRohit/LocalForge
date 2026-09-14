"""Environment file generation for the local backend platform.

Creates or tops up the per-environment files Compose loads, filling every absent variable from the
committed manifest and every secret from a cryptographically secure source, so a fresh clone reaches
a working configuration without anyone typing a credential.
"""

import argparse
import secrets
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import bcrypt

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPOSITORY_ROOT / ".env.example"
GENERATED_PLACEHOLDER = "<GENERATED>"
COMMAND_TIMEOUT_SECONDS = 30

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_MANIFEST_UNUSABLE = 2
EXIT_TRACKED_FILE = 3
EXIT_FORCE_WITHOUT_ENVIRONMENT = 4

EMPTY_INDEX = frozenset[str]()
INVALID_VALUES = ("", GENERATED_PLACEHOLDER)

SECRET_KEY_BYTES = 64
PASSWORD_BYTES = 32
ACCESS_KEY_BYTES = 20
BCRYPT_ROUNDS = 12
BASIC_AUTH_USER = "admin"
BCRYPT_MAXIMUM_BYTES = 72
MINIMUM_QUOTED_LENGTH = 2

DEVELOPMENT = "development"
TESTING = "testing"
TESTING_HOST = "testing-host"
ALL_ENVIRONMENTS = "all"

CREDENTIAL_DERIVED_VOLUMES: Mapping[str, tuple[str, ...]] = {
    DEVELOPMENT: (
        "postgres-pg3ka-data",
        "postgres-replica-pg6vy-data",
        "rabbitmq-rq4sx-data",
        "grafana-gf7qv-data",
        "pgadmin-pa7fe-data",
    ),
    TESTING: (
        "postgres-tp8vn-data",
        "rabbitmq-tr6mc-data",
    ),
    TESTING_HOST: (
        "postgres-tp8vn-data",
        "rabbitmq-tr6mc-data",
    ),
}

TESTING_OVERRIDES: Mapping[str, str] = {
    "COMPOSE_PROJECT_NAME": "localforge-test",
    "DJANGO_SETTINGS_MODULE": "config.settings.testing",
    "DJANGO_DEBUG": "false",
    "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1,django-test-dt5qx",
    "POSTGRES_HOST": "postgres-tp8vn",
    "POSTGRES_REPLICA_HOST": "postgres-tp8vn",
    "VALKEY_CACHE_HOST": "valkey-cache-tv4kq",
    "VALKEY_CHANNELS_HOST": "valkey-channels-tv9zw",
    "RABBITMQ_HOST": "rabbitmq-tr6mc",
    "S3_ENDPOINT_URL": "http://seaweedfs-ts3jd:8333",
    "EMAIL_HOST": "mailpit-tm7bh",
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "DJANGO_SITE_URL": "http://localhost:8000",
    "CELERY_TASK_ALWAYS_EAGER": "true",
}

TESTING_HOST_OVERRIDES: Mapping[str, str] = {
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": "25432",
    "POSTGRES_REPLICA_HOST": "127.0.0.1",
    "POSTGRES_REPLICA_PORT": "25432",
    "VALKEY_CACHE_HOST": "127.0.0.1",
    "VALKEY_CACHE_PORT": "26379",
    "VALKEY_CHANNELS_HOST": "127.0.0.1",
    "VALKEY_CHANNELS_PORT": "26380",
    "RABBITMQ_HOST": "127.0.0.1",
    "RABBITMQ_PORT": "25672",
    "S3_ENDPOINT_URL": "http://127.0.0.1:28333",
    "EMAIL_HOST": "127.0.0.1",
    "EMAIL_PORT": "21025",
    "MAILPIT_WEB_PORT": "28025",
}

ENVIRONMENT_FILES: Mapping[str, str] = {
    DEVELOPMENT: ".env.development",
    TESTING: ".env.testing",
    TESTING_HOST: ".env.testing.host",
}

NO_OVERRIDES = dict[str, str]()

ENVIRONMENT_OVERRIDES: Mapping[str, Mapping[str, str]] = {
    DEVELOPMENT: NO_OVERRIDES,
    TESTING: TESTING_OVERRIDES,
    TESTING_HOST: {**TESTING_OVERRIDES, **TESTING_HOST_OVERRIDES},
}

SELECTIONS: Mapping[str, tuple[str, ...]] = {
    DEVELOPMENT: (DEVELOPMENT,),
    TESTING: (TESTING, TESTING_HOST),
    ALL_ENVIRONMENTS: (DEVELOPMENT, TESTING, TESTING_HOST),
}

SHARING_GROUPS: tuple[tuple[str, ...], ...] = (
    (DEVELOPMENT,),
    (TESTING, TESTING_HOST),
)


class RefusalError(Exception):
    """Raised when the existing files are in a state the generator will not resolve.

    Covers a composed value that disagrees with its components and a sharing group whose members
    hold different credentials, both of which need a person to decide rather than a default.
    Inherits Exception; it carries only the message its constructor is given.
    """


class ManifestError(Exception):
    """Raised when the committed variable manifest cannot be used.

    Signals that the manifest is absent or carries a line the parser cannot read, which is the one
    condition that stops generation before any file is touched. Inherits Exception; it carries only
    the message its constructor is given.
    """


def generate_secret_key() -> str:
    """Generate the Django signing key.

    Produces the widest secret the platform uses, because the signing key protects sessions,
    password reset tokens, and every other signed value Django issues.

    Arguments:
        None.

    Returns:
        A URL-safe token holding the documented number of random bytes.
    """
    return secrets.token_urlsafe(SECRET_KEY_BYTES)


def generate_password() -> str:
    """Generate a service password.

    Produces the default secret for every service credential, so no password is ever chosen by a
    person or shared between two services.

    Arguments:
        None.

    Returns:
        A URL-safe token holding the documented number of random bytes.
    """
    return secrets.token_urlsafe(PASSWORD_BYTES)


def generate_access_key() -> str:
    """Generate an object storage key.

    Produces a hexadecimal token, because S3 access keys and secrets are conventionally hexadecimal
    and some clients reject the URL-safe alphabet.

    Arguments:
        None.

    Returns:
        A hexadecimal token holding the documented number of random bytes.
    """
    return secrets.token_hex(ACCESS_KEY_BYTES)


def generate_plain_auth() -> str:
    """Generate a plaintext basic-authentication credential.

    Produces a user and password pair, because the task dashboard compares the configured value
    literally rather than as a hash, so hashing it would make the digest itself the password.

    Arguments:
        None.

    Returns:
        A single user and password pair separated by a colon.
    """
    return f"{BASIC_AUTH_USER}:{generate_password()}"


SECRET_RECIPES: Mapping[str, Callable[[], str]] = {
    "DJANGO_SECRET_KEY": generate_secret_key,
    "S3_ACCESS_KEY_ID": generate_access_key,
    "S3_SECRET_ACCESS_KEY": generate_access_key,
    "FLOWER_BASIC_AUTH": generate_plain_auth,
}


def compose_broker_url(values: Mapping[str, str]) -> str:
    """Compose the Celery broker URL.

    Assembles the AMQP URL from the broker credentials and location already in the file, so the
    broker password exists in exactly one generated place and the URL follows it.

    Arguments:
        values: Variables resolved so far for this environment.

    Returns:
        The AMQP URL for the configured broker.
    """
    user = values["RABBITMQ_DEFAULT_USER"]
    password = values["RABBITMQ_DEFAULT_PASS"]
    host = values["RABBITMQ_HOST"]
    port = values["RABBITMQ_PORT"]
    vhost = values["RABBITMQ_DEFAULT_VHOST"]

    return f"amqp://{user}:{password}@{host}:{port}/{vhost}"


def compose_result_backend(values: Mapping[str, str]) -> str:
    """Compose the Celery result backend URL.

    Points the result backend at the cache instance's results database, which must differ from the
    cache database because clearing the cache issues a database-wide flush.

    Arguments:
        values: Variables resolved so far for this environment.

    Returns:
        The Valkey URL for the Celery result backend.
    """
    password = values["VALKEY_CACHE_PASSWORD"]
    host = values["VALKEY_CACHE_HOST"]
    port = values["VALKEY_CACHE_PORT"]
    database = values["VALKEY_RESULTS_DB"]

    return f"redis://:{password}@{host}:{port}/{database}"


def compose_dashboard_auth(values: Mapping[str, str]) -> str:
    """Compose the edge proxy's basic-authentication entry.

    Hashes the generated dashboard password into the htpasswd form the proxy expects, so the proxy
    stores only a digest while the password itself stays readable to the developer who must log in.

    Arguments:
        values: Variables resolved so far for this environment.

    Returns:
        A single htpasswd entry pairing the fixed user with a bcrypt hash of the password.

    Raises:
        RefusalError: If the password is longer than the hash can carry.
    """
    password = values["TRAEFIK_DASHBOARD_PASSWORD"].encode()
    if len(password) > BCRYPT_MAXIMUM_BYTES:
        message = (
            f"TRAEFIK_DASHBOARD_PASSWORD is longer than {BCRYPT_MAXIMUM_BYTES} bytes, "
            "which bcrypt cannot hash; shorten it or remove it to have one generated"
        )
        raise RefusalError(message)

    digest = bcrypt.hashpw(password, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()

    return f"{BASIC_AUTH_USER}:{digest}"


def verify_dashboard_auth(values: Mapping[str, str], current: str) -> bool:
    """Check an existing basic-authentication entry against its password.

    Verifies rather than recomputes, because the hash carries a random salt and a fresh one never
    equals the stored entry. Also rejects an entry naming another user or hashed at another cost,
    so a weakened or hand-edited credential is rebuilt instead of preserved.

    Arguments:
        values: Variables resolved so far for this environment.
        current: The entry already written to the file.

    Returns:
        True when the entry is this password hashed for the expected user at the expected cost.
    """
    password = values.get("TRAEFIK_DASHBOARD_PASSWORD", "")
    user, separator, digest = current.partition(":")
    if not separator or not password or user != BASIC_AUTH_USER:
        return False

    if not digest.startswith(f"$2b${BCRYPT_ROUNDS:02d}$"):
        return False

    try:
        return bcrypt.checkpw(password.encode(), digest.encode())
    except ValueError:
        return False


COMPOSED_VERIFIERS: Mapping[str, Callable[[Mapping[str, str], str], bool]] = {
    "TRAEFIK_DASHBOARD_AUTH": verify_dashboard_auth,
}

COMPOSED_INPUTS: Mapping[str, tuple[str, ...]] = {
    "TRAEFIK_DASHBOARD_AUTH": ("TRAEFIK_DASHBOARD_PASSWORD",),
}


COMPOSED_VALUES: Mapping[str, Callable[[Mapping[str, str]], str]] = {
    "CELERY_BROKER_URL": compose_broker_url,
    "CELERY_RESULT_BACKEND": compose_result_backend,
    "TRAEFIK_DASHBOARD_AUTH": compose_dashboard_auth,
}


class IndexUnavailableError(Exception):
    """Raised when version control cannot say whether a file is tracked.

    Separates a verified empty index from an index that could not be read, so the generator can
    refuse rather than assume a file is safe to write. Inherits Exception; it carries only the
    message its constructor is given.
    """


class VersionControl(Protocol):
    """Query surface for the repository's version control index.

    Isolates the one question generation needs to ask of Git, so the refusal to overwrite a tracked
    file can be exercised without a repository. Inherits Protocol, so any object providing the
    method satisfies it structurally.

    Members:
        tracked_files: Report the paths Git currently tracks.
    """

    def tracked_files(self) -> frozenset[str]:
        """Report the paths Git currently tracks.

        Lists the index relative to the repository root, which is what determines whether writing a
        generated file would put a secret under version control.

        Arguments:
            None.

        Returns:
            Repository-relative paths, with forward slashes.

        Raises:
            IndexUnavailableError: If the index exists but could not be read.
        """


class GitIndex:
    """Version control queries backed by the real Git index.

    Implements the VersionControl surface by asking Git directly, distinguishing a non-repository
    where nothing can be tracked from a repository whose tracking status is unknown. Inherits
    nothing and satisfies VersionControl structurally.

    Attributes:
        root: Repository whose index is queried.

    Members:
        __init__: Bind the query surface to one repository.
        tracked_files: Report the paths Git currently tracks.
    """

    def __init__(self, root: Path = REPOSITORY_ROOT) -> None:
        """Bind the query surface to one repository.

        Takes the repository explicitly so a caller generating into a different tree asks that
        tree's index rather than this module's own.

        Arguments:
            root: Repository whose index should be queried.

        Returns:
            None.
        """
        self.root = root

    def tracked_files(self) -> frozenset[str]:
        """Report the paths Git currently tracks.

        Answers an empty index only outside a repository, where no file can be tracked.
        Raises for unreadable repositories, including absent Git, because a later commit could
        still track the file and an unreadable index is not evidence that writing is safe.

        Arguments:
            None.

        Returns:
            Repository-relative paths, with forward slashes.

        Raises:
            IndexUnavailableError: If this is a repository whose index could not be read.
        """
        if not (self.root / ".git").exists():
            return EMPTY_INDEX

        git = shutil.which("git")
        if git is None:
            message = "git is not installed, so the index of this repository cannot be read"
            raise IndexUnavailableError(message)

        try:
            completed = subprocess.run(
                [git, "ls-files"],
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
                check=False,
                cwd=self.root,
            )
        except OSError as error:
            message = f"git could not be run: {error}"
            raise IndexUnavailableError(message) from error
        except subprocess.SubprocessError as error:
            message = f"git did not complete: {error}"
            raise IndexUnavailableError(message) from error

        if completed.returncode != 0:
            message = f"git exited {completed.returncode}"
            raise IndexUnavailableError(message)

        tracked: frozenset[str] = frozenset(line.strip() for line in completed.stdout.splitlines())

        return frozenset(path for path in tracked if path)


@dataclass(frozen=True, slots=True)
class Outcome:
    """Result of generating one environment file.

    Reports what changed without naming a single value, so the summary can be printed and logged
    safely. Inherits nothing; it is a plain frozen data holder with no methods.

    Attributes:
        path: Repository-relative path of the file written.
        added: Names of variables written for the first time.
        kept: Number of variables left exactly as they were.
        regenerated: Names of variables replaced because regeneration was forced.
    """

    path: str
    added: tuple[str, ...]
    kept: int
    regenerated: tuple[str, ...]


def unquote(value: str) -> str:
    """Strip the quoting an environment file applies to a value.

    Removes a surrounding pair of single quotes, which is how a value containing a dollar sign is
    protected from expansion, so a value read back compares equal to the value written.

    Arguments:
        value: Raw value as it appears in the file.

    Returns:
        The value with one surrounding pair of single quotes removed.
    """
    if len(value) >= MINIMUM_QUOTED_LENGTH and value.startswith("'") and value.endswith("'"):
        return value[1:-1]

    return value


def parse_env_text(text: str) -> dict[str, str]:
    """Read an environment file into variables.

    Ignores blank lines and comments and splits each remaining line on its first equals sign, which
    is the form Compose itself accepts.

    Arguments:
        text: Contents of an environment file.

    Returns:
        The variables in the order they appeared.

    Raises:
        ManifestError: If a non-comment line carries no equals sign.
    """
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            message = f"line {number} is not a variable assignment"
            raise ManifestError(message)

        name, _, value = line.partition("=")
        values[name.strip()] = unquote(value.strip())

    return values


def read_manifest(path: Path) -> dict[str, str]:
    """Read the committed variable manifest.

    Treats the manifest as the single list of variables every environment carries, so a variable
    added to the inventory reaches every generated file without the script naming it.

    Arguments:
        path: Path to the manifest.

    Returns:
        Every manifest variable with its development value or generation placeholder.

    Raises:
        ManifestError: If the manifest is absent, unreadable, or unparsable.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        message = f"{path} could not be read: {error}"
        raise ManifestError(message) from error

    values = parse_env_text(text)
    if not values:
        message = f"{path} declares no variables"
        raise ManifestError(message)

    return values


def render_value(name: str, value: str) -> str:
    """Render one value for an environment file.

    Single-quotes values containing dollar signs because Compose expands unquoted env_file values,
    so an unquoted bcrypt hash such as admin:$2b$12$abc would lose everything from the third dollar.
    The quotes suppress expansion and are stripped when the file is read.

    Arguments:
        name: Variable the value belongs to, named in any refusal.
        value: Value to render.

    Returns:
        The value, quoted when it would otherwise be expanded.

    Raises:
        ManifestError: If the value carries a single quote, which cannot be quoted this way.
    """
    if "$" not in value:
        return value

    if "'" in value:
        message = f"{name} contains both a dollar sign and a single quote, which cannot be written"
        raise ManifestError(message)

    return f"'{value}'"


def render_env_text(values: Mapping[str, str]) -> str:
    """Render variables as an environment file.

    Writes one assignment per line in manifest order and nothing else. The file carries no comment
    and no blank line, because the dotenv encryption format drops blank lines, and because a file
    of credentials should contain only the credentials it is read for.

    Arguments:
        values: Variables to write.

    Returns:
        The full text of the environment file.

    Raises:
        ManifestError: If a value cannot be written safely.
    """
    return "\n".join(f"{name}={render_value(name, value)}" for name, value in values.items()) + "\n"


def secret_names(manifest: Mapping[str, str]) -> tuple[str, ...]:
    """List the variables the manifest marks as secret.

    Reads the placeholder rather than a hardcoded list, so marking a new variable secret in the
    manifest is enough to have it generated.

    Arguments:
        manifest: Variables read from the manifest.

    Returns:
        The names whose manifest value is the generation placeholder.
    """
    return tuple(name for name, value in manifest.items() if value == GENERATED_PLACEHOLDER)


def groups_for(environments: Sequence[str]) -> list[tuple[str, ...]]:
    """Select the sharing groups a run must resolve together.

    Includes every member of a group whose environment was selected, so generating one member
    always reads its siblings and cannot leave the pair holding different credentials.

    Arguments:
        environments: Environments the run was asked to generate.

    Returns:
        The sharing groups covering those environments, in registry order.
    """
    selected = set(environments)

    return [group for group in SHARING_GROUPS if selected.intersection(group)]


def apply_composed(resolved: dict[str, str], *, force: bool) -> None:
    """Reconcile every composed value with the variables it is built from.

    Derives composed values for absent placeholders and forced runs, then preserves entries that
    already verify against their inputs. A present real value that disagrees is refused rather than
    rewritten, because the generator cannot prove whether it is stale or deliberate.

    Arguments:
        resolved: Variables resolved so far, updated in place.
        force: Whether existing values are being replaced.

    Returns:
        None.

    Raises:
        RefusalError: If a composed value was written by hand and disagrees with its components.
    """
    for name, composer in COMPOSED_VALUES.items():
        if name not in resolved:
            continue

        current = resolved[name]
        verifier = COMPOSED_VERIFIERS.get(name)
        if verifier is not None and is_usable(current) and verifier(resolved, current):
            continue

        derived = composer(resolved)
        if derived == current:
            continue

        if is_usable(current) and not force:
            message = (
                f"{name} does not match the variables it is built from; "
                "remove it to have it rebuilt, or regenerate with --force"
            )
            raise RefusalError(message)

        resolved[name] = derived


def resolve_values(
    manifest: Mapping[str, str],
    overrides: Mapping[str, str],
    existing: Mapping[str, str],
    generated: Mapping[str, str],
    *,
    force: bool,
) -> tuple[dict[str, str], list[str], list[str]]:
    """Decide the final value of every variable for one environment.

    Keeps existing values unless regeneration is forced, fills absent values from defaults, and
    preserves variables outside the manifest because the generator owns only known names.
    Re-derives composed values, correcting changed inputs while refusing unproven mismatches.

    Arguments:
        manifest: Variables read from the manifest.
        overrides: Environment-specific values replacing the manifest defaults.
        existing: Variables already present in the target file.
        generated: Secrets shared across this environment group.
        force: Whether to replace existing values rather than keep them.

    Returns:
        The resolved variables, the names newly added, and the names regenerated.

    Raises:
        RefusalError: If a composed value disagrees with its components on a default run.
    """
    defaults = {**manifest, **overrides}
    resolved: dict[str, str] = {}
    added: list[str] = []
    regenerated: list[str] = []

    for name, default in defaults.items():
        fallback = generated.get(name, default)
        held = existing.get(name)
        absent = held is None or (name in generated and not is_usable(held))
        if absent:
            resolved[name] = fallback
            added.append(name)
        elif force:
            resolved[name] = fallback
            regenerated.append(name)
        else:
            resolved[name] = existing[name]

    for name, value in existing.items():
        if name not in resolved:
            resolved[name] = value

    apply_composed(resolved, force=force)

    return resolved, added, regenerated


def read_existing(path: Path) -> dict[str, str]:
    """Read an environment file that may not exist yet.

    Treats an absent file as an empty set of variables, which is the ordinary state of a fresh
    clone and not an error, and reports a file that exists but cannot be read as a manifest
    problem rather than letting the operating system error escape.

    Arguments:
        path: Path to the environment file.

    Returns:
        The variables the file declares, or nothing when it does not exist.

    Raises:
        ManifestError: If the file exists but cannot be read or parsed.
    """
    if not path.exists():
        return {}

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        message = f"{path.name} could not be read: {error}"
        raise ManifestError(message) from error

    return parse_env_text(text)


def is_usable(value: str | None) -> bool:
    """Decide whether a stored value can be kept.

    Rejects an empty value and the manifest placeholder, because both mean the variable has no
    credential yet even though the line exists, and propagating either would hand a service a
    password it cannot authenticate with.

    Arguments:
        value: Value read from a file, or None when the variable was absent.

    Returns:
        True when the value is a real one, and False when it is absent, empty, or a placeholder.
    """
    return value is not None and value not in INVALID_VALUES


def group_secrets(
    manifest: Mapping[str, str],
    existing_by_environment: Mapping[str, Mapping[str, str]],
) -> dict[str, str]:
    """Decide the secrets an environment group shares.

    Prefers a credential any member of the group already holds, so regenerating one file because
    its sibling was deleted cannot leave the pair addressing the same containers with different
    passwords, and generates a fresh value only where the group holds none.

    Arguments:
        manifest: Variables read from the manifest.
        existing_by_environment: Variables already on disk, keyed by environment.

    Returns:
        One value per secret variable, shared by every member of the group.

    Raises:
        RefusalError: If two members of the group hold different values for one secret, which
            cannot be reconciled without choosing one credential over another.
    """
    shared: dict[str, str] = {}
    for name in secret_names(manifest):
        if name in COMPOSED_VALUES:
            continue

        held = {
            values[name]
            for values in existing_by_environment.values()
            if is_usable(values.get(name))
        }
        if len(held) > 1:
            message = (
                f"{name} differs between files that must share it; "
                "reconcile them by hand, or regenerate the pair with --force"
            )
            raise RefusalError(message)

        shared[name] = held.pop() if held else SECRET_RECIPES.get(name, generate_password)()

    return shared


@dataclass(frozen=True, slots=True)
class Prepared:
    """One environment file resolved but not yet written.

    Separates deciding what a file should contain from putting it on disk, so a run that fails
    partway leaves every file exactly as it was. Inherits nothing; it is a plain frozen data holder
    with no methods.

    Attributes:
        path: Absolute path the text will be written to.
        text: Full contents to write.
        outcome: What writing this file will have changed.
    """

    path: Path
    text: str
    outcome: Outcome


def share_composed(shared: dict[str, str], existing: Mapping[str, Mapping[str, str]]) -> None:
    """Compose the group's non-deterministic values once.

    Derives values whose composition carries randomness before the group's files are resolved, so
    siblings that must hold identical credentials do not each generate a different one, and adopts
    a surviving sibling's value rather than replacing one that is still correct.

    Arguments:
        shared: Secrets shared across the group, updated in place.
        existing: Values already held by each environment in the group.

    Returns:
        None.

    Raises:
        RefusalError: If two members of the group hold different values for one composed credential.
    """
    for name, verifier in COMPOSED_VERIFIERS.items():
        held = {
            values[name]
            for values in existing.values()
            if is_usable(values.get(name)) and verifier(shared, values[name])
        }
        if len(held) > 1:
            message = (
                f"{name} differs between files that must share it; "
                "reconcile them by hand, or regenerate the pair with --force"
            )
            raise RefusalError(message)

        current = shared.get(name, "")
        if is_usable(current) and verifier(shared, current):
            continue

        if held:
            shared[name] = held.pop()
            continue

        if any(required not in shared for required in COMPOSED_INPUTS[name]):
            continue

        shared[name] = COMPOSED_VALUES[name](shared)


def prepare_group(
    environments: Sequence[str],
    manifest: Mapping[str, str],
    root: Path,
    *,
    force: bool,
) -> list[Prepared]:
    """Resolve every environment file in one sharing group.

    Reads all members before resolving any, so the credentials they must agree on are decided once
    for the group rather than independently per file, and returns the rendered text rather than
    writing it so the caller can validate the whole run first.

    Arguments:
        environments: Environments belonging to one sharing group.
        manifest: Variables read from the manifest.
        root: Repository root holding the environment files.
        force: Whether to replace existing values rather than keep them.

    Returns:
        One prepared file per environment, in the order given.

    Raises:
        ManifestError: If an existing file cannot be read.
        RefusalError: If the group's members disagree on a shared credential, or a composed value
            disagrees with its components.
    """
    existing_by_environment = {
        environment: read_existing(root / ENVIRONMENT_FILES[environment])
        for environment in environments
    }
    shared = group_secrets(manifest, {} if force else existing_by_environment)
    share_composed(shared, {} if force else existing_by_environment)

    prepared: list[Prepared] = []
    for environment in environments:
        relative = ENVIRONMENT_FILES[environment]
        resolved, added, regenerated = resolve_values(
            manifest,
            ENVIRONMENT_OVERRIDES[environment],
            existing_by_environment[environment],
            shared,
            force=force,
        )
        prepared.append(
            Prepared(
                path=root / relative,
                text=render_env_text(resolved),
                outcome=Outcome(
                    path=relative,
                    added=tuple(added),
                    kept=len(resolved) - len(added) - len(regenerated),
                    regenerated=tuple(regenerated),
                ),
            ),
        )

    return prepared


def tracked_refusals(
    environments: Sequence[str],
    version_control: VersionControl,
) -> tuple[str, ...]:
    """List the target files Git already tracks.

    Refuses before writing rather than after, because a generated secret committed once stays in
    history whatever is done to the working tree afterwards.

    Arguments:
        environments: Environments about to be written.
        version_control: Index query surface.

    Returns:
        The repository-relative paths that are tracked and must not be written.

    Raises:
        IndexUnavailableError: If the index could not be read, leaving the question unanswered.
    """
    tracked = version_control.tracked_files()

    return tuple(
        ENVIRONMENT_FILES[environment]
        for environment in environments
        if ENVIRONMENT_FILES[environment] in tracked
    )


def commit_prepared(prepared: Sequence[Prepared]) -> list[Outcome]:
    """Put every prepared file on disk, or leave every file as it was.

    Stages each file beside its destination first, so a failure while writing leaves the originals
    untouched, then replaces the destinations atomically and restores any already-replaced file if
    a later replacement fails.

    Arguments:
        prepared: Files resolved and rendered but not yet written.

    Returns:
        A description of what each file changed.

    Raises:
        ManifestError: If a file could not be staged or replaced.
    """
    staged: list[tuple[Path, Path]] = []
    try:
        for file in prepared:
            temporary = file.path.with_name(f"{file.path.name}.partial")
            temporary.write_text(file.text, encoding="utf-8", newline="\n")
            staged.append((temporary, file.path))
    except OSError as error:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)

        message = f"could not stage the new files: {error}"
        raise ManifestError(message) from error

    originals = {
        destination: destination.read_bytes() for _, destination in staged if destination.exists()
    }
    replaced: list[Path] = []
    try:
        for temporary, destination in staged:
            temporary.replace(destination)
            replaced.append(destination)
    except OSError as error:
        for destination in replaced:
            if destination in originals:
                destination.write_bytes(originals[destination])
            else:
                destination.unlink(missing_ok=True)

        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)

        message = f"could not replace the existing files: {error}"
        raise ManifestError(message) from error

    return [file.outcome for file in prepared]


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Leaves the environment selection without a default, so forcing regeneration without naming an
    environment is a distinguishable error rather than a silent regeneration of everything.

    Arguments:
        None.

    Returns:
        The configured argument parser.
    """
    parser = argparse.ArgumentParser(
        prog="gen_secrets",
        description="Create or top up the per-environment files Compose loads.",
    )
    parser.add_argument(
        "--environment",
        choices=sorted(SELECTIONS),
        default=None,
        help="environment to generate; omit to generate every environment",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace existing values instead of keeping them",
    )

    return parser


def report(
    outcomes: Sequence[Outcome],
    environments: Sequence[str],
    *,
    force: bool,
) -> str:
    """Summarise a run without disclosing a value.

    Names the variables that changed and counts those left alone, and warns about the volumes whose
    contents derive from credentials when regeneration was forced, scoped to the environments the
    run actually touched.

    Arguments:
        outcomes: What each environment file run produced.
        environments: Environments the run generated.
        force: Whether values were replaced rather than kept.

    Returns:
        The summary to print.
    """
    lines: list[str] = []
    for outcome in outcomes:
        lines.append(
            f"{outcome.path}: {len(outcome.added)} added, "
            f"{outcome.kept} kept, {len(outcome.regenerated)} regenerated",
        )
        if outcome.added:
            lines.append(f"  added: {', '.join(outcome.added)}")

    if force:
        volumes = sorted(
            {
                volume
                for environment in environments
                for volume in CREDENTIAL_DERIVED_VOLUMES[environment]
            },
        )
        lines.extend(
            (
                "",
                "Forced regeneration rotates every credential in the files, but a service that is",
                "already running keeps the one it started with, and a stateful service keeps it",
                "inside its data directory where recreating the container does not reach it.",
                "Nothing reports this: every health check still passes and only an authenticated",
                "connection fails. Stop the stack, recreate these volumes, then start it again:",
            ),
        )
        lines.extend(f"  {volume}" for volume in volumes)

    return "\n".join(lines)


def preflight_request(
    arguments: argparse.Namespace,
    root: Path,
    index: VersionControl,
    environments: Sequence[str],
) -> int:
    """Check everything that must hold before any file is considered.

    Refuses a run whose targets are tracked, or whose tracking status cannot be established, so the
    decision is made once and before any file is read or written.

    Arguments:
        arguments: Parsed command-line arguments.
        root: Repository root holding the environment files.
        index: Version control query surface.
        environments: Environments the run will write.

    Returns:
        Zero when the run may proceed, or the documented failure code.
    """
    del arguments, root

    try:
        refusals = tracked_refusals(environments, index)
    except IndexUnavailableError as error:
        print(f"refusing to write: cannot determine what Git tracks: {error}")
        return EXIT_TRACKED_FILE

    if refusals:
        print(f"refusing to write Git-tracked file(s): {', '.join(refusals)}")
        return EXIT_TRACKED_FILE

    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    root: Path = REPOSITORY_ROOT,
    version_control: VersionControl | None = None,
) -> int:
    """Generate the environment files.

    Validates the request, refuses to write anything Git tracks or anything whose tracking status
    cannot be established, then generates each selected sharing group so the files that must agree
    on a credential are resolved together.

    Arguments:
        argv: Command-line arguments, or None to read them from the process.
        root: Repository root holding the manifest and the environment files.
        version_control: Index query surface, or None to query the repository at root.

    Returns:
        Zero on success, or the documented failure code.
    """
    arguments = build_parser().parse_args(argv)
    if arguments.force and arguments.environment is None:
        print("--force requires --environment, so a forced run cannot be a slip of the keyboard")
        return EXIT_FORCE_WITHOUT_ENVIRONMENT

    index = version_control if version_control is not None else GitIndex(root)

    try:
        manifest = read_manifest(root / MANIFEST_PATH.name)
    except ManifestError as error:
        print(f"manifest unusable: {error}")
        return EXIT_MANIFEST_UNUSABLE

    selection = SELECTIONS[arguments.environment or ALL_ENVIRONMENTS]
    groups = groups_for(selection)
    environments = [environment for group in groups for environment in group]

    blocked = preflight_request(arguments, root, index, environments)
    if blocked != EXIT_OK:
        return blocked

    outcomes: list[Outcome] = []
    try:
        prepared = [
            file
            for group in groups
            for file in prepare_group(group, manifest, root, force=arguments.force)
        ]
        outcomes = commit_prepared(prepared)
    except RefusalError as error:
        print(f"refusing to write: {error}")
        return EXIT_REFUSED
    except ManifestError as error:
        print(f"refusing to write: {error}")
        return EXIT_MANIFEST_UNUSABLE

    print(report(outcomes, environments, force=arguments.force))

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
