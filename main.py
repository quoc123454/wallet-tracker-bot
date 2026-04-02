import os
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_TELEGRAM_TOKEN")
ETHERSCAN_KEY  = os.environ.get("ETHERSCAN_KEY", "YOUR_ETHERSCAN_KEY")

CHAINS = {
    "eth": {
        "name": "Ethereum",
        "api": "https://api.etherscan.io/api",
        "key": ETHERSCAN_KEY,
        "explorer_tx": "https://etherscan.io/tx",
        "explorer_addr": "https://etherscan.io/address",
        "symbol": "ETH",
    },
    "base": {
        "name": "Base",
        "api": "https://api.basescan.org/api",
        "key": ETHERSCAN_KEY,
        "explorer_tx": "https://basescan.org/tx",
        "explorer_addr": "https://basescan.org/address",
        "symbol": "ETH",
    },
}

# { chat_id: { "eth": set(addresses), "base": set(addresses) } }
watched = {}
# { "chain:address": last_tx_hash }
last_tx = {}

async def fetch(session, url):
    async with session.get(url) as r:
        return await r.json()

async def get_latest_normal_tx(session, chain_id, address):
    c = CHAINS[chain_id]
    # Lấy cả TX gửi và nhận bằng cách query không lọc địa chỉ cụ thể
    url = (f"{c['api']}?module=account&action=txlist"
           f"&address={address}&startblock=0&endblock=99999999"
           f"&page=1&offset=5&sort=desc&apikey={c['key']}")
    data = await fetch(session, url)
    if data["status"] == "1" and data["result"]:
        return data["result"][0]
    return None

async def get_token_transfers(session, chain_id, address, block):
    c = CHAINS[chain_id]
    url = (f"{c['api']}?module=account&action=tokentx"
           f"&address={address}&startblock={block}&endblock={block}"
           f"&sort=asc&apikey={c['key']}")
    data = await fetch(session, url)
    if data["status"] == "1":
        return data["result"]
    return []

def fmt_val(value, decimals):
    try:
        v = int(value) / (10 ** int(decimals))
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    except:
        return value

def classify_tx(tx, addr, token_transfers):
    is_from = tx["from"].lower() == addr
    has_input = tx.get("input", "0x") not in ("0x", "")
    eth_val = int(tx.get("value", 0))

    # Phân loại swap
    if has_input and token_transfers:
        tokens_in  = [t for t in token_transfers if t["to"].lower() == addr]
        tokens_out = [t for t in token_transfers if t["from"].lower() == addr]

        if tokens_in and (tokens_out or eth_val > 0):
            out_str = " + ".join(f"{fmt_val(t['value'], t['tokenDecimal'])} {t['tokenSymbol']}" for t in tokens_out)
            if eth_val > 0 and not tokens_out:
                out_str = f"{eth_val/1e18:.6f} ETH"
            in_str = " + ".join(f"{fmt_val(t['value'], t['tokenDecimal'])} {t['tokenSymbol']}" for t in tokens_in)
            return "🔄 Swap", f"{out_str} → {in_str}"

        if tokens_out and not tokens_in:
            out_str = " + ".join(f"{fmt_val(t['value'], t['tokenDecimal'])} {t['tokenSymbol']}" for t in tokens_out)
            return "📤 Gửi Token", out_str

        if tokens_in and not tokens_out:
            in_str = " + ".join(f"{fmt_val(t['value'], t['tokenDecimal'])} {t['tokenSymbol']}" for t in tokens_in)
            return "📥 Nhận Token", in_str

    # Native ETH transfer
    if eth_val > 0 and not has_input:
        eth_str = f"{eth_val/1e18:.6f} ETH"
        if is_from:
            return "📤 Gửi ETH", eth_str
        else:
            return "📥 Nhận ETH", eth_str

    # Contract interaction không rõ
    if has_input:
        return "⚙️ Contract Call", tx.get("functionName", "").split("(")[0] or "unknown"

    return "❓ Giao dịch", ""

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Wallet Tracker — Ethereum & Base*\n\n"
        "Lệnh:\n"
        "/watch `0xAddress` — theo dõi cả 2 mạng\n"
        "/watch `0xAddress` eth — chỉ Ethereum\n"
        "/watch `0xAddress` base — chỉ Base\n"
        "/unwatch `0xAddress` — bỏ theo dõi\n"
        "/list — danh sách đang theo dõi",
        parse_mode="Markdown"
    )

