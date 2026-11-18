from __future__ import annotations

from typing import Optional


class BusinessError(Exception):
    """Domain rule violation shown to the operator as a flash message."""

    def __init__(self, message: str, message_key: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.message_key = message_key
