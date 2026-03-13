"""
PC Scraper Example
------------------
Run this on your local PC to push mint data to the bot running on Railway.
The bot's API endpoint avoids 403 blocks that Railway would get from OpenSea.

Usage:
  1. Set BOT_API_URL to your Railway deployment URL
  2. Set API_SECRET_KEY if you configured one in Railway env vars
  3. Run: python pc_scraper_example.py
"""
import asyncio
import aiohttp
import json
from datetime import datetime, timedelta

# ── CONFIG ──────────────────────────────────────────────────
BOT_API_URL = "https://mint-alarm-production.up.railway.app"
API_SECRET_KEY = "9fA7K2xQ4Lm8TzR6Wb1H"


# ── SEND MINT TO BOT ─────────────────────────────────────────

async def send_mint(mint_data: dict):
    """POST mint data to the bot's API endpoint."""
    url = f"{BOT_API_URL}/api/mint"
    headers = {"Content-Type": "application/json"}
    if API_SECRET_KEY:
        headers["X-API-Key"] = API_SECRET_KEY

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=mint_data, headers=headers) as resp:
            body = await resp.json()
            print(f"[{resp.status}] {mint_data['name']}: {body}")
            return body


# ── EXAMPLE PAYLOAD ──────────────────────────────────────────

async def main():
    # Example: a mint with two phases
    now = datetime.utcnow()
    wl_time  = (now + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    pub_time = (now + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")

    mint_data = {
        "name":       "Example Collection",
        "chain":      "Ethereum",
        "supply":     5000,
        "minted":     0,
        "mint_link":  "https://example.io/mint",
        "twitter":    "https://x.com/example",
        "discord":    "https://discord.gg/example",
        "phases": [
            {
                "name":  "WL",
                "price": "0.005 ETH",
                "time":  wl_time,
            },
            {
                "name":  "Public",
                "price": "0.008 ETH",
                "time":  pub_time,
            },
        ],
    }

    await send_mint(mint_data)


if __name__ == "__main__":
    asyncio.run(main())
