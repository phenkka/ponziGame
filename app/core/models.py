"""
Pydantic модели для API запросов и ответов.
"""
from pydantic import BaseModel


class AuthRequest(BaseModel):
    """Модель запроса для авторизации."""
    wallet: str
    signature: list
    message: str

