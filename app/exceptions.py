from __future__ import annotations

from typing import Optional


class BusinessError(Exception):
    """Domain rule violation shown to the operator as a flash message.

    ``message_key`` points at app/locales/*.json; ``params`` fill its
    ``{placeholders}`` (e.g. the material name in error.materialQuantity).
    """

    def __init__(self, message: str, message_key: Optional[str] = None, **params: str):
        super().__init__(message)
        self.message = message
        self.message_key = message_key
        self.params = {k: str(v) for k, v in params.items()}
