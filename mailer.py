"""SMTP helpers for sending application notification emails.

Credentials and server come from the system-wide Email Settings
(see store.get_email_settings / store.save_email_settings).
Transport mode: STARTTLS when the server advertises it, otherwise
plain SMTP on the given port.
"""

import smtplib
import ssl
from email.mime.text import MIMEText


def _connect(host, port, username="", password="", timeout=15, use_tls=True, use_auth=True):
    """Open an SMTP connection.

    If ``use_tls`` is true, attempt STARTTLS after the initial handshake;
    if the server does not advertise it, continue over plaintext.

    If ``use_auth`` is true and credentials are present, log in via SMTP
    AUTH; otherwise the connection is left unauthenticated (for relays
    that do not require it, e.g. some test servers).
    """
    server = smtplib.SMTP(host, port, timeout=timeout)
    if use_tls:
        try:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        except smtplib.SMTPNotSupportedError:
            pass  # plain fallback; the same connection is still usable

    if use_auth and username and password:
        try:
            server.login(username, password)
        except smtplib.SMTPAuthenticationError:
            try:
                server.quit()
            except Exception:
                server.close()
            raise
    return server


def test_connection(host, port, username="", password="", from_address="", use_auth=True):
    """Open a connection, log in if credentials are present, and close it.

    Raises on any failure so the caller can surface a status message.
    """
    host = (host or "").strip()
    if not host:
        raise ValueError("SMTP server is required.")
    port = int(port)
    if not (1 <= port <= 65535):
        raise ValueError("SMTP port must be between 1 and 65535.")

    server = _connect(host, port, username, password, use_auth=use_auth)
    try:
        if from_address:
            server.verify(from_address)
    finally:
        try:
            server.quit()
        except Exception:
            server.close()


def send_email(host, port, username, password, sender, to, subject, body,
               html_body=None, use_auth=True):
    """Send a single email over the configured SMTP server.

    ``body`` is the plain-text part; ``html_body`` (optional) becomes the
    multipart HTML part. ``use_auth`` (default True) controls whether SMTP
    AUTH is attempted when credentials are present.
    Raises smtplib.SMTPException on failure.
    """
    msg = MIMEText(body if body is not None else "", "plain")
    if html_body is not None:
        msg = _with_html(msg, body, html_body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to if isinstance(to, str) else ", ".join(to)

    server = _connect(host, port, username, password, use_auth=use_auth)
    try:
        server.sendmail(sender, _recipients(to), msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            server.close()


def _with_html(plain_part, text, html):
    from email.mime.multipart import MIMEMultipart
    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText(text or "", "plain"))
    msg.attach(MIMEText(html, "html"))
    return msg


def _recipients(to):
    if isinstance(to, (list, tuple, set)):
        return list(to)
    return [to]
