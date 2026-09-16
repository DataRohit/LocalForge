"""Independent process worker for login-throttle integration tests.

Bootstraps Django inside a spawned process and exercises only the public PostgreSQL store, allowing
tests to prove authoritative admission across isolated application runtimes.
"""

import os
import uuid
from importlib import import_module

import django


def count_postgres_throttle_admissions(
    key: str,
    attempts: int,
    limit: int,
    window_seconds: int,
    database_name: str,
) -> int:
    """Count admissions made by one isolated application process.

    Initializes Django after process creation, then submits distinct requests against a shared
    rolling-window key without reading or clearing any cache state.

    Arguments:
        key: Unique opaque rolling-window key shared by every worker.
        attempts: Number of decisions this worker requests.
        limit: Exact shared admission limit.
        window_seconds: Rolling window duration.
        database_name: Pytest database name shared by the spawned workers.

    Returns:
        Number of requests admitted for this worker.

    Raises:
        DatabaseError: If PostgreSQL cannot make the admission decision.
        ValueError: If the store rejects the supplied rule.
    """
    os.environ["POSTGRES_DB"] = database_name
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.testing")
    django.setup()

    throttle_module = import_module("accounts.login_throttle")
    store = throttle_module.PostgresLoginThrottleStore("default")
    rule = throttle_module.RollingWindowRule(
        key=key,
        limit=limit,
        window_seconds=window_seconds,
    )

    return sum(
        store.admit((rule,), member=f"{os.getpid()}-{uuid.uuid4().hex}").admitted
        for _attempt in range(attempts)
    )
