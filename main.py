import os
import sys
import logging
from datetime import datetime, timezone
import pytz
import requests
from flask import Flask, request
from bs4 import BeautifulSoup
import imghdr2 as imghdr

from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Dispatcher, CommandHandler, CallbackQueryHandler, CallbackContext

# Patch telegram bug
sys.modules["imghdr"] = imghdr

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
WEBHOOK_URL = "https://binance-alert-bot.onrender.com/webhook"

CMC_NEW_URL = "https://coinmarketcap.com/new/"
CMC_UPCOMING_URL = "https://coinmarketcap.com/upcoming/"
BINANCE_ALPHA_URL = "https://www.binance.com/en/markets/alpha-BSC"

app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)
bot.set_webhook(url=WEBHOOK_URL)

logging.basicConfig(level=logging.INFO)

# ---------------- DISPATCHER ----------------
dispatcher = Dispatcher(bot, None, workers=1)

# ---------------- ROUTES ----------------
@app.route("/")
def home():
    return "✅ Bot is running", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    if request.method == "POST":
        update = Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
        return "Webhook received", 200

# ---------------- FETCH SUPPLY ----------------
def fetch_max_supply(symbol):
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

# ---------------- FETCH CRYPTOS ----------------
def fetch_cmc_new(limit=30):
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
            price = float(price_text) if price_text else 0

            change_text = cols[4].get_text(strip=True).replace("%", "")
            change = float(change_text) if change_text else 0

            supply = fetch_max_supply(symbol)

            tokens.append({
                "name": name,
                "symbol": symbol,
                "price": price,
                "change": change,
                "supply": supply,
            })
        return tokens
    except Exception as e:
        logging.error(f"CMC /new scrape error: {e}")
        return []

def fetch_cmc_upcoming(limit=30):
    try:
        r = requests.get(CMC_UPCOMING_URL, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        tokens = []
        rows = soup.select("table tbody tr")
        for row in rows[:limit]:
            cols = row.find_all("td")
            if len(cols) < 3:
                continue

            name_elem = cols[1].find("p")
            date_elem = cols[2].get_text(strip=True)

            name = name_elem.get_text(strip=True) if name_elem else "Unknown"
            symbol = name.split(" ")[-1].replace("(", "").replace(")", "")

            date = None
            try:
                date = datetime.strptime(date_elem, "%b %d, %Y")
                date = date.replace(tzinfo=timezone.utc)
            except:
                pass

            tokens.append({
                "name": name,
                "symbol": symbol,
                "date": date,
                "url": CMC_UPCOMING_URL,
                "source": "CMC Upcoming",
            })
        return tokens
    except Exception as e:
        logging.error(f"CMC /upcoming scrape error: {e}")
        return []

def fetch_binance_alpha(limit=30):
    try:
        r = requests.get(BINANCE_ALPHA_URL, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        tokens = []
        cards = soup.select("a[href*='/en/trade/']")
        for card in cards[:limit]:
            name = card.get("title", "Unknown")
            symbol = name.split(" ")[0]
            url = "https://www.binance.com" + card["href"]

            date = None
            date_elem = card.find("div", string=lambda t: t and any(y in t for y in ["2025", "2026"]))
            if date_elem:
                try:
                    date = datetime.strptime(date_elem.get_text(strip=True), "%Y-%m-%d")
                    date = date.replace(tzinfo=timezone.utc)
                except:
                    pass

            tokens.append({
                "name": name,
                "symbol": symbol,
                "date": date,
                "url": url,
                "source": "Binance Alpha",
            })
        return tokens
    except Exception as e:
        logging.error(f"Binance Alpha scrape error: {e}")
        return []

# ---------------- FILTERS ----------------
def token_filter(t):
    price = t["price"]
    supply = t["supply"]
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
    if token["date"]:
        now = datetime.now(pytz.utc)
        delta = (token["date"] - now).days
        if delta < 0 or delta > 30:
            return False
    return True

# ---------------- LINKS ----------------
def cmc_link(symbol):
    return f"https://coinmarketcap.com/currencies/{symbol.lower()}/"

def dexscreener_link(symbol):
    return f"https://dexscreener.com/search?q={symbol}"

# ---------------- ALERTS ----------------
def new_crypto_alert():
    fresh = [t for t in fetch_cmc_new() if token_filter(t)]
    if not fresh:
        return "✅ No new cryptos match your filters."

    msg = f"🆕 New Crypto Alerts\n{datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
    for i, t in enumerate(fresh, 1):
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
    cmc = [t for t in fetch_cmc_upcoming() if alpha_filter(t)]
    binance = [t for t in fetch_binance_alpha() if alpha_filter(t)]
    alphas = cmc + binance
    if not alphas:
        return "🚀 No valid upcoming listings right now."

    msg = f"🚀 New Alpha Alerts\n{datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
    for i, t in enumerate(alphas, 1):
        date_str = t["date"].strftime("%Y-%m-%d") if t["date"] else "Unknown"
        msg += (
            f"{i}. 💎 {t['name']} ({t['symbol']})\n"
            f"📅 First Listing: {date_str}\n"
            f"📌 Source: {t['source']}\n"
            f"🔗 [More Info]({t['url']})\n\n"
        )
    return msg

def send_alerts():
    try:
        bot.send_message(chat_id=CHAT_ID, text=new_crypto_alert(), parse_mode="Markdown", disable_web_page_preview=True)
        bot.send_message(chat_id=CHAT_ID, text=alpha_alert(), parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        logging.error(f"Telegram alert failed: {e}")

# ---------------- BUTTONS + HANDLERS ----------------
def start(update: Update, context: CallbackContext):
    keyboard = [
        [
            InlineKeyboardButton("🆕 New Crypto Alerts", callback_data="new"),
            InlineKeyboardButton("🚀 Alpha Alerts", callback_data="alpha"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        "👋 Welcome! This bot sends crypto alerts hourly.\n\nChoose an action:",
        reply_markup=reply_markup
    )

def button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    if query.data == "new":
        query.edit_message_text(new_crypto_alert(), parse_mode="Markdown", disable_web_page_preview=True)
    elif query.data == "alpha":
        query.edit_message_text(alpha_alert(), parse_mode="Markdown", disable_web_page_preview=True)

dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CallbackQueryHandler(button_callback))

# ---------------- RUN ----------------
if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(send_alerts, "interval", minutes=60, timezone=pytz.utc)
    scheduler.start()

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
