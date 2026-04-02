import os
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_TELEGRAM_TOKEN")
ETHERSCAN_KEY  = os.environ.get("ETHERSCAN_KEY",  "YOUR_ETHERSCAN_KEY")

CHAINS = {
    "eth": {
        "name": "Ethereum",
        "api": "https://api.etherscan.io/api",
        "key": ETHERSCAN_KEY,
        "explorer": "https://etherscan.io/tx",
        "symbol": "ETH",
    },
    "base": {
        "name": "Base",
        "api": "https://api.basescan.org/api",
        "key": ETHERSCAN_KEY,
        "explorer": "https://basescan.org/tx",
        "symbol": "ETH",
    },
}

# { chat_id: { "eth": set(addresses), "base": set(addresses) } }
watched = {}
# { "eth:address": last_tx_hash }
last_tx = {}

async def get_latest_tx(session, chain_id, address):
    chain = CHAINS[chain_id]
    url = (
        f"{chain['api']}"
        f"?module=account&action=txlist"
        f"&address={address}"
        f"&startblock=0&endblock=99999999"
        f"&page=1&offset=1&sort=desc"
        f"&apikey={chain['key']}"
    )
    async with session.get(url) as r:
        data = await r.json()
    if data["status"] == "1" and data["result"]:
        return data["result"][0]
    return None

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Ethereum & Base Wallet Tracker*\n\n"
        "Lệnh:\n"
        "/watch `0xAddress` — theo dõi trên cả 2 mạng\n"
        "/watch `0xAddress` eth — chỉ Ethereum\n"
        "/watch `0xAddress` base — chỉ Base\n"
        "/unwatch `0xAddress` — bỏ theo dõi\n"
        "/list — xem danh sách đang theo dõi",
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
        f"✅ Đang theo dõi `{addr}`\n🌐 Mạng: {labels}",
        parse_mode="Markdown"
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
    chains_data = watched.get(cid, {})
    lines = []
    for c, addrs in chains_data.items():
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
            for chain_id in CHAINS:
                all_addrs = set(
                    a for data in watched.values()
                    for a in data.get(chain_id, set())
                )
                for addr in all_addrs:
                    tx = await get_latest_tx(session, chain_id, addr)
                    if not tx:
                        continue
                    key = f"{chain_id}:{addr}"
                    txhash = tx["hash"]
                    if last_tx.get(key) == txhash:
                        continue
                    last_tx[key] = txhash

                    chain = CHAINS[chain_id]
                    val = int(tx["value"]) / 1e18
                    direction = "📤 Gửi đi" if tx["from"].lower() == addr else "📥 Nhận"
                    msg = (
                        f"{direction} — `{addr[:6]}...{addr[-4:]}`\n"
                        f"🌐 Mạng: *{chain['name']}*\n"
                        f"├ Từ: `{tx['from'][:10]}...`\n"
                        f"├ Đến: `{tx['to'][:10]}...`\n"
                        f"├ Số: `{val:.4f} {chain['symbol']}`\n"
                        f"└ [Xem TX]({chain['explorer']}/{txhash})"
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
