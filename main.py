import os
import sys
import requests
import logging
import threading
from datetime import datetime, timedelta
from flask import Flask, request
import imghdr2 as imghdr
from bs4 import BeautifulSoup  # for fallback scraping

# Fix PIL/telegram bug
sys.modules["imghdr"] = imghdr

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
CMC_KEY = os.getenv("COINMARKETCAP_KEY")

CMC_API = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
CMC_NEW_URL = "https://coinmarketcap.com/new/"  # fallback scrape

app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)

logging.basicConfig(level=logging.INFO)


# ---------------- FETCHERS ----------------
def fetch_cmc(limit=50):
    """Fetch tokens from CMC API"""
    headers = {"X-CMC_PRO_API_KEY": CMC_KEY}
    try:
        r = requests.get(
            CMC_API,
            params={"start": 1, "limit": limit, "convert": "USD"},
            headers=headers,
            timeout=10,
        )
        r.raise_for_status()
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
                    listed = datetime.fromisoformat(listed_str.replace("Z", ""))
                except Exception:
                    listed = None

            tokens.append(
                {
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
    """Scrape CoinMarketCap new listings as fallback"""
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
                    "symbol": symbol,
                    "price": price,
                    "change": change,
                    "supply": None,
                    "listed": datetime.utcnow(),  # fallback has no date
                }
            )
        return tokens
    except Exception as e:
        logging.error(f"CMC fallback error: {e}")
        return []


# ---------------- FILTERS ----------------
def token_filter(token):
    """Supply–price filter"""
    price = token["price"]
    supply = token["supply"]

    if supply:
        if supply <= 1_000_000_000 and 0.02 <= price <= 0.05:
            return True
        if supply <= 10_000_000_000 and 0.002 <= price <= 0.005:
            return True
    return False


def is_new_crypto(token):
    """≤ 60 days old + passes filter"""
    listed = token.get("listed")
    if not listed:
        return False
    age = datetime.utcnow() - listed
    return age <= timedelta(days=60) and token_filter(token)


def is_alpha(token):
    """≤ 7 days old (ignore filter)"""
    listed = token.get("listed")
    if not listed:
        return False
    age = datetime.utcnow() - listed
    return age <= timedelta(days=7)


# ---------------- ALERTS ----------------
def check_tokens():
    results = []
    for token in fetch_cmc(100):
        if abs(token["change"]) >= 5 and token_filter(token):
            results.append(token)

    if not results:
        return "✅ No tokens match criteria right now."

    msg = f"📊 Token Alerts ({datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}):\n\n"
    for t in results:
        msg += f"🔹 {t['symbol']} | 💵 ${t['price']:.6f} | 📈 {t['change']}%\n"
    return msg


def new_crypto_alert():
    fresh = [t for t in fetch_cmc(100) if is_new_crypto(t)]
    if not fresh:
        return "✅ No new cryptos in last 60 days match your filters."
    msg = f"🆕 New Crypto (≤60 days) ({datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}):\n\n"
    for t in fresh:
        msg += f"🔹 {t['symbol']} | 💵 ${t['price']:.6f}\n"
    return msg


def alpha_alert():
    alphas = [t for t in fetch_cmc(100) if is_alpha(t)]
    if not alphas:
        return "🚀 No new alpha listings yet."
    msg = f"🚀 New Alpha Alerts ({datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}):\n\n"
    for t in alphas:
        msg += f"🔹 {t['symbol']} | Listed recently!\n"
    return msg


# ---------------- TELEGRAM ----------------
def main_menu():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔍 Check Tokens", callback_data="check_tokens")],
            [InlineKeyboardButton("💰 CMC Top", callback_data="cmc")],
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
        "👋 Welcome! Choose an option:", reply_markup=main_menu()
    )


def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    if query.data == "menu":
        query.edit_message_text("👋 Back to menu:", reply_markup=main_menu())

    elif query.data == "check_tokens":
        query.edit_message_text(check_tokens(), reply_markup=back_button())

    elif query.data == "cmc":
        data = fetch_cmc(10)
        msg = "💰 CMC Top Tokens:\n\n" + "\n".join(
            [f"🔹 {t['symbol']} | ${t['price']:.6f} | {t['change']}%" for t in data]
        )
        query.edit_message_text(msg or "No data.", reply_markup=back_button())

    elif query.data == "new_crypto":
        query.edit_message_text(new_crypto_alert(), reply_markup=back_button())

    elif query.data == "alpha":
        query.edit_message_text(alpha_alert(), reply_markup=back_button())


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

    if HOSTNAME:  # Running on Render
        WEBHOOK_URL = f"https://{HOSTNAME}/webhook"
        logging.info(f"Setting webhook to {WEBHOOK_URL}")
        updater.bot.set_webhook(WEBHOOK_URL)
        app.run(host="0.0.0.0", port=PORT)
    else:  # Local dev
        bot_thread = threading.Thread(target=updater.start_polling)
        bot_thread.daemon = True
        bot_thread.start()
        app.run(host="0.0.0.0", port=PORT)
