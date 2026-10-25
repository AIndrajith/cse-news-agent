import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def setup_cors(app: FastAPI) -> None:
    frontend_domain = os.getenv("FRONTEND_DOMAIN", "http://localhost:5173").strip()

    origins = [
        frontend_domain,
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(set(origins)),  # set removes any duplicates
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
