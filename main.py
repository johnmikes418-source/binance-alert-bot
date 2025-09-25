import os
import sys
import requests
import logging
from datetime import datetime
import pytz
from flask import Flask, request
from apscheduler.schedulers.background import BackgroundScheduler
import imghdr2 as imghdr
from bs4 import BeautifulSoup

# Fix PIL/telegram bug
sys.modules["imghdr"] = imghdr

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

CMC_NEW_URL = "https://coinmarketcap.com/new/"
CMC_UPCOMING_URL = "https://coinmarketcap.com/upcoming/"
BINANCE_ALPHA_URL = "https://www.binance.com/en/markets/alpha-BSC"

app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)

logging.basicConfig(level=logging.INFO)

# ---------------- HEALTH ROUTE ----------------
@app.route("/")
def home():
    return "✅ Bot is running", 200

# ---------------- HELPERS ----------------
def fetch_max_supply(symbol):
    """Fetch max supply from individual CMC coin page"""
    try:
        url = f"https://coinmarketcap.com/currencies/{symbol.lower()}/"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        supply_elem = soup.find("div", string=lambda t: t and "Max Supply" in t)
        if supply_elem:
            val_elem = supply_elem.find_next("div")
            if val_elem:
                raw = val_elem.get_text(strip=True).replace(",", "").split(" ")[0]
                return float(raw)
    except Exception as e:
        logging.warning(f"Supply fetch failed for {symbol}: {e}")
    return None

