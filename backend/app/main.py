from fastapi import FastAPI

from app.api.routes import audit, events, metrics
from app.core.config import settings

app = FastAPI(title=settings.app_name)

app.include_router(events.router)
app.include_router(metrics.router)
app.include_router(audit.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
