"""Statistical registration timing tests.

Measures the public registration boundary across every indistinguishable account state and the
activation-token-store fallback, while proving each request leaves only its intended durable state.
"""

from __future__ import annotations

import logging
import secrets
import statistics
import time
import uuid
from contextlib import nullcontext
from http import HTTPStatus
from typing import TYPE_CHECKING, Literal, cast

import pytest
from django.core import mail
from django.db import DatabaseError, connections
from django.db.models import Q
from django.test import override_settings

from accounts.models import ActivationToken, User

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from django.test import Client

CandidateState = Literal["new", "inactive-email", "active-email", "username-only"]
ActivationStoreMode = Literal["normal", "failure"]

PASSWORD = secrets.token_urlsafe(24)
TIMING_WARMUP_REQUESTS = 5
TIMING_MEASURED_REQUESTS = 30
TIMING_RELATIVE_LIMIT = 0.20
TIMING_ABSOLUTE_LIMIT_SECONDS = 0.010
CANDIDATE_STATES: tuple[CandidateState, ...] = (
    "new",
    "inactive-email",
    "active-email",
    "username-only",
)
timing_logger = logging.getLogger("localforge.tests.registration_timing")


def _registration_payload(username: str, email: str) -> dict[str, str]:
    """Build one fixed public registration body.

    Reuses identical submitted identifiers and credentials for every candidate state so status,
    body, and timing comparisons vary only authoritative account and token-store state.

    Arguments:
        username: Public account name shared by every measured outcome.
        email: Public account address shared by every measured outcome.

    Returns:
        Complete registration request body.
    """
    return {
        "username": username,
        "email": email,
        "password": PASSWORD,
        "password_confirm": PASSWORD,
    }


def _candidate_scope(username: str, email: str) -> Q:
    """Build the account scope owned by one timing case.

    Matches either submitted identifier case-insensitively so new and every seeded duplicate can
    be removed between samples without touching accounts belonging to another worker.

    Arguments:
        username: Submitted candidate username.
        email: Submitted candidate email.

    Returns:
        Query expression selecting the case's accounts.
    """
    return Q(username__iexact=username) | Q(email__iexact=email)


def _prepare_candidate_state(
    state: CandidateState,
    *,
    username: str,
    email: str,
    suffix: str,
) -> None:
    """Prepare exactly one registration candidate state.

    Seeds only the row needed to produce the requested duplicate classification, leaving the new
    state empty and avoiding password hashing outside the measured public request.

    Arguments:
        state: Registration outcome the next request must exercise.
        username: Submitted candidate username.
        email: Submitted candidate email.
        suffix: Unique case suffix used by non-conflicting seed identifiers.

    Returns:
        None.
    """
    if state == "new":
        return
    if state == "inactive-email":
        User.objects.using("default").create(
            username=f"inactive-{suffix}",
            email=email,
            password=PASSWORD,
        )
        return
    if state == "active-email":
        User.objects.using("default").create(
            username=f"active-{suffix}",
            email=email,
            password=PASSWORD,
            is_active=True,
        )
        return

    User.objects.using("default").create(
        username=username,
        email=f"username-only-{suffix}@localforge.invalid",
        password=PASSWORD,
    )


def _assert_candidate_state(
    state: CandidateState,
    *,
    username: str,
    email: str,
    token_store_failure: bool,
) -> None:
    """Verify one accepted request left only its intended state.

    Checks account, activation-token, and captured-mail counts before cleanup so a timing pass
    cannot conceal an accidental creation, token residue, or recipient disclosure.

    Arguments:
        state: Registration outcome exercised by the request.
        username: Submitted candidate username.
        email: Submitted candidate email.
        token_store_failure: Whether activation-token inserts were isolated as unavailable.

    Returns:
        None.

    Raises:
        AssertionError: If account, token, or mail state differs from the contract.
    """
    expected_accounts = 0 if token_store_failure and state == "new" else 1
    expects_delivery = not token_store_failure and state in {"new", "inactive-email"}

    assert User.objects.using("default").filter(_candidate_scope(username, email)).count() == (
        expected_accounts
    )
    assert ActivationToken.objects.using("default").count() == int(expects_delivery)
    assert len(mail.outbox) == int(expects_delivery)


def _clear_candidate_state(username: str, email: str) -> None:
    """Remove one sample's account, token, and captured-mail state.

    Deletes only identifiers owned by the timing case and clears the process-local outbox after
    every assertion so each sample begins from the same observable state.

    Arguments:
        username: Submitted candidate username.
        email: Submitted candidate email.

    Returns:
        None.
    """
    User.objects.using("default").filter(_candidate_scope(username, email)).delete()
    ActivationToken.objects.using("default").all().delete()
    mail.outbox.clear()


