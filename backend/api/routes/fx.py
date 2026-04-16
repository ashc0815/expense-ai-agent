"""FX rates endpoint — returns mock rate table for frontend currency dropdowns."""
from __future__ import annotations

from fastapi import APIRouter

from backend.services.fx_service import get_all_rates

router = APIRouter()


@router.get("/rates")
async def get_fx_rates():
    return get_all_rates()
