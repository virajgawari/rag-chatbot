"""API v1 Router aggregating health, document, and chat endpoints."""
from fastapi import APIRouter
from backend.api.v1.health import router as health_router
from backend.api.v1.documents import router as documents_router
from backend.api.v1.chat import router as chat_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(documents_router)
api_router.include_router(chat_router)
