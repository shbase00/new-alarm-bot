"""
Start Script
------------
Run this ONE script instead of running pc_server.py and ngrok separately.

It will:
  1. Start your PC scraper server
  2. Start ngrok tunnel
  3. Automatically update Railway with the new ngrok URL
  4. Keep everything running

Requirements:
  pip install aiohttp requests
  ngrok must be installed and authenticated

Usage:
  python start.py
"""
import subprocess
import asyncio
import time
import sys
import os
NGROK_PATH = os.path.expanduser(
    r"~\AppData\Local\Microsoft\WindowsApps\ngrok.exe"
)
import json
import threading

# ── CONFIG ───────────────────────────────────────────────────
RAILWAY_API_TOKEN  = "a82a6b15-653d-4f1d-915b-0911ce70d0c4"
RAILWAY_PROJECT_ID = "259c0af3-0562-40ce-a11d-ec7e9ea98f8b"
RAILWAY_SERVICE_ID = "8ee7f6f1-42ff-441a-8559-4ace79d1bdc1"
RAILWAY_ENV_ID     = "e45ea477-2bf0-40ba-8156-cbcaa801ab16"
PORT               = 7842
# ─────────────────────────────────────────────────────────────


def get_ngrok_url(timeout=15) -> str:
    """Poll ngrok's local API until we get the public URL."""
    import urllib.request, json as _json
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=2) as r:
                data = _json.loads(r.read())
                tunnels = data.get("tunnels", [])
                for t in tunnels:
                    if t.get("proto") == "https":
                        return t["public_url"]
        except Exception:
            pass
        time.sleep(1)
    return ""


def update_railway_env(ngrok_url: str) -> bool:
    """Update PC_SCRAPER_URL in Railway environment variables via GraphQL API."""
    if not all([RAILWAY_API_TOKEN, RAILWAY_PROJECT_ID, RAILWAY_SERVICE_ID, RAILWAY_ENV_ID]):
        print("  ⚠  Railway credentials not configured in start.py")
        print(f"  👉 Please manually set PC_SCRAPER_URL = {ngrok_url} in Railway Variables")
        return False

    import urllib.request, urllib.error, json as _json

    query = """
    mutation UpsertVariables($input: VariableCollectionUpsertInput!) {
      variableCollectionUpsert(input: $input)
    }
    """
    variables = {
        "input": {
            "projectId":     RAILWAY_PROJECT_ID,
            "serviceId":     RAILWAY_SERVICE_ID,
            "environmentId": RAILWAY_ENV_ID,
            "variables": {
                "PC_SCRAPER_URL": ngrok_url
            }
        }
    }

    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://backboard.railway.app/graphql/v2",
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {RAILWAY_API_TOKEN}",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            resp = _json.loads(r.read())
            if resp.get("data", {}).get("variableCollectionUpsert"):
                return True
            print(f"  ⚠  Railway API response: {resp}")
            return False
    except Exception as e:
        print(f"  ⚠  Failed to update Railway: {e}")
        return False


def kill_port(port: int):
    """Kill any process using the given port."""
    import subprocess
    try:
        result = subprocess.run(
            f'netstat -ano | findstr :{port}',
            shell=True, capture_output=True, text=True
        )
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            if parts and parts[-1].isdigit():
                pid = parts[-1]
                subprocess.run(f'taskkill /PID {pid} /F', shell=True,
                               capture_output=True)
        time.sleep(1)
    except Exception:
        pass


def run_pc_server():
    """Run pc_server.py in a background thread."""
    script = os.path.join(os.path.dirname(__file__), "pc_server.py")
    subprocess.run([sys.executable, script])


def main():
    print("╔══════════════════════════════════════════════╗")
    print("║       NFT Mint Bot — PC Bridge               ║")
    print("╚══════════════════════════════════════════════╝")
    print()

    # ── Kill anything already on the port ──
    kill_port(PORT)

    # ── Start pc_server.py in background ──
    print("  [1/3] Starting PC scraper server...")
    t = threading.Thread(target=run_pc_server, daemon=True)
    t.start()
    time.sleep(2)
    print("        ✅ PC server running on port", PORT)

    # ── Start ngrok ──
    print("  [2/3] Starting ngrok tunnel...")
    ngrok_proc = subprocess.Popen(
        [NGROK_PATH, "http", str(PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)

    ngrok_url = get_ngrok_url(timeout=15)
    if not ngrok_url:
        print("        ❌ Could not get ngrok URL.")
        print("           Make sure ngrok is installed and authenticated.")
        print("           Run: ngrok config add-authtoken YOUR_TOKEN")
        ngrok_proc.terminate()
        sys.exit(1)

    print(f"        ✅ ngrok URL: {ngrok_url}")

    # ── Update Railway ──
    print("  [3/3] Updating Railway with new URL...")
    if RAILWAY_API_TOKEN:
        ok = update_railway_env(ngrok_url)
        if ok:
            print("        ✅ Railway updated automatically!")
        else:
            print(f"        ⚠  Update failed — set manually in Railway:")
            print(f"           PC_SCRAPER_URL = {ngrok_url}")
    else:
        print(f"        ℹ  Railway auto-update not configured.")
        print(f"           Set this in Railway Variables tab:")
        print(f"           PC_SCRAPER_URL = {ngrok_url}")

    print()
    print("  ✅ Everything is running!")
    print("  📱 Your Telegram bot will now use your PC for scraping.")
    print()
    print("  Press Ctrl+C to stop.")
    print()

    try:
        while True:
            time.sleep(10)
            # Check ngrok is still alive
            if ngrok_proc.poll() is not None:
                print("  ⚠  ngrok stopped. Restarting...")
                ngrok_proc = subprocess.Popen(
                    [NGROK_PATH, "http", str(PORT)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(3)
                new_url = get_ngrok_url(timeout=15)
                if new_url and new_url != ngrok_url:
                    ngrok_url = new_url
                    print(f"  New ngrok URL: {ngrok_url}")
                    if RAILWAY_API_TOKEN:
                        update_railway_env(ngrok_url)
                    else:
                        print(f"  ⚠  Update Railway manually: PC_SCRAPER_URL = {ngrok_url}")
    except KeyboardInterrupt:
        print("\n  Shutting down...")
        ngrok_proc.terminate()
        print("  Stopped. Goodbye!")


if __name__ == "__main__":
    main()
