from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import audit, events, metrics
from app.api.routes import live_status, customers, gate_rules, comms
from app.core.config import settings

app = FastAPI(title=settings.app_name, version="5.0.0")

# CORS — allow the Vite dev server (localhost:5173) and any other configured origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, "http://localhost:5173", "http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(events.router)
app.include_router(metrics.router)
app.include_router(audit.router)
app.include_router(live_status.router)
app.include_router(customers.router)
app.include_router(gate_rules.router)
app.include_router(comms.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "5.0.0"}
