from typing import Optional

from pydantic import BaseModel


class AuthRequest(BaseModel):
    wallet: str
    signature: list
    message: str
    referrerCode: Optional[str] = None

