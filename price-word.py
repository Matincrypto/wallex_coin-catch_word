import requests
import json
import time
import logging
import asyncio
from telegram import Bot
from telegram.constants import ParseMode
from datetime import datetime
import pytz

# --- Logging Configuration ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Helper Functions ---
def load_config():
    """Loads the configuration from config.json file."""
    try:
        with open('config.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.critical("FATAL: config.json not found. Please create it.")
        exit()

# --- API Fetching Functions ---
def get_wallex_usdt_markets(config):
    """Fetches all USDT markets from Wallex to know which symbols to check."""
    try:
        url = config['price_sources']['wallex']['base_url'] + config['price_sources']['wallex']['markets_endpoint']
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json().get("result", {}).get("symbols", {})
        return {s: d for s, d in data.items() if d.get('quoteAsset') == 'USDT'}
    except Exception as e:
        logger.error(f"Error fetching Wallex markets: {e}")
        return {}

def get_wallex_last_trade_price(config, symbol):
    """Fetches the last trade price for a single symbol from Wallex REST API."""
    try:
        api_key = config['price_sources']['wallex'].get('api_key')
        if not api_key or api_key == "YOUR_API_KEY_HERE":
            # This check is important. We don't want to proceed without an API key.
            # We log it once per symbol check, so the user knows.
            return None

        url = config['price_sources']['wallex']['base_url'] + config['price_sources']['wallex']['trades_endpoint']
        params = {'symbol': symbol}
        headers = {'x-api-key': api_key}
        
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        
        trades = r.json().get("result", {}).get("latestTrades", [])
        if trades:
            return float(trades[0]['price'])
        return None
    except Exception as e:
        logger.warning(f"Could not fetch Wallex last trade for {symbol}: {e}")
        return None

def get_coincatch_prices(config):
    """Fetches all prices from CoinCatch."""
    try:
        url = config['price_sources']['coincatch']['base_url'] + config['price_sources']['coincatch']['tickers_endpoint']
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json().get('data', [])
        prices = {ticker.get('symbol', '').replace('-', ''): float(ticker.get('close')) for ticker in data}
        logger.info(f"Fetched {len(prices)} tickers from CoinCatch.")
        return {k: v for k, v in prices.items() if k and v}
    except Exception as e:
        logger.error(f"Error fetching CoinCatch prices: {e}")
        return {}

async def send_telegram_message(config, message_text):
    """Sends a message to the configured Telegram chat."""
    bot_token = config['telegram']['bot_token']
    chat_id = config['telegram']['group_chat_id']
    thread_id = config['telegram']['message_thread_id']
    if not bot_token: return
    try:
        bot = Bot(token=bot_token)
        await bot.send_message(
            chat_id=chat_id, text=message_text, parse_mode=ParseMode.MARKDOWN_V2,
            message_thread_id=thread_id, disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")

# --- Main Analysis Logic ---
async def analyze_prices(config):
    """Compares Wallex last trade price with CoinCatch price and prints details."""
    logger.info("--- Starting New Analysis Cycle ---")
    
    coincatch_prices = get_coincatch_prices(config)
    wallex_markets = get_wallex_usdt_markets(config)

    if not coincatch_prices or not wallex_markets:
        logger.warning("Could not fetch necessary data. Skipping cycle.")
        return

    logger.info(f"Comparing {len(wallex_markets)} Wallex markets with CoinCatch prices...")
    signal_threshold = config['settings']['price_difference_threshold']
    
    # Check for API key once at the start of the cycle
    if not config['price_sources']['wallex'].get('api_key') or config['price_sources']['wallex'].get('api_key') == "YOUR_API_KEY_HERE":
        logger.error("Wallex API Key is missing in config.json. Cannot fetch Wallex prices.")
        return

    for symbol, market_data in wallex_markets.items():
        wallex_price = get_wallex_last_trade_price(config, symbol)
        coincatch_price = coincatch_prices.get(symbol)

        if wallex_price and coincatch_price:
            percentage_diff = ((wallex_price - coincatch_price) / coincatch_price) * 100
            
            # --- چاپ نتایج تحلیل در ترمینال ---
            print(f"=======================================")
            print(f"📊 Symbol: {symbol}")
            print(f"  - Wallex Last Trade : {wallex_price:,.4f} $")
            print(f"  - CoinCatch Price   : {coincatch_price:,.4f} $")
            print(f"  - Difference        : {percentage_diff:+.2f}%")
            
            if abs(percentage_diff) >= signal_threshold:
                print(f"🔥🔥🔥 SIGNAL FOUND! 🔥🔥🔥")
                base_asset = market_data['baseAsset']
                trade_link = f"https://wallex.ir/app/trade/{symbol}"
                
                utc_now = datetime.now(pytz.utc)
                iran_tz = pytz.timezone('Asia/Tehran')
                iran_now = utc_now.astimezone(iran_tz)
                iran_time_str = iran_now.strftime('%Y-%m-%d %H:%M:%S')

                if percentage_diff < 0:
                    action = "BUY"
                    profit_potential = abs(percentage_diff)
                else:
                    action = "SELL"
                    profit_potential = percentage_diff

                def escape_markdown(text):
                    text = str(text)
                    escape_chars = '_*[]()~`>#+-=|{}.!'
                    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

                safe_entry_price = escape_markdown(f"{wallex_price:,.4f}")
                safe_target_price = escape_markdown(f"{coincatch_price:,.4f}")
                safe_profit = escape_markdown(f"{profit_potential:.2f}")
                safe_time = escape_markdown(iran_time_str)

                message = (
                    f"*{action} : {escape_markdown(base_asset)}\-USDT*\n\n"
                    f"Inter Price : `${safe_entry_price}`\n"
                    f"Target Price : `${safe_target_price}`\n"
                    f"Difference : *{safe_profit}\%*\n\n"
                    f"[{'خرید' if action == 'BUY' else 'فروش'} در والکس]({trade_link})\n\n"
                    f"Time : `{safe_time}`"
                )
                await send_telegram_message(config, message)
            else:
                print(f"  - No signal. Difference is below threshold.")

        await asyncio.sleep(0.5) # A short delay between API calls to reduce rate-limit risk

# --- Main Execution Block ---
async def main():
    config = load_config()
    while True:
        await analyze_prices(config)
        wait_time = config['settings']['check_interval_seconds']
        logger.info(f"--- Cycle Complete. Waiting for {wait_time} seconds. ---")
        await asyncio.sleep(wait_time)

if __name__ == "__main__":
    asyncio.run(main())