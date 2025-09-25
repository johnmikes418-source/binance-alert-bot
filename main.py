import os
import requests
import logging
import threading
from datetime import datetime, timedelta
from flask import Flask, request
import imghdr2 as imghdr
import sys
sys.modules["imghdr"] = imghdr

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

binance_api = "https://api.binance.com/api/v3/ticker/24hr"
coingecko_api = "https://api.coingecko.com/api/v3/coins/markets"
dexscreener_api = "https://api.dexscreener.com/latest/dex/tokens"

app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)

logging.basicConfig(level=logging.INFO)

# ---------------- FETCHERS ----------------
def fetch_binance():
    try:
        r = requests.get(binance_api, timeout=10).json()
        return [
            {
                "symbol": x["symbol"],
                "price": float(x["lastPrice"]),
                "change": float(x["priceChangePercent"]),
                "supply": None,
                "listed": None,  # Binance doesn’t give listing date
            }
            for x in r
        ]
    except Exception as e:
        logging.error(f"Binance error: {e}")
        return []

def fetch_coingecko():
    try:
        r = requests.get(
            coingecko_api,
            params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": 50, "page": 1},
            timeout=10,
        ).json()
        return [
            {
                "symbol": x["symbol"].upper(),
                "price": float(x["current_price"]),
                "change": float(x["price_change_percentage_24h"] or 0),
                "supply": x.get("max_supply") or 0,
                "listed": x.get("ath_date") or x.get("last_updated"),  # fallback
            }
            for x in r
        ]
    except Exception as e:
        logging.error(f"Coingecko error: {e}")
        return []

