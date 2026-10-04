"""Unit tests for the user model's configuration and refusals.

Covers what the model decides before any database is involved: the configured authentication model,
the arguments the manager refuses, and the fields the administration keeps out of an operator's
reach.
"""

import secrets
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest
from django.contrib.admin import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower

from accounts.admin import AccountAdmin
from accounts.models import ActivationToken, PasswordResetToken, User, UsernameResetToken
from accounts.normalisation import normalise_email
from tests.factories import (
    build_activation_token,
    build_login_throttle_event,
    build_password_reset_token,
    build_username_reset_token,
)

if TYPE_CHECKING:
    from typing import Protocol

    from django.db.models import UniqueConstraint
    from django.http import HttpRequest

    class CredentialRecordProtocol(Protocol):
        """Describe shared credential-record state for type checking.

        Inherits from ``Protocol`` and defines only the dynamic foreign-key identifier and
        timestamps common to all three models. Runtime assertions use concrete model objects.

        Attributes:
            account_id: Nullable related account identifier.
            used_at: Optional consumption timestamp.
            delivery_claimed_at: Optional delivery-claim timestamp.
            delivered_at: Optional successful-delivery timestamp.

        Members:
            None.
        """

        account_id: object | None
        used_at: object | None
        delivery_claimed_at: object | None
        delivered_at: object | None


PASSWORD = secrets.token_urlsafe(16)
TOKEN_DIGEST_LENGTH = 64


@pytest.mark.unit
def test_the_project_owns_its_user_model() -> None:
    """Authenticate against the project's own model.

    Confirms the configured authentication model is this project's, because a migration generated
    against the framework's default cannot be swapped afterwards.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If another model is configured.
    """
    assert get_user_model() is User


@pytest.mark.unit
def test_the_username_identifies_an_account_and_the_email_is_required() -> None:
    """Sign in by username, register with an address.

    Confirms the username is the credential field and the address is required alongside it, which
    is the shape the account endpoints and the management commands both expect.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either identifier is configured differently.
    """
    assert User.USERNAME_FIELD == "username"
    assert User.EMAIL_FIELD == "email"
    assert "email" in User.REQUIRED_FIELDS


@pytest.mark.unit
@pytest.mark.parametrize(
    ("username", "email", "expected"),
    [("", "probe@localforge.invalid", "username"), ("probe", "", "email")],
)
def test_creation_requires_both_identifiers(username: str, email: str, expected: str) -> None:
    """Refuse an account missing an identifier.

    Confirms both identifiers are required before anything reaches the database, because each is
    unique and the account endpoints address accounts by either one.

    Arguments:
        username: Username the caller supplied.
        email: Email address the caller supplied.
        expected: Word the refusal must name.

    Returns:
        None.

    Raises:
        AssertionError: If the account is created without an identifier.
    """
    with pytest.raises(ValueError, match=expected):
        User.objects.create_user(username, email, PASSWORD)


@pytest.mark.unit
@pytest.mark.parametrize("flag", ["is_staff", "is_superuser", "is_active"])
def test_a_contradicted_superuser_flag_is_refused(flag: str) -> None:
    """Refuse a superuser that is not one.

    Confirms each permission flag must be true, so a caller cannot create something recorded as a
    superuser that behaves as an ordinary account.

    Arguments:
        flag: Flag the caller contradicts.

    Returns:
        None.

    Raises:
        AssertionError: If the contradiction is accepted.
    """
    with pytest.raises(ValueError, match=flag):
        User.objects.create_superuser(
            "probe-root",
            "root@localforge.invalid",
            PASSWORD,
            **{flag: False},
        )


@pytest.mark.unit
def test_an_account_renders_as_its_username() -> None:
    """Show the identifier a human recognises.

    Confirms an account renders as its username, which is what the administration interface and log
    records display in place of an opaque key.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the account renders as something else.
    """
    assert str(User(username="probe")) == "probe"


