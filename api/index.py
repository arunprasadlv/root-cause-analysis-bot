from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from api.analyze import router as analyze_router
from api.health import router as health_router

app = FastAPI(title="RCA Chatbot API")
app.include_router(analyze_router)
app.include_router(health_router)

_index_html = Path(__file__).parent.parent / "frontend" / "index.html"


@app.get("/")
def root():
    return FileResponse(_index_html)