def fetch_dexscreener():
    try:
        tokens = ["0x0d4890ecEc59cd55D640d36f7acc6F7F512Fdb6e"]
        data = []
        for t in tokens:
            r = requests.get(f"{dexscreener_api}/{t}", timeout=10).json()
            pairs = r.get("pairs", [])
            for p in pairs:
                listed = p.get("pairCreatedAt")
                listed_dt = datetime.utcfromtimestamp(listed // 1000) if listed else None
                data.append({
                    "symbol": p.get("baseToken", {}).get("symbol", "UNK"),
                    "price": float(p.get("priceUsd", 0)),
                    "change": float(p.get("priceChange", {}).get("h24", 0)),
                    "supply": None,
                    "listed": listed_dt,
                })
        return data
    except Exception as e:
        logging.error(f"Dexscreener error: {e}")
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
    return False  # skip if no supply

def is_new_crypto(token):
    """≤ 60 days old and matches supply–price filter"""
    listed = token.get("listed")
    if not listed:
        return False
    if isinstance(listed, str):
        try:
            listed = datetime.fromisoformat(listed.replace("Z", ""))
        except:
            return False
    age = datetime.utcnow() - listed
    return age <= timedelta(days=60) and token_filter(token)

def is_alpha(token):
    """New/prelaunch tokens (just use 'listed' ≤ 7 days, no price filter)"""
    listed = token.get("listed")
    if not listed:
        return False
    if isinstance(listed, str):
        try:
            listed = datetime.fromisoformat(listed.replace("Z", ""))
        except:
            return False
    age = datetime.utcnow() - listed
    return age <= timedelta(days=7)

# ---------------- ALERT ----------------
def check_tokens():
    results = []
    for token in fetch_binance() + fetch_coingecko() + fetch_dexscreener():
        if abs(token["change"]) >= 5 and token_filter(token):
            results.append(token)

    if not results:
        return "✅ No tokens match criteria right now."

    msg = "📊 Token Alerts:\n\n"
    for t in results:
        msg += f"🔹 {t['symbol']} | 💵 ${t['price']:.6f} | 📈 {t['change']}%\n"
    return msg

def new_crypto_alert():
    data = fetch_coingecko() + fetch_dexscreener()
    fresh = [t for t in data if is_new_crypto(t)]
    if not fresh:
        return "✅ No new cryptos in last 60 days match your filters."
    msg = "🆕 New Crypto (≤60 days):\n\n"
    for t in fresh:
        msg += f"🔹 {t['symbol']} | 💵 ${t['price']:.6f}\n"
    return msg

def alpha_alert():
    data = fetch_coingecko() + fetch_dexscreener()
    alphas = [t for t in data if is_alpha(t)]
    if not alphas:
        return "🚀 No new alpha listings yet."
    msg = "🚀 New Alpha Alerts:\n\n"
    for t in alphas:
        msg += f"🔹 {t['symbol']} | Listed recently!\n"
    return msg

# ---------------- TELEGRAM ----------------
def start_command(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("🔍 Check Tokens", callback_data="check_tokens")],
        [InlineKeyboardButton("💰 Binance Top", callback_data="binance")],
        [InlineKeyboardButton("🌐 CoinGecko Top", callback_data="coingecko")],
        [InlineKeyboardButton("🦄 Dexscreener Token", callback_data="dexscreener")],
        [InlineKeyboardButton("🆕 New Crypto", callback_data="new_crypto")],
        [InlineKeyboardButton("🚀 New Alpha Alert", callback_data="alpha")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Welcome! Choose an option:", reply_markup=reply_markup)

def main_menu_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Back to Menu", callback_data="menu")]]
    )

def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    if query.data == "menu":
        start_command(query, context)

    elif query.data == "check_tokens":
        msg = check_tokens()
        query.edit_message_text(text=msg, reply_markup=main_menu_keyboard())

    elif query.data == "binance":
        data = fetch_binance()[:5]
        msg = "📊 Binance Top Tokens:\n\n" + "\n".join(
            [f"🔹 {t['symbol']} | 💵 ${t['price']:.6f} | 📈 {t['change']}%" for t in data]
        )
        query.edit_message_text(text=msg or "No data available.", reply_markup=main_menu_keyboard())

    elif query.data == "coingecko":
        data = fetch_coingecko()[:5]
        msg = "🌐 CoinGecko Top Tokens:\n\n" + "\n".join(
            [f"🔹 {t['symbol']} | 💵 ${t['price']:.6f} | 📈 {t['change']}%" for t in data]
        )
        query.edit_message_text(text=msg or "No data available.", reply_markup=main_menu_keyboard())

    elif query.data == "dexscreener":
        data = fetch_dexscreener()
        msg = "🦄 Dexscreener Tokens:\n\n" + "\n".join(
            [f"🔹 {t['symbol']} | 💵 ${t['price']:.6f} | 📈 {t['change']}%" for t in data]
        )
        query.edit_message_text(text=msg or "No data available.", reply_markup=main_menu_keyboard())

    elif query.data == "new_crypto":
        msg = new_crypto_alert()
        query.edit_message_text(text=msg, reply_markup=main_menu_keyboard())

    elif query.data == "alpha":
        msg = alpha_alert()
        query.edit_message_text(text=msg, reply_markup=main_menu_keyboard())

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

    if HOSTNAME:  # Running on Render
        WEBHOOK_URL = f"https://{HOSTNAME}/webhook"
        logging.info(f"Setting webhook to {WEBHOOK_URL}")

        updater = Updater(BOT_TOKEN, use_context=True)
        dp = updater.dispatcher
        dp.add_handler(CommandHandler("start", start_command))
        dp.add_handler(CallbackQueryHandler(button_handler))

        updater.bot.set_webhook(WEBHOOK_URL)

        app.run(host="0.0.0.0", port=PORT)

    else:  # Local development
        updater = Updater(BOT_TOKEN, use_context=True)
        dp = updater.dispatcher
        dp.add_handler(CommandHandler("start", start_command))
        dp.add_handler(CallbackQueryHandler(button_handler))

        bot_thread = threading.Thread(target=updater.start_polling)
        bot_thread.daemon = True
        bot_thread.start()
        app.run(host="0.0.0.0", port=PORT)
