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
CMC_KEY = os.getenv("COINMARKETCAP_KEY")

CMC_API = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
CMC_NEW_URL = "https://coinmarketcap.com/new/"
BINANCE_ALPHA_URL = "https://www.binance.com/en/support/announcement/list/48"

app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)

logging.basicConfig(level=logging.INFO)

# Debug: check env vars
logging.info(f"BOT_TOKEN loaded? {'Yes' if BOT_TOKEN else 'No'}")
logging.info(f"CHAT_ID loaded? {'Yes' if CHAT_ID else 'No'}")
logging.info(f"CMC_KEY loaded? {'Yes' if CMC_KEY else 'No'}")


# ---------------- FETCHERS ----------------
def fetch_cmc(limit=50):
    if not CMC_KEY:
        logging.error("❌ No CMC_KEY found in environment variables!")
        return fetch_cmc_fallback()

    headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
    try:
        r = requests.get(
            CMC_API,
            params={"start": 1, "limit": limit, "convert": "USD"},
            headers=headers,
            timeout=10,
        )

        if r.status_code != 200:
            logging.error(f"❌ CMC API failed: {r.status_code} {r.text}")
            return fetch_cmc_fallback()

        data = r.json().get("data", [])
    except Exception as e:
        logging.error(f"CMC API error: {e}")
        return fetch_cmc_fallback()

    tokens = []
    for x in data:
        try:
            listed_str = x.get("date_added")
            listed = None
            if listed_str:
                try:
                    listed = datetime.fromisoformat(listed_str.replace("Z", "+00:00"))
                except Exception:
                    listed = None

            tokens.append(
                {
                    "id": x.get("id"),
                    "name": x.get("name"),
                    "symbol": x.get("symbol", "UNK"),
                    "price": float(x["quote"]["USD"]["price"]),
                    "change": float(x["quote"]["USD"]["percent_change_24h"]),
                    "supply": x.get("max_supply") or 0,
                    "listed": listed,
                }
            )
        except Exception as e:
            logging.error(f"Parse error: {e}")
    return tokens


def fetch_cmc_fallback():
    logging.warning("⚠️ Using fallback: scraping CMC /new/")
    try:
        r = requests.get(CMC_NEW_URL, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        tokens = []
        rows = soup.select("table tbody tr")
        for row in rows[:20]:
            cols = row.find_all("td")
            if len(cols) < 5:
                continue
            symbol = cols[2].get_text(strip=True)
            price_text = cols[3].get_text(strip=True).replace("$", "").replace(",", "")
            price = float(price_text) if price_text else 0
            change_text = cols[4].get_text(strip=True).replace("%", "")
            change = float(change_text) if change_text else 0

            tokens.append(
                {
                    "id": None,
                    "name": symbol,
                    "symbol": symbol,
                    "price": price,
                    "change": change,
                    "supply": None,
                    "listed": datetime.now(UTC),
                }
            )
        return tokens
    except Exception as e:
        logging.error(f"CMC fallback error: {e}")
        return []


def fetch_binance_alpha():
    """Scrape Binance announcement page for upcoming token listings"""
    try:
        r = requests.get(BINANCE_ALPHA_URL, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        results = []
        for link in soup.select("a.css-1ej4hfo"):  # Binance uses dynamic CSS classes
            title = link.get_text(strip=True)
            href = "https://www.binance.com" + link.get("href")
            if "Will List" in title:  # filter only token listing announcements
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
    return f"https://coinmarketcap.com/currencies/{token['id']}/" if token["id"] else "https://coinmarketcap.com/new/"


def dexscreener_link(symbol):
    return f"https://dexscreener.com/search?q={symbol}"


def new_crypto_alert():
    fresh = [t for t in fetch_cmc(100) if is_new_crypto(t)]
    if not fresh:
        return "✅ No new cryptos (≤60 days) match your filters."

    msg = f"🆕 New Crypto Alerts (≤60 days)\n{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
    for i, t in enumerate(fresh, start=1):
        msg += (
            f"{i}. 💎 {t['name']} ({t['symbol']}/USDT)\n"
            f"💰 Price: ${t['price']:.6f}\n"
            f"📈 24h Change: {t['change']:+.2f}%\n"
            f"🔢 Supply: {t['supply']:,}\n"
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
