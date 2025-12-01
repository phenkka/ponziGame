from fastapi import Depends, Request
from fastapi.responses import FileResponse, RedirectResponse

from core.sessions import verify_session_cookie


def setup_page_routes(app):
    """
    Роуты для HTML страниц.
    Защищенные страницы проверяют cookie авторизации.
    """
    # Главная страница - публичная
    @app.get("/")
    async def read_root():
        return FileResponse("public/index.html")
    
    @app.get("/shop")
    async def shop_page(request: Request, auth: dict = Depends(verify_session_cookie)):
        return FileResponse("public/coming-soon.html")

    @app.get("/battle")
    async def battle_page(request: Request, auth: dict = Depends(verify_session_cookie)):
        return FileResponse("public/coming-soon.html")

    @app.get("/cards")
    async def cards_page(request: Request, auth: dict = Depends(verify_session_cookie)):
        return FileResponse("public/coming-soon.html")

    @app.get("/profile")
    async def profile_page(request: Request, auth: dict = Depends(verify_session_cookie)):
        return FileResponse("public/referral.html")

    @app.get("/rules")
    async def rules_page(request: Request, auth: dict = Depends(verify_session_cookie)):
        return FileResponse("public/rules.html")