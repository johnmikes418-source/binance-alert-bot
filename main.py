import os
import sys
import requests
import logging
import threading
from datetime import datetime, timedelta
from flask import Flask, request
import imghdr2 as imghdr

# Fix PIL/telegram bug
sys.modules["imghdr"] = imghdr

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BINANCE_API = "https://api.binance.com/api/v3/ticker/24hr"
COINGECKO_API = "https://api.coingecko.com/api/v3/coins/markets"
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/search"

app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)

logging.basicConfig(level=logging.INFO)


# ---------------- SAFE FETCHERS ----------------
def fetch_binance():
    try:
        r = requests.get(BINANCE_API, timeout=10)
        try:
            data = r.json()
        except Exception:
            logging.error(f"Binance not JSON: {r.text[:200]}")
            return []

        if not isinstance(data, list):
            logging.error(f"Binance API error: {data}")
            return []

        return [
            {
                "symbol": x.get("symbol", "UNK"),
                "price": float(x.get("lastPrice") or 0),
                "change": float(x.get("priceChangePercent") or 0),
                "supply": None,
                "listed": None,
            }
            for x in data
            if isinstance(x, dict)
        ]
    except Exception as e:
        logging.error(f"Binance error: {e}")
        return []


def fetch_coingecko():
    try:
        r = requests.get(
            COINGECKO_API,
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 50,
                "page": 1,
            },
            timeout=10,
        )
        try:
            data = r.json()
        except Exception:
            logging.error(f"Coingecko not JSON: {r.text[:200]}")
            return []

        if not isinstance(data, list):
            logging.error(f"Coingecko API error: {data}")
            return []

        result = []
        for x in data:
            if not isinstance(x, dict):
                continue

            listed_str = (
                x.get("atl_date")
                or x.get("ath_date")
                or x.get("last_updated")
            )
            listed = None
            try:
                if listed_str:
                    listed = datetime.fromisoformat(listed_str.replace("Z", ""))
            except Exception:
                listed = None

            result.append(
                {
                    "symbol": x.get("symbol", "UNK").upper(),
                    "price": float(x.get("current_price") or 0),
                    "change": float(x.get("price_change_percentage_24h") or 0),
                    "supply": x.get("max_supply") or 0,
                    "listed": listed,
                }
            )
        return result
    except Exception as e:
        logging.error(f"Coingecko error: {e}")
        return []


def fetch_dexscreener():
    try:
        tokens = ["0x0d4890ecEc59cd55D640d36f7acc6F7F512Fdb6e"]  # sample
        result = []
        for t in tokens:
            r = requests.get(f"{DEXSCREENER_API}?q={t}", timeout=10)
            try:
                resp = r.json()
            except Exception:
                logging.error(f"Dexscreener not JSON: {r.text[:200]}")
                continue

            if not isinstance(resp, dict):
                logging.error(f"Dexscreener API error for {t}: {resp}")
                continue

            pairs = resp.get("pairs", [])
            if not isinstance(pairs, list):
                continue

            for p in pairs:
                if not isinstance(p, dict):
                    continue

                listed = p.get("pairCreatedAt")
                listed_dt = (
                    datetime.utcfromtimestamp(listed // 1000)
                    if listed
                    else None
                )
                result.append(
                    {
                        "symbol": p.get("baseToken", {}).get("symbol", "UNK"),
                        "price": float(p.get("priceUsd") or 0),
                        "change": float(p.get("priceChange", {}).get("h24") or 0),
                        "supply": None,
                        "listed": listed_dt,
                    }
                )
        return result
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


def is_new_crypto(token):
    """≤ 60 days old + passes filter"""
    listed = token.get("listed")
    if not listed:
        return False
    age = datetime.utcnow() - listed
    return age <= timedelta(days=60) and token_filter(token)


def is_alpha(token):
    """≤ 7 days old (ignore price filter)"""
    listed = token.get("listed")
    if not listed:
        return False
    age = datetime.utcnow() - listed
    return age <= timedelta(days=7)


# ---------------- ALERTS ----------------
def check_tokens():
    results = []
    for token in fetch_binance() + fetch_coingecko() + fetch_dexscreener():
        if abs(token["change"]) >= 5 and token_filter(token):
            results.append(token)

    if not results:
        return "✅ No tokens match criteria right now."

    msg = f"📊 Token Alerts ({datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}):\n\n"
    for t in results:
        msg += (
            f"🔹 {t['symbol']} | 💵 ${t['price']:.6f} | 📈 {t['change']}%\n"
        )
    return msg


def new_crypto_alert():
    data = fetch_coingecko() + fetch_dexscreener()
    fresh = [t for t in data if is_new_crypto(t)]
    if not fresh:
        return "✅ No new cryptos in last 60 days match your filters."
    msg = f"🆕 New Crypto (≤60 days) ({datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}):\n\n"
    for t in fresh:
        msg += f"🔹 {t['symbol']} | 💵 ${t['price']:.6f}\n"
    return msg


def alpha_alert():
    data = fetch_coingecko() + fetch_dexscreener()
    alphas = [t for t in data if is_alpha(t)]
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
            [InlineKeyboardButton("💰 Binance Top", callback_data="binance")],
            [InlineKeyboardButton("🌐 CoinGecko Top", callback_data="coingecko")],
            [InlineKeyboardButton("🦄 Dexscreener Token", callback_data="dexscreener")],
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

    elif query.data == "binance":
        data = fetch_binance()[:5]
        msg = "📊 Binance Top Tokens:\n\n" + "\n".join(
            [
                f"🔹 {t['symbol']} | ${t['price']:.6f} | {t['change']}%"
                for t in data
            ]
        )
        query.edit_message_text(msg or "No data.", reply_markup=back_button())

    elif query.data == "coingecko":
        data = fetch_coingecko()[:5]
        msg = "🌐 CoinGecko Top:\n\n" + "\n".join(
            [
                f"🔹 {t['symbol']} | ${t['price']:.6f} | {t['change']}%"
                for t in data
            ]
        )
        query.edit_message_text(msg or "No data.", reply_markup=back_button())

    elif query.data == "dexscreener":
        data = fetch_dexscreener()
        msg = "🦄 Dexscreener:\n\n" + "\n".join(
            [
                f"🔹 {t['symbol']} | ${t['price']:.6f} | {t['change']}%"
                for t in data
            ]
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
