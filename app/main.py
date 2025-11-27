from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

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

    app.mount("/css", StaticFiles(directory="public/css"), name="css")
    app.mount("/img", StaticFiles(directory="public/img"), name="img")
    app.mount("/scripts", StaticFiles(directory="public/scripts"), name="scripts")
    
    # Настраиваем роуты
    setup_page_routes(app)
    setup_api_routes(app)
    
    return app

app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
