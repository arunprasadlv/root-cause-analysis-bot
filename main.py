import uvicorn

from api.index import app  # noqa: F401 — shared with Vercel entry point

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
