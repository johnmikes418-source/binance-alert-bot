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
BINANCE_ALPHA_URL = "https://www.binance.com/en/support/announcement/list/48"

app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)

logging.basicConfig(level=logging.INFO)

# ---------------- FETCHERS ----------------
def fetch_cmc_from_web(limit=20):
    """Scrape new tokens directly from CoinMarketCap /new/ page"""
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
            listed_elem = cols[5].find("span") if len(cols) > 5 else None

            name = name_elem.get_text(strip=True) if name_elem else "Unknown"
            symbol = symbol_elem.get_text(strip=True) if symbol_elem else "UNK"

            price_text = cols[3].get_text(strip=True).replace("$", "").replace(",", "")
            price = float(price_text) if price_text else 0

            change_text = cols[4].get_text(strip=True).replace("%", "")
            try:
                change = float(change_text)
            except:
                change = 0

            listed = None
            if listed_elem:
                try:
                    listed = datetime.strptime(listed_elem.get_text(strip=True), "%b %d, %Y")
                except Exception:
                    listed = datetime.now(UTC)

            tokens.append(
                {
                    "id": None,
                    "name": name,
                    "symbol": symbol,
                    "price": price,
                    "change": change,
                    "supply": None,
                    "listed": listed,
                }
            )
        return tokens
    except Exception as e:
        logging.error(f"CMC web scrape error: {e}")
        return []


def fetch_binance_alpha():
    """Scrape Binance announcement page for upcoming token listings"""
    try:
        r = requests.get(BINANCE_ALPHA_URL, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        results = []
        for link in soup.select("a.css-1ej4hfo"):
            title = link.get_text(strip=True)
            href = "https://www.binance.com" + link.get("href")
            if "Will List" in title:
                results.append({"title": title, "url": href})
        return results
    except Exception as e:
        logging.error(f"Binance Alpha scrape error: {e}")
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

def is_new_crypto(token):
    listed = token.get("listed")
    if not listed:
        return False
    age = datetime.now(UTC) - listed
    return age <= timedelta(days=60) and token_filter(token)

# ---------------- ALERTS ----------------
def cmc_link(token):
    return "https://coinmarketcap.com/new/"

def dexscreener_link(symbol):
    return f"https://dexscreener.com/search?q={symbol}"

def new_crypto_alert():
    fresh = [t for t in fetch_cmc_from_web(30) if is_new_crypto(t)]
    if not fresh:
        return "✅ No new cryptos (≤60 days) match your filters."

    msg = f"🆕 New Crypto Alerts (≤60 days)\n{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
    for i, t in enumerate(fresh, start=1):
        msg += (
            f"{i}. 💎 {t['name']} ({t['symbol']}/USDT)\n"
            f"💰 Price: ${t['price']:.6f}\n"
            f"📈 24h Change: {t['change']:+.2f}%\n"
            f"🔗 [CMC]({cmc_link(t)}) | [DexScreener]({dexscreener_link(t['symbol'])})\n\n"
        )
    return msg

def alpha_alert():
    alphas = fetch_binance_alpha()
    if not alphas:
        return "🚀 No upcoming Binance Alpha listings right now."

    msg = f"🚀 New Alpha Alerts (Upcoming Binance Listings)\n{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
    for i, t in enumerate(alphas, start=1):
        msg += (
            f"{i}. 💎 {t['title']}\n"
            f"🔗 [More Info]({t['url']})\n\n"
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
