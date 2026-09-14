"""Integration tests for the user model and its manager.

Covers creation, email normalisation, and the inactive-by-default decision against the database. A
test that reads back through the ORM commits its writes, because reads are routed to the replica
connection, which cannot see another connection's open transaction.
"""

import secrets
import uuid
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

from accounts.models import User

if TYPE_CHECKING:
    from django.test import Client

PASSWORD = secrets.token_urlsafe(16)


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"])
def test_an_account_is_created_inactive_with_a_usable_password() -> None:
    """Create an account that cannot yet sign in.

    Confirms a new account is inactive and its password is hashed rather than stored, so a
    self-registered account is unusable until it is activated.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the account is active or the password was stored in the clear.
    """
    account = User.objects.create_user("probe", "probe@localforge.invalid", PASSWORD)

    assert account.is_active is False
    assert account.is_staff is False
    assert account.is_superuser is False
    assert account.password != PASSWORD
    assert account.check_password(PASSWORD)


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"])
def test_an_account_carries_a_non_sequential_key_and_timestamps() -> None:
    """Identify accounts without revealing how many exist.

    Confirms the primary key is a time-ordered identifier rather than a counter, and that both
    timestamps are recorded, which is what keeps a URL from leaking the size of the table.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the key is sequential or a timestamp is missing.
    """
    first = User.objects.create_user("probe-one", "one@localforge.invalid", PASSWORD)
    second = User.objects.create_user("probe-two", "two@localforge.invalid", PASSWORD)

    assert isinstance(first.pk, uuid.UUID)
    assert first.pk.version == uuid.uuid7().version
    assert first.pk != second.pk
    assert first.created_at is not None
    assert first.updated_at is not None


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"])
def test_saving_an_account_again_moves_only_the_modified_timestamp() -> None:
    """Record when an account was last changed.

    Confirms the creation timestamp is fixed while the modification timestamp moves, so an operator
    can tell a new account from one that was edited.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If either timestamp behaves the other way.
    """
    account = User.objects.create_user("probe", "probe@localforge.invalid", PASSWORD)
    created, updated = account.created_at, account.updated_at

    account.username = "probe-renamed"
    account.save()

    assert account.created_at == created
    assert account.updated_at > updated


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"])
def test_a_superuser_is_active_and_fully_permitted() -> None:
    """Create a superuser that can sign in immediately.

    Confirms a superuser is created active with both permission flags, because an inactive
    superuser cannot sign in and reads as a broken installation.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If any flag is not set.
    """
    account = User.objects.create_superuser("probe-root", "root@localforge.invalid", PASSWORD)

    assert account.is_active is True
    assert account.is_staff is True
    assert account.is_superuser is True


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"])
def test_two_accounts_cannot_differ_only_by_username_case() -> None:
    """Keep usernames unique whatever their case.

    Confirms a username differing only in letter case is refused, because two accounts a human
    reads as identical are an impersonation risk rather than a convenience.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the second account is accepted.
    """
    User.objects.create_user("Probe", "one@localforge.invalid", PASSWORD)

    with pytest.raises(IntegrityError, match="username_ci_unique"):
        User.objects.create_user("probe", "two@localforge.invalid", PASSWORD)


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"])
def test_two_accounts_cannot_differ_only_by_email_case() -> None:
    """Keep addresses unique whatever their case.

    Confirms an address differing only in letter case is refused, because password reset and
    activation both address an account by its email.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the second account is accepted.
    """
    User.objects.create_user("probe-one", "Probe@localforge.invalid", PASSWORD)

    with pytest.raises(IntegrityError, match=r"(?i)unique"):
        User.objects.create_user("probe-two", "probe@localforge.invalid", PASSWORD)


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"])
def test_an_address_is_normalised_on_save() -> None:
    """Store one canonical form of an address.

    Confirms the domain is lowercased and surrounding whitespace removed when an account is saved,
    so the same address typed two ways resolves to one account.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the stored address keeps its original form.
    """
    account = User.objects.create_user("probe", " Probe@LOCALFORGE.Invalid ", PASSWORD)

    assert account.email == "probe@localforge.invalid"


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"])
def test_an_unparsable_address_is_refused_on_save() -> None:
    """Refuse an address that is not one.

    Confirms validation happens before the write, because the case-insensitive constraint would
    otherwise be the first thing to notice and would report a confusing error.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the address is stored.
    """
    with pytest.raises(ValidationError):
        User.objects.create_user("probe", "not-an-address", PASSWORD)


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_an_inactive_account_cannot_authenticate() -> None:
    """Refuse sign-in until the account is activated.

    Confirms authentication rejects an account that has not been activated even with the right
    password, which is what makes the activation step meaningful.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an inactive account authenticates.
    """
    User.objects.create_user("probe", "probe@localforge.invalid", PASSWORD)

    assert authenticate(username="probe", password=PASSWORD) is None


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_an_activated_account_authenticates() -> None:
    """Allow sign-in once the account is activated.

    Confirms the same credentials succeed after activation, so the refusal above is the activation
    flag rather than a broken password path.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If an activated account cannot authenticate.
    """
    account = User.objects.create_user("probe", "probe@localforge.invalid", PASSWORD)
    account.is_active = True
    account.save()

    assert authenticate(username="probe", password=PASSWORD) == account


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"])
def test_an_account_created_without_a_password_cannot_sign_in() -> None:
    """Leave an account without a usable password.

    Confirms omitting the password produces an unusable one rather than an empty one, which is the
    state an account invited by an operator starts in.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the password is usable.
    """
    account = User.objects.create_user("probe", "probe@localforge.invalid")

    assert account.has_usable_password() is False


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_the_administration_hides_the_sensitive_fields(admin_client: Client) -> None:
    """Keep the password hash out of an editable field.

    Confirms the administration form renders with the identity and permission fields while the
    hash, the key, and the timestamps are read-only, so an operator cannot overwrite a credential.

    Arguments:
        admin_client: Authenticated administration client supplied by the test framework.

    Returns:
        None.

    Raises:
        AssertionError: If the page does not render or exposes an editable hash.
    """
    account = User.objects.create_user("probe", "probe@localforge.invalid", PASSWORD)
    response = admin_client.get(f"/admin/accounts/user/{account.pk}/change/")

    assert response.status_code == HTTPStatus.OK
    assert b'name="password"' not in response.content
    assert b'name="username"' in response.content


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_an_account_is_found_whatever_case_the_name_is_typed_in() -> None:
    """Find an account however its name was typed.

    Confirms the natural-key lookup matches case-insensitively, because the identifiers are unique
    case-insensitively: without this an account named one way is locked out when typed another and
    cannot re-register, since the constraint refuses the duplicate.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the lookup misses the account.
    """
    account = User.objects.create_user("Probe", "probe@localforge.invalid", PASSWORD)

    assert User.objects.get_by_natural_key("probe") == account
    assert User.objects.get_by_natural_key("PROBE") == account


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_an_account_authenticates_whatever_case_the_name_is_typed_in() -> None:
    """Sign in however the name was typed.

    Confirms authentication reaches the same account whatever case the credential is entered in,
    which is the path a person actually takes and the one a locked-out account would fail on.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If authentication misses the account.
    """
    account = User.objects.create_user("Probe", "probe@localforge.invalid", PASSWORD)
    account.is_active = True
    account.save()

    assert authenticate(username="probe", password=PASSWORD) == account


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"], transaction=True)
def test_an_address_is_found_by_an_exact_lookup() -> None:
    """Find an account by the address it was given.

    Confirms the stored address is fully lowercased, so every flow that addresses an account by
    email matches exactly rather than depending on each caller remembering to ignore case.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If the stored address is not canonical.
    """
    User.objects.create_user("probe", "Probe@LOCALFORGE.Invalid", PASSWORD)

    assert User.objects.filter(email="probe@localforge.invalid").count() == 1


@pytest.mark.integration
@pytest.mark.services("postgres")
@pytest.mark.django_db(databases=["default", "replica"])
def test_full_validation_canonicalises_the_address() -> None:
    """Canonicalise the address wherever validation runs.

    Confirms full validation leaves the address in the form the database stores, so a uniqueness
    check made during validation sees the value that will actually be written.

    Arguments:
        None.

    Returns:
        None.

    Raises:
        AssertionError: If validation leaves the address in another form.
    """
    account = User(username="probe", email=" Probe@LOCALFORGE.Invalid ")
    account.full_clean(exclude=["password"], validate_unique=False, validate_constraints=False)

    assert account.email == "probe@localforge.invalid"
