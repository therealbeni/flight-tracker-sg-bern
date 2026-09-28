"""Outbound email for account approval notices and password resets.

No SMTP provider is wired up yet (needs real credentials from whoever hosts this), so
the default backend just logs the message. Swap `sender` for an SMTP-backed
implementation once the club has a mail account to send from - nothing else in the
app needs to change.
"""

import sys
from typing import Protocol


class EmailSender(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class ConsoleEmailSender:
    """Prints the email instead of sending it. Fine for development; for production
    this must be replaced before password resets can actually reach anyone."""

    def send(self, to: str, subject: str, body: str) -> None:
        print(f"\n--- EMAIL to {to} ---\nSubject: {subject}\n\n{body}\n--- END EMAIL ---\n", file=sys.stderr)


sender: EmailSender = ConsoleEmailSender()
