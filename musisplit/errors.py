"""Exceptions the UI can show to the user as-is."""
from __future__ import annotations


class UserError(Exception):
    """An error with a message written for a non-technical user."""


class NoInternetError(UserError):
    def __init__(self, what: str = "this") -> None:
        super().__init__(
            f"Couldn't connect to the internet to download {what}.\n"
            "Check your connection and try again."
        )


class Cancelled(Exception):
    """The user pressed Cancel."""


# Fragments seen in socket / urllib / yt-dlp messages when the network is down.
_NETWORK_HINTS = (
    "getaddrinfo failed",
    "nodename nor servname",
    "name or service not known",
    "temporary failure in name resolution",
    "no address associated",
    "network is unreachable",
    "timed out",
    "connection refused",
    "connection reset",
    "connection aborted",
    "failed to establish a new connection",
    "urlopen error",
    "errno 11001",
    "errno 11002",
    "errno 10060",
    "errno 10061",
    "errno 10065",
    "remote end closed connection",
)


def looks_like_network_error(exc: BaseException | str) -> bool:
    text = str(exc).lower()
    return any(h in text for h in _NETWORK_HINTS)
