"""Native Hermes directory entry point; all runtime code stays in this checkout."""

from .src.software_factory import register

__all__ = ["register"]
