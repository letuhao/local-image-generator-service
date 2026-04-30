from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_auth

router = APIRouter()


@router.get("/v1/models")
async def list_models(
    request: Request,
    kid: str = Depends(require_auth),
) -> dict:
    """OpenAI-compatible model list, plus capabilities + backend per arch §6.4."""
    registry = request.app.state.registry
    preset_registry = request.app.state.preset_registry
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": cfg.name,
                "object": "model",
                "created": now,
                "owned_by": "local",
                "capabilities": cfg.capabilities,
                "backend": cfg.backend,
                "family": cfg.family,
                "description": next(
                    (p.description for p in preset_registry.confirmed_for_model(cfg.name)),
                    None,
                ),
                "supported_asset_types": sorted(
                    {
                        p.asset_type
                        for p in preset_registry.all()
                        if p.model == cfg.name
                    }
                ),
                "has_confirmed_combo": any(
                    p.confirmed for p in preset_registry.all() if p.model == cfg.name
                ),
            }
            for cfg in registry.all()
        ],
    }


@router.get("/v1/catalog/models")
async def catalog_models(
    request: Request,
    kid: str = Depends(require_auth),
) -> dict:
    registry = request.app.state.registry
    preset_registry = request.app.state.preset_registry
    items = []
    for cfg in registry.all():
        confirmed = preset_registry.confirmed_for_model(cfg.name)
        items.append(
            {
                "id": cfg.name,
                "backend": cfg.backend,
                "family": cfg.family,
                "capabilities": cfg.capabilities,
                "description": confirmed[0].description if confirmed else None,
                "supported_asset_types": sorted(
                    {
                        p.asset_type
                        for p in preset_registry.all()
                        if p.model == cfg.name
                    }
                ),
                "defaults": cfg.defaults,
                "limits": cfg.limits,
                "confirmed_presets": [p.id for p in confirmed],
            }
        )
    return {"object": "list", "data": items}


@router.get("/v1/catalog/presets")
async def catalog_presets(
    request: Request,
    kid: str = Depends(require_auth),
) -> dict:
    preset_registry = request.app.state.preset_registry
    return {
        "object": "list",
        "data": [
            {
                "id": p.id,
                "version": p.version,
                "asset_type": p.asset_type,
                "status": p.status,
                "confirmed": p.confirmed,
                "description": p.description,
                "model": p.model,
                "loras": [{"name": l.name, "weight": l.weight} for l in p.loras],
            }
            for p in preset_registry.all()
        ],
    }


@router.get("/v1/catalog/presets/{preset_id}")
async def catalog_preset_by_id(
    preset_id: str,
    request: Request,
    kid: str = Depends(require_auth),
) -> dict:
    preset_registry = request.app.state.preset_registry
    try:
        p = preset_registry.get(preset_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"preset not found: {preset_id!r}") from None
    return {
        "id": p.id,
        "version": p.version,
        "asset_type": p.asset_type,
        "status": p.status,
        "confirmed": p.confirmed,
        "description": p.description,
        "bundle": {
            "model": p.model,
            "loras": [{"name": l.name, "weight": l.weight} for l in p.loras],
        },
        "generation_defaults": p.generation_defaults,
        "supported_asset_types": list(p.supported_asset_types),
        "variables_schema": list(p.variables_schema),
    }
