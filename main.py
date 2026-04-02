import os
import json
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_TELEGRAM_TOKEN")
ETHERSCAN_KEY  = os.environ.get("ETHERSCAN_KEY",  "YOUR_ETHERSCAN_KEY")

# { chat_id: set(addresses) }
watched = {}
# { address: last_tx_hash }
last_tx = {}

async def get_latest_tx(session, address):
    url = (
        f"https://api.etherscan.io/api"
        f"?module=account&action=txlist"
        f"&address={address}"
        f"&startblock=0&endblock=99999999"
        f"&page=1&offset=1&sort=desc"
        f"&apikey={ETHERSCAN_KEY}"
    )
    async with session.get(url) as r:
        data = await r.json()
    if data["status"] == "1" and data["result"]:
        return data["result"][0]
    return None

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Ethereum Wallet Tracker*\n\n"
        "Lệnh:\n"
        "/watch `0xAddress` — theo dõi ví\n"
        "/unwatch `0xAddress` — bỏ theo dõi\n"
        "/list — xem danh sách đang theo dõi",
        parse_mode="Markdown"
    )

async def watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if not ctx.args:
        await update.message.reply_text("⚠️ Dùng: /watch 0xAddress")
        return
    addr = ctx.args[0].lower()
    if not addr.startswith("0x") or len(addr) != 42:
        await update.message.reply_text("⚠️ Địa chỉ không hợp lệ.")
        return
    watched.setdefault(cid, set()).add(addr)
    await update.message.reply_text(f"✅ Đang theo dõi `{addr}`", parse_mode="Markdown")

async def unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if not ctx.args:
        await update.message.reply_text("⚠️ Dùng: /unwatch 0xAddress")
        return
    addr = ctx.args[0].lower()
    if cid in watched:
        watched[cid].discard(addr)
    await update.message.reply_text(f"🗑️ Đã bỏ theo dõi `{addr}`", parse_mode="Markdown")

async def list_wallets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    addrs = watched.get(cid, set())
    if not addrs:
        await update.message.reply_text("Chưa theo dõi ví nào. Dùng /watch 0xAddress")
        return
    msg = "📋 *Ví đang theo dõi:*\n" + "\n".join(f"• `{a}`" for a in addrs)
    await update.message.reply_text(msg, parse_mode="Markdown")

async def poll_loop(app: Application):
    await asyncio.sleep(5)
    async with aiohttp.ClientSession() as session:
        while True:
            all_addrs = set(a for s in watched.values() for a in s)
            for addr in all_addrs:
                tx = await get_latest_tx(session, addr)
                if not tx:
                    continue
                txhash = tx["hash"]
                if last_tx.get(addr) == txhash:
                    continue
                last_tx[addr] = txhash

                val = int(tx["value"]) / 1e18
                direction = "📤 Gửi đi" if tx["from"].lower() == addr else "📥 Nhận"
                msg = (
                    f"{direction} — `{addr[:6]}...{addr[-4:]}`\n"
                    f"├ Từ: `{tx['from'][:10]}...`\n"
                    f"├ Đến: `{tx['to'][:10]}...`\n"
                    f"├ Số ETH: `{val:.4f} ETH`\n"
                    f"└ [Xem TX](https://etherscan.io/tx/{txhash})"
                )
                for cid, addrs in watched.items():
                    if addr in addrs:
                        await app.bot.send_message(cid, msg, parse_mode="Markdown")

            await asyncio.sleep(30)  # poll mỗi 30 giây

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
