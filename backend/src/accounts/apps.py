"""Application configuration for accounts.

Declares the application the authentication model lives in, so the framework loads it under a
stable label that every migration and every foreign key refers to.
"""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Configuration for the accounts application.

    Inherits from ``AppConfig`` and fixes the label the user model is referenced by, because the
    label becomes part of every migration that points at the authentication model.

    Attributes:
        default_auto_field: Primary key type for models that declare none of their own.
        name: Import path of the application.
        label: Stable label the authentication model is referenced by.

    Members:
        None beyond those the base class defines.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    label = "accounts"
