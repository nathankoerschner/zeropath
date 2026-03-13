from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import comparison, findings, health, repositories, scans

app = FastAPI(
    title="ZeroPath API",
    version="0.1.0",
    description="LLM-powered Python security scanner platform",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(repositories.router)
app.include_router(scans.router)
app.include_router(findings.router)
app.include_router(comparison.router)


@app.get("/")
async def root():
    return {"service": "zeropath-api", "status": "ok"}
