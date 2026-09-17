"""Versioned account API routes.

Maps fixed authentication, registration, activation, self-profile, password-change, and
password-recovery endpoints while leaving username-management routes absent until their ticket.
"""

from django.urls import path

from accounts.jwt_authentication import JWTCreateView, JWTRefreshView, JWTVerifyView
from accounts.password_management import (
    PasswordChangeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
)
from accounts.token_authentication import TokenLoginView, TokenLogoutView
from accounts.user_profiles import ActivationResendView, UserProfileView, UserRegistrationView

app_name = "accounts"

urlpatterns = [
    path("users/", UserRegistrationView.as_view(), name="user-registration"),
    path("users/me/", UserProfileView.as_view(), name="user-profile"),
    path(
        "users/resend_activation/",
        ActivationResendView.as_view(),
        name="activation-resend",
    ),
    path("users/set_password/", PasswordChangeView.as_view(), name="password-change"),
    path(
        "users/reset_password/",
        PasswordResetRequestView.as_view(),
        name="password-reset",
    ),
    path(
        "users/reset_password_confirm/",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path("jwt/create/", JWTCreateView.as_view(), name="jwt-create"),
    path("jwt/refresh/", JWTRefreshView.as_view(), name="jwt-refresh"),
    path("jwt/verify/", JWTVerifyView.as_view(), name="jwt-verify"),
    path("token/login/", TokenLoginView.as_view(), name="token-login"),
    path("token/logout/", TokenLogoutView.as_view(), name="token-logout"),
]
