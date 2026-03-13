"""
Configuration - Edit these settings before running the bot
"""
import os
from dotenv import load_dotenv

# Load .env file automatically
load_dotenv()

# ============================================================
# REQUIRED SETTINGS - You MUST fill these in
# ============================================================

# Your Telegram Bot Token (get from @BotFather on Telegram)
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Your Telegram User ID (get from @userinfobot on Telegram)
# Only this user can access the admin dashboard
ADMIN_USER_IDS = [
    int(x) for x in os.getenv("ADMIN_IDS", "123456789").split(",") if x.strip()
]

# ============================================================
# OPTIONAL SETTINGS
# ============================================================

# How many minutes before each mint phase to send alerts (first alert)
ALERT_MINUTES_BEFORE = int(os.getenv("ALERT_MINUTES_BEFORE", "15"))

# Second alert: when mint starts (0 minutes before)
ALERT_MINUTES_LIVE = 0

# Daily summary time (24h format, UTC) - send at 10:00 UTC
DAILY_SUMMARY_HOUR = int(os.getenv("DAILY_SUMMARY_HOUR", "10"))
DAILY_SUMMARY_MINUTE = int(os.getenv("DAILY_SUMMARY_MINUTE", "0"))

# Database file location
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/mints.db")

# ============================================================
# PLATFORM API KEYS (optional but enables auto-detection)
# ============================================================

# OpenSea API key — enables full phase auto-detection for opensea.io links
# Get yours free at: https://docs.opensea.io/reference/api-keys
OPENSEA_API_KEY = os.getenv("OPENSEA_API_KEY", "")

# ============================================================
# SMART FEATURES
# ============================================================

# How often to check mint status (in seconds)
STATUS_CHECK_INTERVAL = int(os.getenv("STATUS_CHECK_INTERVAL", "60"))

# Floor monitor: check interval in seconds
FLOOR_CHECK_INTERVAL = int(os.getenv("FLOOR_CHECK_INTERVAL", "60"))

# Floor pump threshold (0.5 = 50% increase triggers alert)
FLOOR_PUMP_THRESHOLD = float(os.getenv("FLOOR_PUMP_THRESHOLD", "0.5"))

# Sweep detection: number of NFTs bought in window to trigger alert
SWEEP_COUNT_THRESHOLD = int(os.getenv("SWEEP_COUNT_THRESHOLD", "10"))

# Sweep detection: time window in seconds
SWEEP_WINDOW_SECONDS = int(os.getenv("SWEEP_WINDOW_SECONDS", "60"))

# API endpoint secret key (optional, for PC scraper authentication)
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "")

# Port for the API server (Railway sets PORT automatically)
API_PORT = int(os.getenv("PORT", os.getenv("API_PORT", "8080")))
