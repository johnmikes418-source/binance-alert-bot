import os
import requests
import logging
import threading
from flask import Flask
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
                "supply": None
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
                data.append({
                    "symbol": p.get("baseToken", {}).get("symbol", "UNK"),
                    "price": float(p.get("priceUsd", 0)),
                    "change": float(p.get("priceChange", {}).get("h24", 0)),
                    "supply": None
                })
        return data
    except Exception as e:
        logging.error(f"Dexscreener error: {e}")
        return []

# ---------------- FILTER ----------------
def token_filter(token):
    price = token["price"]
    supply = token["supply"]

    if supply:
        if supply <= 1_000_000_000 and 0.02 <= price <= 0.05:
            return True
        if supply <= 10_000_000_000 and 0.002 <= price <= 0.005:
            return True
        return False
    return True

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

# ---------------- TELEGRAM ----------------
def start_command(update: Update, context: CallbackContext):
    keyboard = [[InlineKeyboardButton("🔍 Check Tokens", callback_data="check_tokens")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Welcome! Tap below to check tokens:", reply_markup=reply_markup)

def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    if query.data == "check_tokens":
        msg = check_tokens()
        query.edit_message_text(text=msg)

def run_telegram_bot():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start_command))
    dp.add_handler(CallbackQueryHandler(button_handler))
    updater.start_polling()

# ---------------- FLASK ----------------
@app.route("/")
def home():
    return "Bot is running!"

@app.route("/ping")
def ping():
    return "✅ Bot is alive."

@app.route("/start")
def trigger_start():
    msg = check_tokens()
    return msg

# ---------------- MAIN ----------------
if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_telegram_bot)
    bot_thread.daemon = True
    bot_thread.start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))