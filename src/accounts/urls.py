"""Versioned account API routes.

Maps only the fixed token authentication endpoints built by Ticket 29, leaving every later account
and JSON web token route absent until its own ticket implements and documents it.
"""

from django.urls import path

from accounts.token_authentication import TokenLoginView, TokenLogoutView

app_name = "accounts"

urlpatterns = [
    path("token/login/", TokenLoginView.as_view(), name="token-login"),
    path("token/logout/", TokenLogoutView.as_view(), name="token-logout"),
]
