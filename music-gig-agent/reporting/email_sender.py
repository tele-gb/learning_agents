import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


class EmailConfigError(RuntimeError):
    """Raised when report email settings are missing or invalid."""


def send_report_email(
    report_path: Path,
    html_report_path: Path | None = None,
    to_address: str | None = None,
    subject: str | None = None,
) -> None:
    """Send the report using SMTP settings from environment variables."""
    config = _load_email_config(to_address, subject)
    report_text = report_path.read_text(encoding="utf-8")
    html_report = (
        html_report_path.read_text(encoding="utf-8")
        if html_report_path and html_report_path.exists()
        else None
    )

    message = EmailMessage()
    message["Subject"] = config["subject"]
    message["From"] = config["from_address"]
    message["To"] = ", ".join(config["to_addresses"])
    message.set_content(report_text)
    if html_report:
        message.add_alternative(html_report, subtype="html")
    message.add_attachment(
        report_text.encode("utf-8"),
        maintype="text",
        subtype="markdown",
        filename=report_path.name,
    )
    if html_report and html_report_path:
        message.add_attachment(
            html_report.encode("utf-8"),
            maintype="text",
            subtype="html",
            filename=html_report_path.name,
        )

    if config["use_ssl"]:
        with smtplib.SMTP_SSL(config["host"], config["port"], timeout=30) as smtp:
            _login_if_needed(smtp, config)
            smtp.send_message(message)
        return

    with smtplib.SMTP(config["host"], config["port"], timeout=30) as smtp:
        if config["use_tls"]:
            smtp.starttls()
        _login_if_needed(smtp, config)
        smtp.send_message(message)


def _load_email_config(
    to_address: str | None,
    subject: str | None,
) -> dict[str, object]:
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        raise EmailConfigError("Set SMTP_HOST before using --email-report.")

    from_address = os.environ.get("EMAIL_FROM", "").strip()
    if not from_address:
        raise EmailConfigError("Set EMAIL_FROM before using --email-report.")

    raw_to_addresses = (to_address or os.environ.get("EMAIL_TO", "")).strip()
    to_addresses = [
        address.strip()
        for address in raw_to_addresses.split(",")
        if address.strip()
    ]
    if not to_addresses:
        raise EmailConfigError("Set EMAIL_TO or pass --email-to before using --email-report.")

    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "username": os.environ.get("SMTP_USERNAME", "").strip(),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "from_address": from_address,
        "to_addresses": to_addresses,
        "subject": subject or os.environ.get(
            "EMAIL_SUBJECT",
            "Birmingham gig recommendations",
        ),
        "use_tls": _env_bool("SMTP_USE_TLS", default=True),
        "use_ssl": _env_bool("SMTP_USE_SSL", default=False),
    }


def _login_if_needed(smtp: smtplib.SMTP, config: dict[str, object]) -> None:
    username = str(config["username"])
    password = str(config["password"])
    if username or password:
        smtp.login(username, password)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
