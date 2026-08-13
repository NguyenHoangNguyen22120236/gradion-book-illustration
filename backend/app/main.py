from fastapi import FastAPI

app = FastAPI(title="Gradion Book Illustration API")


@app.get("/api/health")
def health() -> dict[str, str]:
    """Report whether the API process is ready to receive requests."""
    return {"status": "ok"}

