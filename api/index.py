from fastapi import FastAPI

from api.analyze import router as analyze_router
from api.health import router as health_router

app = FastAPI(title="RCA Chatbot API")
app.include_router(analyze_router)
app.include_router(health_router)
