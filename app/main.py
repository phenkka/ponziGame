from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import asyncio
import os

from core.auth import auth_middleware
from core.utils import get_db_connection
from routes.pages import setup_page_routes
from routes.api import setup_api_routes
from services.polymarket_sync import sync_polymarket_top_popular_markets


def create_app() -> FastAPI:
    app = FastAPI(title="Tired Card Game API")

    def _seed_dev_test_prediction():
        if os.getenv("DEV_TEST_PREDICTION_ENABLED", "0") != "1":
            return

        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO public.predictions (
                    polymarket_id, title, description, category,
                    outcome_a, outcome_b,
                    outcome_a_probability, outcome_b_probability,
                    outcome_a_odds, outcome_b_odds,
                    resolution_date, volume_24h, volume_7d, volume_30d,
                    status, updated_at
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    now() + interval '3 minutes',
                    %s, %s, %s,
                    'active', now()
                )
                ON CONFLICT (polymarket_id) DO UPDATE
                SET title = EXCLUDED.title,
                    description = EXCLUDED.description,
                    category = EXCLUDED.category,
                    outcome_a = EXCLUDED.outcome_a,
                    outcome_b = EXCLUDED.outcome_b,
                    outcome_a_probability = EXCLUDED.outcome_a_probability,
                    outcome_b_probability = EXCLUDED.outcome_b_probability,
                    outcome_a_odds = EXCLUDED.outcome_a_odds,
                    outcome_b_odds = EXCLUDED.outcome_b_odds,
                    resolution_date = EXCLUDED.resolution_date,
                    status = 'active',
                    winner_outcome = NULL,
                    updated_at = now()
                """,
                (
                    "DEV_TEST_3MIN_5050",
                    "Dev Test: 3-min 50/50",
                    "Development-only manual test prediction (3 minutes).",
                    "dev",
                    "Yes",
                    "No",
                    50.0,
                    50.0,
                    2.0,
                    2.0,
                    999999999.0,
                    999999999.0,
                    999999999.0,
                ),
            )

            conn.commit()
        except Exception as e:
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass
            print(f"Dev test prediction seed error: {e}")
        finally:
            try:
                if cursor:
                    cursor.close()
            except Exception:
                pass
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
    
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

    @app.on_event("startup")
    async def _start_predictions_sync():
        await asyncio.to_thread(_seed_dev_test_prediction)

        if os.getenv("PREDICTIONS_SYNC_ENABLED", "1") != "1":
            return

        interval = int(os.getenv("PREDICTIONS_SYNC_INTERVAL_SECONDS", "300"))
        target = int(os.getenv("PREDICTIONS_SYNC_TARGET", "10"))

        async def _loop():
            while True:
                await asyncio.to_thread(sync_polymarket_top_popular_markets, target)
                await asyncio.sleep(interval)

        app.state.predictions_sync_task = asyncio.create_task(_loop())

    @app.on_event("shutdown")
    async def _stop_predictions_sync():
        task = getattr(app.state, "predictions_sync_task", None)
        if not task:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    
    return app

app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
