"""Application email rendering and delivery.

Builds every message from matching plain-text and HTML templates, adds the configured site
identity, and contains expected SMTP transport failures before they can escape into a request.
"""

import logging
import smtplib
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)


def build_site_url(path: str) -> str:
    """Build an absolute application URL for an email.

    Joins a route onto the environment's public site URL, so the same email code points at the
    correct host in development and testing without inspecting the request that triggered it.

    Arguments:
        path: Application path to place beneath the configured site URL.

    Returns:
        An absolute URL on the configured site.
    """
    base = f"{settings.SITE_URL.rstrip('/')}/"
    return urljoin(base, path.lstrip("/"))


def send_application_email(
    recipient: str,
    subject: str,
    message: str,
    *,
    action_path: str | None = None,
    context: Mapping[str, object] | None = None,
) -> bool:
    """Render and deliver one multipart application email.

    Adds the site identity and optional absolute action URL to caller context, sends from the
    configured address, and logs expected transport failures without exposing recipient data.

    Arguments:
        recipient: Address that should receive the message.
        subject: Subject shown by the mail client.
        message: Plain application copy shared by both templates.
        action_path: Optional application route rendered as an absolute link.
        context: Additional values available to both templates.

    Returns:
        True when the backend accepted one message, otherwise False.
    """
    template_context = dict(context or {})
    template_context.update(
        {
            "action_url": build_site_url(action_path) if action_path is not None else None,
            "message": message,
            "site_name": settings.SITE_NAME,
        }
    )
    text_body = render_to_string("email/message.txt", template_context).strip()
    html_body = render_to_string("email/message.html", template_context).strip()
    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    email.attach_alternative(html_body, "text/html")

    try:
        delivered = email.send(fail_silently=False)
    except (OSError, smtplib.SMTPException, ValueError) as error:
        error_type = type(error).__name__
    else:
        if delivered == 1:
            return True
        error_type = "BackendRejectedMessage"

    logger.error(
        "Email delivery failed",
        extra={
            "email_error_type": error_type,
            "email_template": "email/message",
            "recipient_count": 1,
        },
    )
    return False
