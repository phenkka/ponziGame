from pydantic import BaseModel


class AuthRequest(BaseModel):
    wallet: str
    signature: list
    message: str

