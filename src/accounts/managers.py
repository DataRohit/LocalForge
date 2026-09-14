"""User creation for the accounts application.

Holds the manager every account is created through, so the rules an account must satisfy — a
username, an email, and a deliberate activation decision — live in one place.
"""

from typing import TYPE_CHECKING, Any, override

from django.contrib.auth.models import BaseUserManager

from accounts.normalisation import normalise_email

if TYPE_CHECKING:
    from accounts.models import User


class UserManager(BaseUserManager["User"]):
    """Create ordinary accounts and superusers.

    Inherits from ``BaseUserManager`` and adds the two creation entry points the project uses,
    refusing an account that carries no username or no email because both are identifying and both
    are unique.

    Members:
        get_by_natural_key: Find an account by username, whatever case it was typed in.
        create_user: Create an ordinary account, inactive until it is activated.
        create_superuser: Create an active account with full permissions.
    """

    @override
    def get_by_natural_key(self, username: str | None) -> User:
        """Find an account by username, whatever case it was typed in.

        Matches case-insensitively because the identifiers are unique case-insensitively, so an
        account named one way cannot be locked out by being typed another and cannot be silently
        missed by an endpoint that addresses accounts by name.

        Arguments:
            username: Username to look up.

        Returns:
            The matching account.

        Raises:
            User.DoesNotExist: If no account carries that username.
        """
        return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})

    def create_user(
        self,
        username: str,
        email: str,
        password: str | None = None,
        **extra_fields: Any,  # noqa: ANN401
    ) -> User:
        """Create an ordinary account.

        Normalises the address, hashes the password, and leaves the account inactive unless the
        caller says otherwise, so a self-registered account cannot sign in before it is activated.

        Arguments:
            username: Unique username for the account.
            email: Unique email address for the account.
            password: Raw password to hash, or None for an unusable password.
            **extra_fields: Any other model field to set.

        Returns:
            The saved account.

        Raises:
            ValueError: If the username or the email is missing.
        """
        if not username:
            message = "an account requires a username"
            raise ValueError(message)

        if not email:
            message = "an account requires an email address"
            raise ValueError(message)

        extra_fields.setdefault("is_active", False)
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)

        user = self.model(username=username, email=normalise_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)

        return user

    def create_superuser(
        self,
        username: str,
        email: str,
        password: str | None = None,
        **extra_fields: Any,  # noqa: ANN401
    ) -> User:
        """Create an account with full permissions.

        Forces the staff, superuser, and active flags rather than trusting the caller, because an
        inactive superuser cannot sign in and would look like a broken installation.

        Arguments:
            username: Unique username for the account.
            email: Unique email address for the account.
            password: Raw password to hash, or None for an unusable password.
            **extra_fields: Any other model field to set.

        Returns:
            The saved account.

        Raises:
            ValueError: If the username or the email is missing, or if a flag is contradicted.
        """
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        for flag in ("is_staff", "is_superuser", "is_active"):
            if extra_fields.get(flag) is not True:
                message = f"a superuser requires {flag} to be true"
                raise ValueError(message)

        return self.create_user(username, email, password, **extra_fields)
