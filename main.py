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
BINANCE_ALPHA_URL = "https://www.binance.com/en/markets/alpha-BSC"

app = Flask(__name__)
bot = Bot(token=BOT_TOKEN)

logging.basicConfig(level=logging.INFO)

# ---------------- HELPERS ----------------
def fetch_supply(coin_url):
    """Scrape max supply from an individual CMC coin page"""
    try:
        r = requests.get(coin_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        supply_elem = soup.find("div", string=lambda t: t and "Max Supply" in t)
        if not supply_elem:
            return None

        sibling = supply_elem.find_next("div")
        if not sibling:
            return None

        raw = sibling.get_text(strip=True).replace(",", "").replace("∞", "")
        try:
            return float(raw.split(" ")[0])
        except:
            return None
    except Exception as e:
        logging.warning(f"Supply scrape error for {coin_url}: {e}")
        return None

# ---------------- FETCHERS ----------------
def fetch_cmc_new(limit=20):
    """Scrape new tokens from CoinMarketCap /new/ page (with supply details)"""
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

            link_elem = cols[1].find("a", href=True)
            coin_url = f"https://coinmarketcap.com{link_elem['href']}" if link_elem else None

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

            supply = fetch_supply(coin_url) if coin_url else None

            tokens.append(
                {
                    "name": name,
                    "symbol": symbol,
                    "price": price,
                    "change": change,
                    "supply": supply,
                    "url": coin_url,
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

            listing_date = None
            try:
                listing_date = datetime.strptime(date_text, "%b %d, %Y")
            except:
                pass

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
        cards = soup.select("a.css-1ej4hfo, a.css-17wnpgm")  # Binance Alpha token cards
        for card in cards[:limit]:
            name = card.get("title", "Unknown") or card.get_text(strip=True)
            symbol = name.split(" ")[0]
            url = "https://www.binance.com" + card["href"]

            results.append(
                {
                    "name": name,
                    "symbol": symbol,
                    "date": None,
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

    if not supply:
        return False

    # Case 1: Supply ≤ 1B
    if supply <= 1_000_000_000 and 0.005 <= price <= 0.05:
        return True
    # Case 2: Supply ≤ 10B
    if supply <= 10_000_000_000 and 0.0005 <= price <= 0.005:
        return True
    return False

def alpha_filter(token):
    """Skip past or >30d future listings"""
    if token["date"]:
        now = datetime.now()
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

    msg = f"🆕 New Crypto Alerts\n{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
    for i, t in enumerate(fresh, start=1):
        msg += (
            f"{i}. 💎 {t['name']} ({t['symbol']}/USDT)\n"
            f"💰 Price: ${t['price']:.6f}\n"
            f"📈 24h Change: {t['change']:+.2f}%\n"
            f"🔄 Supply: {t['supply']:,} \n"
            f"🔗 [CMC]({t['url']}) | [DexScreener]({dexscreener_link(t['symbol'])})\n\n"
        )
    return msg

def alpha_alert():
    cmc = [t for t in fetch_cmc_upcoming(30) if alpha_filter(t)]
    binance = fetch_binance_alpha(20)

    alphas = cmc + binance
    if not alphas:
        return "🚀 No valid upcoming listings right now."

    msg = f"🚀 New Alpha Alerts (Upcoming Listings)\n{datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
    for i, t in enumerate(alphas, start=1):
        date_str = (
            t["date"].strftime("%Y-%m-%d") if t["date"] else "Recently Added"
        )
        msg += (
            f"{i}. 💎 {t['name']} ({t['symbol']})\n"
            f"📅 First Listing: {date_str}\n"
            f"📌 Source: {t['source']}\n"
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
