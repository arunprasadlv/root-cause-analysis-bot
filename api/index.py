import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from analyze import router as analyze_router
from health import router as health_router

app = FastAPI(title="RCA Chatbot API")
app.include_router(analyze_router)
app.include_router(health_router)

_root = Path(__file__).parent.parent
_index_html = _root / "frontend" / "index.html"
_chat_html   = _root / "frontend" / "chat.html"
_reports_dir = _root / "eval" / "reports"


@app.get("/")
def root():
    return FileResponse(_index_html)


@app.get("/chat")
def chat():
    return FileResponse(_chat_html)


@app.get("/eval-report")
def eval_report(phase: str = "phase6_production"):
    report_file = _reports_dir / f"{phase}.html"
    if not report_file.exists():
        raise HTTPException(status_code=404, detail=f"Report not found: {phase}")
    return FileResponse(report_file, media_type="text/html")
