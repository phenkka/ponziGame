"""
Роуты для HTML страниц.
"""
from fastapi import Depends
from fastapi.responses import FileResponse
from core.auth import verify_auth


def setup_page_routes(app):
    """
    Настраивает роуты для HTML страниц.
    """
    # Главная страница - публичная
    @app.get("/")
    async def read_root():
        return FileResponse("public/index.html")
    
    # Остальные HTML страницы - ЗАЩИЩЕНЫ АВТОРИЗАЦИЕЙ
    @app.get("/shop")
    async def shop_page(auth: dict = Depends(verify_auth)):
        return FileResponse("public/shop.html")
    
    @app.get("/battle")
    async def battle_page(auth: dict = Depends(verify_auth)):
        return FileResponse("public/battle.html")
    
    @app.get("/cards")
    async def cards_page(auth: dict = Depends(verify_auth)):
        return FileResponse("public/cards.html")
    
    @app.get("/profile")
    async def profile_page(auth: dict = Depends(verify_auth)):
        return FileResponse("public/referral.html")
    
    @app.get("/rules")
    async def rules_page(auth: dict = Depends(verify_auth)):
        return FileResponse("public/rules.html")