async def watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if not ctx.args:
        await update.message.reply_text("⚠️ Dùng: /watch 0xAddress [eth|base]")
        return
    addr = ctx.args[0].lower()
    if not addr.startswith("0x") or len(addr) != 42:
        await update.message.reply_text("⚠️ Địa chỉ không hợp lệ.")
        return
    chain_arg = ctx.args[1].lower() if len(ctx.args) > 1 else "all"
    chains = [chain_arg] if chain_arg in CHAINS else list(CHAINS.keys())
    watched.setdefault(cid, {c: set() for c in CHAINS})
    for c in chains:
        watched[cid][c].add(addr)
    labels = " + ".join(CHAINS[c]["name"] for c in chains)
    await update.message.reply_text(
        f"✅ Đang theo dõi `{addr}`\n🌐 Mạng: {labels}", parse_mode="Markdown"
    )

async def unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if not ctx.args:
        await update.message.reply_text("⚠️ Dùng: /unwatch 0xAddress")
        return
    addr = ctx.args[0].lower()
    if cid in watched:
        for c in CHAINS:
            watched[cid][c].discard(addr)
    await update.message.reply_text(f"🗑️ Đã bỏ theo dõi `{addr}`", parse_mode="Markdown")

async def list_wallets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    data = watched.get(cid, {})
    lines = []
    for c, addrs in data.items():
        if addrs:
            lines.append(f"*{CHAINS[c]['name']}:*")
            lines += [f"• `{a}`" for a in addrs]
    if not lines:
        await update.message.reply_text("Chưa theo dõi ví nào. Dùng /watch 0xAddress")
        return
    await update.message.reply_text("📋 *Ví đang theo dõi:*\n" + "\n".join(lines), parse_mode="Markdown")

async def poll_loop(app: Application):
    await asyncio.sleep(5)
    async with aiohttp.ClientSession() as session:
        while True:
            for chain_id, chain in CHAINS.items():
                all_addrs = set(
                    a for data in watched.values()
                    for a in data.get(chain_id, set())
                )
                for addr in all_addrs:
                    tx = await get_latest_normal_tx(session, chain_id, addr)
                    if not tx:
                        continue
                    key = f"{chain_id}:{addr}"
                    txhash = tx["hash"]
                    if last_tx.get(key) == txhash:
                        continue
                    last_tx[key] = txhash

                    # Lấy token transfers trong cùng block
                    token_transfers = await get_token_transfers(session, chain_id, addr, tx["blockNumber"])
                    # Chỉ lấy transfers liên quan đến tx này
                    token_transfers = [t for t in token_transfers if t.get("hash", "").lower() == txhash.lower()]

                    kind, detail = classify_tx(tx, addr, token_transfers)
                    short = f"`{addr[:6]}...{addr[-4:]}`"

                    msg = (
                        f"{kind} — {short}\n"
                        f"🌐 *{chain['name']}*\n"
                        f"├ {detail}\n"
                        f"└ [Xem TX]({chain['explorer_tx']}/{txhash})"
                    )

                    for cid, data in watched.items():
                        if addr in data.get(chain_id, set()):
                            await app.bot.send_message(cid, msg, parse_mode="Markdown")

            await asyncio.sleep(30)

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("watch", watch))
    app.add_handler(CommandHandler("unwatch", unwatch))
    app.add_handler(CommandHandler("list", list_wallets))

    loop = asyncio.get_event_loop()
    loop.create_task(poll_loop(app))
    app.run_polling()

if __name__ == "__main__":
    main()
