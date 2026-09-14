"""Administration registration for accounts.

Exposes the user model in the administration interface with the identifying fields editable and
everything security-sensitive read-only, so an operator can manage accounts without editing a hash.
"""

from typing import TYPE_CHECKING, override

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from accounts.models import User

if TYPE_CHECKING:
    from django.http import HttpRequest

    AccountAdminBase = UserAdmin[User]
else:
    AccountAdminBase = UserAdmin


@admin.register(User)
class AccountAdmin(AccountAdminBase):
    """Administration interface for the user model.

    Inherits the framework's AccountAdminBase, which is its ``UserAdmin``, so password handling
    stays the framework's, and replaces its field layout because this model identifies accounts by
    a non-sequential key and records its own timestamps.

    Attributes:
        ordering: Order accounts are listed in.
        list_display: Columns shown in the account list.
        list_filter: Filters offered beside the account list.
        search_fields: Fields the search box matches.
        readonly_fields: Fields no operator may edit.
        privileged_fields: Fields only a superuser may edit.
        fieldsets: Layout of the account edit form.
        add_fieldsets: Layout of the account creation form.

    Members:
        get_readonly_fields: Decide which fields this operator may edit.
    """

    ordering = ("created_at", "id")
    list_display = ("username", "email", "is_active", "is_staff", "created_at")
    list_filter = ("is_active", "is_staff", "is_superuser")
    search_fields = ("username", "email")
    readonly_fields = ("id", "last_login", "created_at", "updated_at")
    privileged_fields = ("is_staff", "is_superuser", "groups")

    @override
    def get_readonly_fields(
        self,
        request: HttpRequest,
        obj: User | None = None,
    ) -> tuple[str, ...]:
        """Decide which fields this operator may edit.

        Locks the permission fields for anyone who is not already a superuser, so an operator with
        nothing but the change permission cannot promote an account — themselves included — to one
        that outranks them.

        Arguments:
            request: Request carrying the operator making the change.
            obj: Account being edited, or None on the creation form.

        Returns:
            The fields to render read-only for this operator.

        Raises:
            None.
        """
        fields = tuple(super().get_readonly_fields(request, obj))

        if getattr(request.user, "is_superuser", False):
            return fields

        return fields + self.privileged_fields

    fieldsets = (
        (None, {"fields": ("id", "username", "password")}),
        ("Contact", {"fields": ("email",)}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups")}),
        ("Activity", {"fields": ("last_login", "created_at", "updated_at")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "email", "usable_password", "password1", "password2"),
            },
        ),
    )
