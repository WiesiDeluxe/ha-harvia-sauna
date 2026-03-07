"""Shared exceptions for Harvia API clients."""


class HarviaAuthError(Exception):
    """Authentication failed."""


class HarviaConnectionError(Exception):
    """Connection to API failed."""
