import os
import sys
import requests
import logging
import threading
from datetime import datetime, timedelta, UTC
from flask import Flask, request
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

app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)

logging.basicConfig(level=logging.INFO)

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

            tokens.append(
                {
                    "name": name,
                    "symbol": symbol,
                    "price": price,
                    "change": change,
                    "supply": None,
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
            date_elem = cols[2]

            name = name_elem.get_text(strip=True) if name_elem else "Unknown"
            symbol = name.split(" ")[-1].replace("(", "").replace(")", "")
            date_text = date_elem.get_text(strip=True)

            results.append(
                {
                    "name": name,
                    "symbol": symbol,
                    "date": date_text,
                    "url": CMC_UPCOMING_URL,
                }
            )
        return results
    except Exception as e:
        logging.error(f"CMC /upcoming scrape error: {e}")
        return []

# ---------------- FILTERS ----------------
def token_filter(token):
    price = token["price"]
    supply = token["supply"]

    if supply:
        if supply <= 1_000_000_000 and 0.02 <= price <= 0.05:
            return True
        if supply <= 10_000_000_000 and 0.002 <= price <= 0.005:
            return True
    return False

# ---------------- ALERTS ----------------
def cmc_link(symbol):
    return f"https://coinmarketcap.com/currencies/{symbol.lower()}/"

def dexscreener_link(symbol):
    return f"https://dexscreener.com/search?q={symbol}"

def new_crypto_alert():
    fresh = [t for t in fetch_cmc_new(30) if token_filter(t)]
    if not fresh:
        return "✅ No new cryptos match your filters."

    msg = f"🆕 New Crypto Alerts\n{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
    for i, t in enumerate(fresh, start=1):
        msg += (
            f"{i}. 💎 {t['name']} ({t['symbol']}/USDT)\n"
            f"💰 Price: ${t['price']:.6f}\n"
            f"📈 24h Change: {t['change']:+.2f}%\n"
            f"🔗 [CMC]({cmc_link(t['symbol'])}) | [DexScreener]({dexscreener_link(t['symbol'])})\n\n"
        )
    return msg

def alpha_alert():
    alphas = fetch_cmc_upcoming(30)
    if not alphas:
        return "🚀 No upcoming CMC listings right now."

    msg = f"🚀 New Alpha Alerts (Upcoming Listings)\n{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
    for i, t in enumerate(alphas, start=1):
        msg += (
            f"{i}. 💎 {t['name']} ({t['symbol']})\n"
            f"📅 First Listing: {t['date']}\n"
            f"📌 More Info: [CMC Upcoming]({t['url']})\n\n"
        )
    return msg

# ---------------- TELEGRAM ----------------
def main_menu():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🆕 New Crypto", callback_data="new_crypto")],
            [InlineKeyboardButton("🚀 New Alpha Alert", callback_data="alpha")],
        ]
    )

def back_button():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu")]]
    )

def start_command(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Welcome! Choose an option:", reply_markup=main_menu(), parse_mode="Markdown"
    )

def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    if query.data == "menu":
        query.edit_message_text(
            "👋 Back to menu:", reply_markup=main_menu(), parse_mode="Markdown"
        )

    elif query.data == "new_crypto":
        query.edit_message_text(
            new_crypto_alert(), reply_markup=back_button(), parse_mode="Markdown"
        )

    elif query.data == "alpha":
        query.edit_message_text(
            alpha_alert(), reply_markup=back_button(), parse_mode="Markdown"
        )

# ---------------- FLASK ----------------
@app.route("/")
def home():
    return "Bot is running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher = updater.dispatcher
    dispatcher.process_update(update)
    return "ok"

# ---------------- MAIN ----------------
if __name__ == "__main__":
    PORT = int(os.getenv("PORT", 5000))
    HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")

    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start_command))
    dp.add_handler(CallbackQueryHandler(button_handler))

    if HOSTNAME:
        WEBHOOK_URL = f"https://{HOSTNAME}/webhook"
        logging.info(f"Setting webhook to {WEBHOOK_URL}")
        updater.bot.set_webhook(WEBHOOK_URL)
        app.run(host="0.0.0.0", port=PORT)
    else:
        bot_thread = threading.Thread(target=updater.start_polling)
        bot_thread.daemon = True
        bot_thread.start()
        app.run(host="0.0.0.0", port=PORT)
