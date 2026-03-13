"""
API Server — receives mint data from PC scraper
POST /api/mint  →  stores mint + triggers alert
GET  /health    →  Railway health check
"""
import asyncio
import json
import logging
from aiohttp import web
from config import API_SECRET_KEY, API_PORT

logger = logging.getLogger(__name__)

_app_ref = None   # telegram Application reference set by bot.py


def set_telegram_app(app):
    global _app_ref
    _app_ref = app


async def _handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _handle_post_mint(request: web.Request) -> web.Response:
    # Optional secret key check
    if API_SECRET_KEY:
        auth = request.headers.get("X-API-Key", "")
        if auth != API_SECRET_KEY:
            return web.json_response({"error": "unauthorized"}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    required = ("name", "chain", "phases")
    for field in required:
        if field not in body:
            return web.json_response({"error": f"missing field: {field}"}, status=400)

    # Validate phases
    if not isinstance(body["phases"], list) or len(body["phases"]) == 0:
        return web.json_response({"error": "phases must be a non-empty list"}, status=400)

    # Build mint dict — reuse or create DB record
    from database import (
        get_all_mints, add_mint, update_mint, get_channels
    )

    name = body["name"].strip()
    chain = body.get("chain", "Ethereum")
    mint_link = body.get("mint_link", "")
    twitter = body.get("twitter", "")
    discord = body.get("discord", "")
    total_supply = int(body.get("supply", 0))
    minted = int(body.get("minted", 0))

    # Normalise phases to the internal format
    phases = []
    for p in body["phases"]:
        phases.append({
            "name":  p.get("name", "Phase"),
            "time":  p.get("time", ""),
            "price": p.get("price", "TBA"),
            "limit": p.get("limit", "N/A"),
        })

    # Check if a mint with the same name already exists — update it
    existing = next((m for m in get_all_mints() if m["name"].lower() == name.lower()), None)
    if existing:
        update_mint(
            existing["id"],
            chain=chain,
            mint_link=mint_link,
            phases=phases,
            x_link=twitter,
            discord_link=discord,
            total_supply=total_supply,
            minted=minted,
            status="upcoming",
        )
        mint_id = existing["id"]
        from database import get_mint
        mint = get_mint(mint_id)
        action = "updated"
    else:
        mint_id = add_mint(name, chain, mint_link, phases=phases)
        # Set extra fields
        update_mint(
            mint_id,
            x_link=twitter,
            discord_link=discord,
            total_supply=total_supply,
            minted=minted,
        )
        from database import get_mint
        mint = get_mint(mint_id)
        action = "created"

    logger.info(f"[api] Mint {action}: {name} (id={mint_id})")

    # Trigger alert asynchronously
    if _app_ref:
        from handlers.alerts import trigger_mint_alert_from_api
        asyncio.ensure_future(trigger_mint_alert_from_api(mint))

    return web.json_response({
        "status": "ok",
        "action": action,
        "mint_id": mint_id,
        "name": name,
    })


async def start_api_server():
    """Start aiohttp web server. Called from bot.py post_init."""
    app = web.Application()
    app.router.add_get("/health", _handle_health)
    app.router.add_post("/api/mint", _handle_post_mint)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", API_PORT)
    await site.start()
    logger.info(f"API server running on port {API_PORT}")
    return runner