@pytest.mark.unit
def test_an_address_is_normalised_before_it_is_stored() -> None:
    """Store one canonical form of an address.

    Confirms the whole address is lowercased and trimmed, not only its domain, so a lookup by
    address is an exact match and cannot silently miss an account that signed up in another case.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the address keeps its original form.
    """
    assert normalise_email(" Probe@LOCALFORGE.Invalid ") == "probe@localforge.invalid"
    assert normalise_email(None) == ""


@pytest.mark.unit
def test_field_cleaning_normalises_the_address_before_validation() -> None:
    """Validate the canonical address rather than the caller's spelling.

    Calls the model's public field-cleaning seam with surrounding space and mixed case, proving the
    stored-form transformation happens before Django's email validator runs.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If field cleaning validates or retains the non-canonical address.
    """
    account = User(
        username="clean-fields-user",
        email=" Clean-Fields@LOCALFORGE.Invalid ",
    )
    account.set_password(PASSWORD)

    account.clean_fields()

    assert account.email == "clean-fields@localforge.invalid"


@pytest.mark.unit
def test_saving_normalises_and_validates_before_persistence() -> None:
    """Persist only a canonical, syntactically valid address.

    Substitutes the database write while exercising the model's public save method, proving its
    normalization completes before control reaches Django persistence.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If save forwards a non-canonical address or changes caller arguments.
    """
    account = User(
        username="save-user",
        email=" Save-User@LOCALFORGE.Invalid ",
    )

    with patch.object(models.Model, "save") as save:
        account.save(force_insert=True)

    assert account.email == "save-user@localforge.invalid"
    save.assert_called_once_with(force_insert=True)


@pytest.mark.unit
def test_saving_rejects_an_invalid_address_before_persistence() -> None:
    """Refuse an invalid address before the model reaches the database.

    Calls the public save seam with malformed input and verifies Django's validator stops the write
    rather than leaving the database or a constraint to discover it.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If invalid input reaches model persistence.
    """
    account = User(username="invalid-email-user", email="not-an-address")

    with (
        patch.object(models.Model, "save") as save,
        pytest.raises(ValidationError),
    ):
        account.save()

    save.assert_not_called()


@pytest.mark.unit
def test_the_identifiers_are_unique_without_regard_to_case() -> None:
    """Constrain both identifiers case-insensitively.

    Confirms each constraint is declared over the lowercased identifier rather than the raw column,
    which is what a plain unique column does not give and what stops two accounts a human reads as
    one.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either constraint is absent or is not case-insensitive.
    """
    constraints = {constraint.name: constraint for constraint in User._meta.constraints}  # noqa: SLF001

    assert set(constraints) == {"accounts_user_username_ci_unique", "accounts_user_email_ci_unique"}

    for name, field in (
        ("accounts_user_username_ci_unique", "username"),
        ("accounts_user_email_ci_unique", "email"),
    ):
        expression = cast("UniqueConstraint", constraints[name]).expressions[0]

        assert isinstance(expression, Lower)
        assert field in str(expression)


@pytest.mark.unit
def test_a_constraint_violation_names_no_internal_detail() -> None:
    """Refuse a duplicate without describing the schema.

    Confirms each constraint carries its own message, because the default names the index and, for
    an address, confirms to an unauthenticated caller that the account exists.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a message leaks the constraint name.
    """
    for constraint in User._meta.constraints:  # noqa: SLF001
        message = str(constraint.violation_error_message)

        assert constraint.name not in message
        assert "constraint" not in message.lower()


@pytest.mark.unit
def test_an_account_is_inactive_until_it_is_activated() -> None:
    """Create accounts that cannot yet sign in.

    Confirms the activation flag defaults to off on the field itself, so an account created by any
    route starts unusable rather than only one created through the manager.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If a new account is active.
    """
    assert User().is_active is False
    assert User().is_staff is False


