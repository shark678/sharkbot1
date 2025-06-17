import os
import re
import json
import aiohttp
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# === 环境变量配置 ===
ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY")
BSCSCAN_API_KEY = os.environ.get("BSCSCAN_API_KEY")
TRONGRID_API_KEY = os.environ.get("TRONGRID_API_KEY")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# === 表情图标映射 ===
TOKEN_EMOJIS = {
    "USDT": "💵", "USDC": "💸", "ETH": "🧫", "BNB": "🟡", "DAI": "🟠",
    "TRX": "🔺", "SHIB": "🐶", "BTC": "🟧", "BUSD": "💰", "TUSD": "🔪"
}

COMMON_TOKENS = {
    "ERC20": [
        {"symbol": "USDT", "contract": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "decimals": 6},
        {"symbol": "USDC", "contract": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "decimals": 6}
    ],
    "BEP20": [
        {"symbol": "USDT", "contract": "0x55d398326f99059fF775485246999027B3197955", "decimals": 18},
        {"symbol": "USDC", "contract": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d", "decimals": 18}
    ]
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def detect_chain(address: str):
    if re.fullmatch(r"0x[a-fA-F0-9]{40}", address):
        return ['ERC20', 'BEP20']
    if re.fullmatch(r"T[a-zA-Z0-9]{33}", address):
        return ['TRC20']
    return []

def shorten(addr: str):
    return f"{addr[:6]}…{addr[-4:]}"

def fmt_amount(val: float):
    return f"{val:,.4f}".rstrip('0').rstrip('.')

def get_token_emoji(symbol: str):
    return TOKEN_EMOJIS.get(symbol.upper(), "🔹")

WELCOME_TEXT = (
    "👋 *欢迎使用多链地址查询机器人！*\n\n"
    "📌 功能说明：\n"
    "- 输入地址，自动识别链类型\n"
    "- 查询 TRC20 / ERC20 / BEP20 地址余额和交易记录\n"
    "- 优先展示余额多的链\n\n"
    "📥 请直接发送地址开始查询吧！"
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("🔍 开始查询", switch_inline_query_current_chat="")]]
    await update.message.reply_markdown(WELCOME_TEXT, reply_markup=InlineKeyboardMarkup(kb))

async def handle_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    address = update.message.text.strip()
    chains = detect_chain(address)
    if not chains:
        await update.message.reply_text("⚠️ 请输入有效地址（0x... 或 T... 开头）")
        return
    context.user_data['address'] = address
    context.user_data['chains'] = chains
    context.user_data['page'] = {chain: 0 for chain in chains}
    if 'ERC20' in chains and 'BEP20' in chains:
        erc = await fetch_evm_balances(address, ETHERSCAN_API_KEY, "https://api.etherscan.io/api", COMMON_TOKENS["ERC20"])
        bep = await fetch_evm_balances(address, BSCSCAN_API_KEY, "https://api.bscscan.com/api", COMMON_TOKENS["BEP20"])
        erc_total = sum(erc.values())
        bep_total = sum(bep.values())
        prefer_chain = "ERC20" if erc_total >= bep_total else "BEP20"
    else:
        prefer_chain = chains[0]
    context.user_data['current_chain'] = prefer_chain
    await query_and_respond(update, context, prefer_chain, 0)

async def fetch_evm_balances(address, api_key, api_url, token_list):
    balances = {}
    async with aiohttp.ClientSession() as session:
        for token in token_list:
            try:
                params = {
                    "module": "account",
                    "action": "tokenbalance",
                    "contractaddress": token["contract"],
                    "address": address,
                    "tag": "latest",
                    "apikey": api_key
                }
                async with session.get(api_url, params=params, timeout=10) as resp:
                    data = await resp.json()
                    raw = int(data.get("result", 0))
                    value = raw / (10 ** token["decimals"])
                    if value > 0:
                        balances[token["symbol"]] = value
            except:
                continue
    return balances

async def query_and_respond(update, context, chain, page, edit=False):
    address = context.user_data['address']
    if chain == "ERC20":
        result = await fetch_evm(address, page, ETHERSCAN_API_KEY, "https://api.etherscan.io/api", "ERC20")
    elif chain == "BEP20":
        result = await fetch_evm(address, page, BSCSCAN_API_KEY, "https://api.bscscan.com/api", "BEP20")
    else:
        result = await fetch_trc20(address, page)
    text, reply_markup = result
    if edit:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    else:
        await update.message.reply_markdown(text, reply_markup=reply_markup)

async def fetch_evm(address, page, api_key, api_url, chain_label):
    tx_params = {
        "module": "account", "action": "tokentx", "address": address,
        "page": page + 1, "offset": 15, "sort": "desc", "apikey": api_key
    }
    token_list = COMMON_TOKENS.get(chain_label, [])
    balances = await fetch_evm_balances(address, api_key, api_url, token_list)
    async with aiohttp.ClientSession() as session:
        async with session.get(api_url, params=tx_params) as resp:
            data = await resp.json()
    txs = data.get("result", [])
    text = f"*📦 {chain_label} 地址：* `{shorten(address)}`\n\n*💰 余额：*\n"
    found = False
    for sym, amt in sorted(balances.items(), key=lambda x: -x[1]):
        emoji = get_token_emoji(sym)
        text += f"{emoji} `{sym}`：{fmt_amount(amt)}\n"
        found = True
    if not found:
        text += "_无余额_\n"
    text += f"\n🧾 *最近交易记录*（第 {page+1} 页）：\n"
    for tx in txs:
        sym = tx.get("tokenSymbol", "???")
        decimals = int(tx.get("tokenDecimal", 0))
        value = int(tx.get("value", 0)) / (10 ** decimals) if decimals else 0
        from_addr, to_addr = tx["from"], tx["to"]
        direction = "📥" if to_addr.lower() == address.lower() else "📤"
        other = from_addr if direction == "📥" else to_addr
        text += f"{direction} `{fmt_amount(value)} {sym}` → `{shorten(other)}`\n"
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"{chain_label}:prev"))
    if len(txs) == 15:
        buttons.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"{chain_label}:next"))
    buttons.append(InlineKeyboardButton("🔁 切换链", callback_data="switch"))
    buttons.append(InlineKeyboardButton("🔍 继续查询", switch_inline_query_current_chat=""))
    return text, InlineKeyboardMarkup([buttons])

