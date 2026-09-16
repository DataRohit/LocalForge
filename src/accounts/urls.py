"""Versioned account API routes.

Maps the fixed token and JSON web token authentication endpoints built through Ticket 30, leaving
every later account route absent until its own ticket implements and documents it.
"""

from django.urls import path

from accounts.jwt_authentication import JWTCreateView, JWTRefreshView, JWTVerifyView
from accounts.token_authentication import TokenLoginView, TokenLogoutView

app_name = "accounts"

urlpatterns = [
    path("jwt/create/", JWTCreateView.as_view(), name="jwt-create"),
    path("jwt/refresh/", JWTRefreshView.as_view(), name="jwt-refresh"),
    path("jwt/verify/", JWTVerifyView.as_view(), name="jwt-verify"),
    path("token/login/", TokenLoginView.as_view(), name="token-login"),
    path("token/logout/", TokenLogoutView.as_view(), name="token-logout"),
]