# ---------------- FETCHERS ----------------
def fetch_cmc_new(limit=20):
    """Scrape new tokens from CoinMarketCap /new/ page"""
    try:
        r = requests.get(CMC_NEW_URL, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        tokens = []
        rows = soup.select("table tbody tr")
        for row in rows[:limit]:
            cols = row.find_all("td")
            if len(cols) < 5:
                continue

            name_elem = cols[1].find("p")
            symbol_elem = cols[2].find("p")

            name = name_elem.get_text(strip=True) if name_elem else "Unknown"
            symbol = symbol_elem.get_text(strip=True) if symbol_elem else "UNK"

            price_text = cols[3].get_text(strip=True).replace("$", "").replace(",", "")
            try:
                price = float(price_text)
            except:
                price = 0

            change_text = cols[4].get_text(strip=True).replace("%", "")
            try:
                change = float(change_text)
            except:
                change = 0

            supply = fetch_max_supply(symbol)

            tokens.append(
                {
                    "name": name,
                    "symbol": symbol,
                    "price": price,
                    "change": change,
                    "supply": supply,
                }
            )
        return tokens
    except Exception as e:
        logging.error(f"CMC /new scrape error: {e}")
        return []

def fetch_cmc_upcoming(limit=20):
    """Scrape upcoming tokens from CoinMarketCap /upcoming/ page"""
    try:
        r = requests.get(CMC_UPCOMING_URL, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        results = []
        rows = soup.select("table tbody tr")
        for row in rows[:limit]:
            cols = row.find_all("td")
            if len(cols) < 3:
                continue

            name_elem = cols[1].find("p")
            date_elem = cols[2].get_text(strip=True)

            name = name_elem.get_text(strip=True) if name_elem else "Unknown"
            symbol = name.split(" ")[-1].replace("(", "").replace(")", "")

            # Parse date if possible
            listing_date = None
            try:
                listing_date = datetime.strptime(date_elem, "%b %d, %Y")
                listing_date = listing_date.replace(tzinfo=timezone.utc)
            except Exception as e:
                logging.debug(f"Could not parse listing date: {date_elem} ({e})")

            results.append(
                {
                    "name": name,
                    "symbol": symbol,
                    "date": listing_date,
                    "url": CMC_UPCOMING_URL,
                    "source": "CMC Upcoming",
                }
            )
        return results
    except Exception as e:
        logging.error(f"CMC /upcoming scrape error: {e}")
        return []

def fetch_binance_alpha(limit=20):
    """Scrape Binance Alpha page for upcoming tokens"""
    try:
        r = requests.get(BINANCE_ALPHA_URL, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        results = []
        cards = soup.select("a[href*='/en/trade/']")
        for card in cards[:limit]:
            name = card.get("title", "Unknown")
            symbol = name.split(" ")[0]
            url = "https://www.binance.com" + card["href"]

            # Try to extract date if available
            date_elem = card.find("div", string=lambda t: t and any(y in t for y in ["2025", "2026"]))
            listing_date = None
            if date_elem:
                try:
                    listing_date = datetime.strptime(date_elem.get_text(strip=True), "%Y-%m-%d")
                    listing_date = listing_date.replace(tzinfo=timezone.utc)
                except:
                    pass

            results.append(
                {
                    "name": name,
                    "symbol": symbol,
                    "date": listing_date,
                    "url": url,
                    "source": "Binance Alpha",
                }
            )
        return results
    except Exception as e:
        logging.error(f"Binance Alpha scrape error: {e}")
        return []

# ---------------- FILTERS ----------------
def token_filter(token):
    price = token["price"]
    supply = token["supply"]

    if supply:
        if supply <= 1_000_000_000 and 0.005 <= price <= 0.05:
            return True
        if supply <= 10_000_000_000 and 0.0005 <= price <= 0.005:
            return True
    else:
        if price <= 0.05:
            return True
    return False

def alpha_filter(token):
    """Skip past or >30d future listings"""
    if token["date"]:
        now = datetime.now(pytz.utc)
        delta = (token["date"] - now).days
        if delta < 0 or delta > 30:
            return False
    return True

# ---------------- ALERTS ----------------
def cmc_link(symbol):
    return f"https://coinmarketcap.com/currencies/{symbol.lower()}/"

def dexscreener_link(symbol):
    return f"https://dexscreener.com/search?q={symbol}"

def new_crypto_alert():
    fresh = [t for t in fetch_cmc_new(30) if token_filter(t)]
    if not fresh:
        return "✅ No new cryptos match your filters."

    msg = f"🆕 New Crypto Alerts\n{datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
    for i, t in enumerate(fresh, start=1):
        supply_str = f"{t['supply']:,}" if t["supply"] else "?"
        msg += (
            f"{i}. 💎 {t['name']} ({t['symbol']}/USDT)\n"
            f"💰 Price: ${t['price']:.6f}\n"
            f"📈 24h Change: {t['change']:+.2f}%\n"
            f"🔄 Supply: {supply_str}\n"
            f"🔗 [CMC]({cmc_link(t['symbol'])}) | [DexScreener]({dexscreener_link(t['symbol'])})\n\n"
        )
    return msg

def alpha_alert():
    cmc = [t for t in fetch_cmc_upcoming(30) if alpha_filter(t)]
    binance = [t for t in fetch_binance_alpha(20) if alpha_filter(t)]

    alphas = cmc + binance
    if not alphas:
        return "🚀 No valid upcoming listings right now."

    msg = f"🚀 New Alpha Alerts (Upcoming Listings)\n{datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
    for i, t in enumerate(alphas, start=1):
        date_str = t["date"].strftime("%Y-%m-%d") if t["date"] else "Unknown date"
        msg += (
            f"{i}. 💎 {t['name']} ({t['symbol']})\n"
            f"📅 First Listing: {date_str}\n"
            f"📌 Source: {t['source']}\n"
            f"🔗 [More Info]({t['url']})\n\n"
        )
    return msg
   
from apscheduler.schedulers.background import BackgroundScheduler  # ✅ Keep this at the top

# ---------------- ALERTS ----------------
def cmc_link(symbol):
    return f"https://coinmarketcap.com/currencies/{symbol.lower()}/"

def dexscreener_link(symbol):
    return f"https://dexscreener.com/search?q={symbol}"

# (keep the rest of your alert functions here...)

def send_alerts():
    try:
        bot.send_message(chat_id=CHAT_ID, text=new_crypto_alert(), parse_mode="Markdown", disable_web_page_preview=True)
        bot.send_message(chat_id=CHAT_ID, text=alpha_alert(), parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        logging.error(f"Telegram alert failed: {e}")

# ---------------- MAIN ----------------
if __name__ == "__main__":
    # ✅ Start scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(send_alerts, "interval", minutes=60, timezone=pytz.utc)
    scheduler.start()

    # ✅ Start Flask app to keep Render alive
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