async def fetch_trc20(address, page):
    url = f"https://api.trongrid.io/v1/accounts/{address}/transactions/trc20"
    params = {"limit": 15, "only_confirmed": "true", "order_by": "block_timestamp,desc"}
    headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            data = await resp.json()
    txs = data.get("data", [])
    balances = {}
    for tx in txs:
        info = tx.get("token_info", {})
        sym = info.get("symbol", "???")
        decimals = int(info.get("decimals", 0))
        value = int(tx.get("value", 0)) / (10 ** decimals) if decimals else 0
        if tx["to"] == address:
            balances[sym] = balances.get(sym, 0) + value
    text = f"*📦 TRC20 地址：* `{shorten(address)}`\n\n*💰 余额：*\n"
    found = False
    for sym, amt in sorted(balances.items(), key=lambda x: -x[1]):
        emoji = get_token_emoji(sym)
        text += f"{emoji} `{sym}`：{fmt_amount(amt)}\n"
        found = True
    if not found:
        text += "_无余额_\n"
    text += f"\n🧾 *最近交易记录*（第 {page+1} 页）：\n"
    for tx in txs:
        info = tx.get("token_info", {})
        sym = info.get("symbol", "???")
        decimals = int(info.get("decimals", 0))
        value = int(tx.get("value", 0)) / (10 ** decimals) if decimals else 0
        from_addr, to_addr = tx["from"], tx["to"]
        direction = "📥" if to_addr == address else "📤"
        other_party = from_addr if direction == "📥" else to_addr
        text += f"{direction} `{fmt_amount(value)} {sym}` → `{shorten(other_party)}`\n"
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⬅️ 上一页", callback_data="TRC20:prev"))
    if len(txs) == 15:
        buttons.append(InlineKeyboardButton("➡️ 下一页", callback_data="TRC20:next"))
    buttons.append(InlineKeyboardButton("🔁 切换链", callback_data="switch"))
    buttons.append(InlineKeyboardButton("🔍 继续查询", switch_inline_query_current_chat=""))
    return text, InlineKeyboardMarkup([buttons])

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chains = context.user_data.get('chains', [])
    cur_chain = query.data.split(":")[0]
    action = query.data.split(":")[1] if ":" in query.data else ""
    if query.data == "switch":
        idx = chains.index(context.user_data.get('current_chain', chains[0]))
        next_chain = chains[(idx + 1) % len(chains)]
        context.user_data['current_chain'] = next_chain
        page = context.user_data['page'].get(next_chain, 0)
        await query_and_respond(update, context, next_chain, page, edit=True)
    else:
        page = context.user_data['page'].get(cur_chain, 0)
        if action == "next":
            page += 1
        elif action == "prev" and page > 0:
            page -= 1
        context.user_data['page'][cur_chain] = page
        context.user_data['current_chain'] = cur_chain
        await query_and_respond(update, context, cur_chain, page, edit=True)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_address))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