def _fail_activation_token_insert(
    execute: object,
    sql: object,
    *arguments: object,
) -> object:
    """Reject only activation-token persistence.

    Preserves account, admission, lookup, transaction, and dummy-publication work while making the
    real token store unavailable at its database boundary.

    Arguments:
        execute: Django database execution callback.
        sql: SQL statement sent to PostgreSQL.
        *arguments: Remaining execution-wrapper arguments.

    Returns:
        Database-driver result for every unrelated statement.

    Raises:
        DatabaseError: If the statement inserts an activation token.
        TypeError: If Django supplies a non-string SQL statement.
    """
    if not isinstance(sql, str):
        message = "database execution supplied non-string SQL"
        raise TypeError(message)
    if 'INSERT INTO "accounts_activationtoken"' in sql:
        raise DatabaseError
    return cast("Callable[..., object]", execute)(sql, *arguments)


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
@pytest.mark.timeout(360)
@pytest.mark.security_timing
@pytest.mark.parametrize(
    "activation_store_mode",
    [
        pytest.param("normal", id="normal"),
        pytest.param("failure", id="activation-token-store-failure"),
    ],
)
@override_settings(
    USER_REGISTRATION_ADDRESS_THROTTLE_RATE="1000/hour",
    ACCOUNT_ACTIVATION_RESEND_ACCOUNT_THROTTLE_RATE="1000/hour",
)
def test_registration_candidate_states_meet_the_timing_criterion(
    client: Client,
    activation_store_mode: ActivationStoreMode,
) -> None:
    """Keep every accepted registration outcome within the approved timing bound.

    Warms all four states five times, rotates thirty measured requests through each state, and
    compares their medians against the larger of twenty percent or ten milliseconds.

    Arguments:
        client: Django test client supplied by the framework.
        activation_store_mode: Whether activation-token persistence operates normally or fails.

    Returns:
        None.

    Raises:
        AssertionError: If status, body, durable state, or median response timing leaks an outcome.
    """
    suffix = uuid.uuid4().hex
    username = f"registration-timing-{suffix}"
    email = f"{username}@localforge.invalid"
    expected_body = {"username": username, "email": email}
    request_number = 0
    token_store_failure = activation_store_mode == "failure"

    def measure(state: CandidateState) -> float:
        """Measure one fully asserted public registration request.

        Prepares and cleans candidate state outside the timer while the elapsed interval covers the
        complete HTTP request, including its real or dummy activation publication.

        Arguments:
            state: Registration outcome to exercise.

        Returns:
            Elapsed HTTP request time in seconds.

        Raises:
            AssertionError: If the request or resulting durable state differs from the contract.
        """
        nonlocal request_number
        _clear_candidate_state(username, email)
        _prepare_candidate_state(
            state,
            username=username,
            email=email,
            suffix=f"{suffix}-{request_number}",
        )
        request_number += 1
        wrapper: AbstractContextManager[object] = (
            connections["default"].execute_wrapper(_fail_activation_token_insert)
            if token_store_failure
            else nullcontext()
        )
        started = time.perf_counter()
        with wrapper:
            response = client.post(
                "/api/v1/users/",
                _registration_payload(username, email),
                content_type="application/json",
                REMOTE_ADDR=f"2001:db8::{request_number:x}",
            )
        elapsed = time.perf_counter() - started

        assert response.status_code == HTTPStatus.CREATED
        assert response.json() == expected_body
        _assert_candidate_state(
            state,
            username=username,
            email=email,
            token_store_failure=token_store_failure,
        )
        _clear_candidate_state(username, email)
        return elapsed

    for _warmup in range(TIMING_WARMUP_REQUESTS):
        for state in CANDIDATE_STATES:
            measure(state)

    samples: dict[CandidateState, list[float]] = {state: [] for state in CANDIDATE_STATES}
    for attempt in range(TIMING_MEASURED_REQUESTS):
        offset = attempt % len(CANDIDATE_STATES)
        ordered_states = CANDIDATE_STATES[offset:] + CANDIDATE_STATES[:offset]
        for state in ordered_states:
            samples[state].append(measure(state))

    medians = {state: statistics.median(values) for state, values in samples.items()}
    slowest = max(medians.values())
    fastest = min(medians.values())
    median_delta = slowest - fastest
    allowed_delta = max(
        slowest * TIMING_RELATIVE_LIMIT,
        TIMING_ABSOLUTE_LIMIT_SECONDS,
    )
    timing_logger.info(
        (
            "registration timing measured token_store_failure=%s new=%.6fs "
            "inactive_email=%.6fs active_email=%.6fs username_only=%.6fs "
            "delta=%.6fs allowed=%.6fs"
        ),
        token_store_failure,
        medians["new"],
        medians["inactive-email"],
        medians["active-email"],
        medians["username-only"],
        median_delta,
        allowed_delta,
    )

    assert median_delta <= allowed_delta, (
        f"medians={medians} delta={median_delta:.6f}s allowed={allowed_delta:.6f}s"
    )
