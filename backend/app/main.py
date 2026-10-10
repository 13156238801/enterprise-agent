from fastapi import FastAPI

app = FastAPI(title="Enterprise Agent API", version="0.1.0")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
