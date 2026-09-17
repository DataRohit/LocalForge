"""Versioned account API routes.

Maps the fixed authentication, registration, and self-profile endpoints built through Ticket 31,
leaving every later activation, password, and username route absent until its owning ticket.
"""

from django.urls import path

from accounts.jwt_authentication import JWTCreateView, JWTRefreshView, JWTVerifyView
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
    path("jwt/create/", JWTCreateView.as_view(), name="jwt-create"),
    path("jwt/refresh/", JWTRefreshView.as_view(), name="jwt-refresh"),
    path("jwt/verify/", JWTVerifyView.as_view(), name="jwt-verify"),
    path("token/login/", TokenLoginView.as_view(), name="token-login"),
    path("token/logout/", TokenLogoutView.as_view(), name="token-logout"),
]
