from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from django.test import Client


@pytest.mark.integration
@pytest.mark.django_db
def test_admin_login_page_is_available(client: Client) -> None:
    response = client.get("/admin/login/")

    assert response.status_code == HTTPStatus.OK
    assert "admin/login.html" in [template.name for template in response.templates]
