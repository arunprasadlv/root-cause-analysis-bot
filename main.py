from fastapi import FastAPI
import uvicorn

from api.analyze import router as analyze_router
from api.health import router as health_router

app = FastAPI(title="RCA Chatbot API")
app.include_router(analyze_router)
app.include_router(health_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
