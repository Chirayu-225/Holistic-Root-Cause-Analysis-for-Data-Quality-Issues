from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.rca import router as rca_router

app = FastAPI(
    title="Holistic RCA Framework API",
    description="Lineage-adaptive root cause analysis for data quality defects.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://frontend:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rca_router)


@app.get("/")
def root():
    return {"message": "Holistic RCA Framework API", "docs": "/docs"}