@pytest.mark.unit
def test_login_event_metadata_and_rendering_are_safe() -> None:
    """Describe one throttle event without recovering submitted identity.

    Uses the shared factory to verify table, indexes, uniqueness, and string rendering remain
    limited to persisted opaque identifiers.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If metadata or rendering exposes another shape.
    """
    event = build_login_throttle_event(bucket="opaque-bucket", request_id="request-id")

    assert event._meta.db_table == "accounts_login_throttle_event"  # noqa: SLF001
    assert {index.name for index in event._meta.indexes} == {  # noqa: SLF001
        "accounts_login_bucket_time",
        "accounts_login_occurred_id",
    }
    assert {constraint.name for constraint in event._meta.constraints} == {  # noqa: SLF001
        "accounts_login_bucket_request_unique"
    }
    assert str(event) == "opaque-bucket:request-id"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("record", "subject_index", "retention_index"),
    [
        (
            build_activation_token(),
            "accounts_act_subject_used",
            "accounts_activation_issued_id",
        ),
        (
            build_password_reset_token(),
            "accounts_reset_subject_used",
            "accounts_reset_issued_id",
        ),
        (
            build_username_reset_token(),
            "accounts_username_subject_used",
            "accounts_username_issued_id",
        ),
    ],
)
def test_credential_records_share_safe_defaults_metadata_and_rendering(
    record: ActivationToken | PasswordResetToken | UsernameResetToken,
    subject_index: str,
    retention_index: str,
) -> None:
    """Keep token tombstones ordered, indexed, nullable, and bearer-free.

    Compares each model's defaults and metadata while verifying string rendering contains the
    immutable subject and row key but never the digest.

    Arguments:
        record: Unsaved credential-state record to inspect.
        subject_index: Expected account-state lookup index.
        retention_index: Expected chronological cleanup index.

    Returns:
        None.

    Raises:
        AssertionError: If defaults, metadata, ordering, or safe rendering drift.
    """
    record.pk = 7
    rendered = str(record)
    credential = cast("CredentialRecordProtocol", record)

    assert credential.account_id is None
    assert credential.used_at is None
    assert credential.delivery_claimed_at is None
    assert credential.delivered_at is None
    assert record._meta.ordering == ("-issued_at", "-id")  # noqa: SLF001
    assert {index.name for index in record._meta.indexes} == {  # noqa: SLF001
        subject_index,
        retention_index,
    }
    assert str(record.subject_id) in rendered
    assert rendered.endswith(":7")
    assert record.digest not in rendered
    digest_field = record._meta.get_field("digest")  # noqa: SLF001
    assert isinstance(digest_field, models.CharField)
    assert digest_field.max_length == TOKEN_DIGEST_LENGTH
    assert digest_field.unique is True


@pytest.mark.unit
def test_the_administration_keeps_the_sensitive_fields_read_only() -> None:
    """Keep credentials and identifiers out of an operator's reach.

    Confirms the key, the sign-in record, and both timestamps are registered read-only, so an
    operator can inspect an account without rewriting its history.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any sensitive field is editable.
    """
    assert set(AccountAdmin.readonly_fields) == {
        "id",
        "last_login",
        "created_at",
        "updated_at",
    }


@pytest.mark.unit
@pytest.mark.parametrize(("superuser", "locked"), [(True, False), (False, True)])
def test_only_a_superuser_may_edit_the_permission_fields(
    *,
    superuser: bool,
    locked: bool,
) -> None:
    """Stop an operator granting itself more than it has.

    Confirms the permission fields are read-only for anyone who is not already a superuser, because
    an operator holding only the change permission could otherwise promote an account past its own
    authority.

    Arguments:
        superuser: Whether the operator making the request is a superuser.
        locked: Whether the permission fields are expected to be read-only.

    Returns:
        None.

    Raises:
        AssertionError: If a non-superuser may edit a permission field.
    """
    request = SimpleNamespace(user=SimpleNamespace(is_superuser=superuser))
    fields = AccountAdmin(User, AdminSite()).get_readonly_fields(cast("HttpRequest", request))

    assert all(field in fields for field in AccountAdmin.privileged_fields) is locked
