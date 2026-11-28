"""Shared exceptions for Harvia API clients."""


class HarviaAuthError(Exception):
    """Authentication failed."""


class HarviaConnectionError(Exception):
    """Connection to API failed."""


class HarviaNotFoundError(HarviaConnectionError):
    """The cloud does not have what was asked for (HTTP 404)."""
