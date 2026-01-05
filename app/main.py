from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from core.auth import auth_middleware
from routes.pages import setup_page_routes
from routes.api import setup_api_routes


def create_app() -> FastAPI:
    app = FastAPI(title="Tired Card Game API")
    
    # CORS middleware для работы с фронтендом
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # В продакшене лучше указать конкретные домены
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Middleware для проверки авторизации
    app.middleware("http")(auth_middleware)

    base_dir = Path(__file__).resolve().parent
    public_dir = base_dir / "public"
    app.mount("/css", StaticFiles(directory=str(public_dir / "css")), name="css")
    app.mount("/img", StaticFiles(directory=str(public_dir / "img")), name="img")
    app.mount("/scripts", StaticFiles(directory=str(public_dir / "scripts")), name="scripts")
    
    # Настраиваем роуты
    setup_page_routes(app)
    setup_api_routes(app)
    
    return app

app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
