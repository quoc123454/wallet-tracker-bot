import os
import json
import asyncio
import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_TELEGRAM_TOKEN")
ETHERSCAN_KEY  = os.environ.get("ETHERSCAN_KEY", "YOUR_ETHERSCAN_KEY")
DATA_FILE      = "/tmp/watched.json"

CHAINS = {
    "eth": {
        "name": "Ethereum",
        "api": "https://api.etherscan.io/api",
        "key": ETHERSCAN_KEY,
        "explorer_tx": "https://etherscan.io/tx",
        "symbol": "ETH",
    },
    "base": {
        "name": "Base",
        "api": "https://api.basescan.org/api",
        "key": ETHERSCAN_KEY,
        "explorer_tx": "https://basescan.org/tx",
        "symbol": "ETH",
    },
}

# { "chat_id": { "eth": ["addr1", ...], "base": [...] } }
watched = {}
# { "chain:address": last_tx_hash }
last_tx = {}

def save():
    with open(DATA_FILE, "w") as f:
        json.dump(watched, f)

def load():
    global watched
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            watched = json.load(f)
    print(f"[boot] Loaded {sum(len(v) for d in watched.values() for v in d.values())} addresses", flush=True)

async def fetch(session, url):
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
        return await r.json()

async def get_latest_tx(session, chain_id, address):
    c = CHAINS[chain_id]
    url = (f"{c['api']}?module=account&action=txlist"
           f"&address={address}&startblock=0&endblock=99999999"
           f"&page=1&offset=1&sort=desc&apikey={c['key']}")
    try:
        data = await fetch(session, url)
        if data["status"] == "1" and data["result"]:
            return data["result"][0]
    except Exception as e:
        print(f"[error] get_latest_tx {chain_id} {address}: {e}", flush=True)
    return None

async def get_token_transfers(session, chain_id, address, block, txhash):
    c = CHAINS[chain_id]
    url = (f"{c['api']}?module=account&action=tokentx"
           f"&address={address}&startblock={block}&endblock={block}"
           f"&sort=asc&apikey={c['key']}")
    try:
        data = await fetch(session, url)
        if data["status"] == "1":
            return [t for t in data["result"] if t.get("hash", "").lower() == txhash.lower()]
    except Exception as e:
        print(f"[error] get_token_transfers: {e}", flush=True)
    return []

def fmt_val(value, decimals):
    try:
        v = int(value) / (10 ** int(decimals))
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    except:
        return value

def classify_tx(tx, addr, tokens):
    is_from = tx["from"].lower() == addr
    has_input = tx.get("input", "0x") not in ("0x", "")
    eth_val = int(tx.get("value", 0))

    tokens_in  = [t for t in tokens if t["to"].lower() == addr]
    tokens_out = [t for t in tokens if t["from"].lower() == addr]

    if tokens_in and (tokens_out or (eth_val > 0 and is_from)):
        out = " + ".join(f"{fmt_val(t['value'], t['tokenDecimal'])} {t['tokenSymbol']}" for t in tokens_out)
        if not out and eth_val > 0:
            out = f"{eth_val/1e18:.6f} ETH"
        inp = " + ".join(f"{fmt_val(t['value'], t['tokenDecimal'])} {t['tokenSymbol']}" for t in tokens_in)
        return "🔄 Swap", f"{out} → {inp}"

    if tokens_out and not tokens_in:
        return "📤 Gửi Token", " + ".join(f"{fmt_val(t['value'], t['tokenDecimal'])} {t['tokenSymbol']}" for t in tokens_out)

    if tokens_in and not tokens_out:
        return "📥 Nhận Token", " + ".join(f"{fmt_val(t['value'], t['tokenDecimal'])} {t['tokenSymbol']}" for t in tokens_in)

    if eth_val > 0 and not has_input:
        s = f"{eth_val/1e18:.6f} ETH"
        return ("📤 Gửi ETH", s) if is_from else ("📥 Nhận ETH", s)

    if has_input:
        fn = tx.get("functionName", "").split("(")[0] or "unknown"
        return "⚙️ Contract Call", fn

    return "❓ Giao dịch", ""

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Wallet Tracker — Ethereum & Base*\n\n"
        "/watch `0xAddress` — theo dõi cả 2 mạng\n"
        "/watch `0xAddress` eth — chỉ Ethereum\n"
        "/watch `0xAddress` base — chỉ Base\n"
        "/unwatch `0xAddress` — bỏ theo dõi\n"
        "/list — danh sách đang theo dõi",
        parse_mode="Markdown"
    )

async def watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    if not ctx.args:
        await update.message.reply_text("⚠️ Dùng: /watch 0xAddress [eth|base]")
        return
    addr = ctx.args[0].lower()
    if not addr.startswith("0x") or len(addr) != 42:
        await update.message.reply_text("⚠️ Địa chỉ không hợp lệ.")
        return
    chain_arg = ctx.args[1].lower() if len(ctx.args) > 1 else "all"
    chains = [chain_arg] if chain_arg in CHAINS else list(CHAINS.keys())

    if cid not in watched:
        watched[cid] = {c: [] for c in CHAINS}
    for c in chains:
        if addr not in watched[cid][c]:
            watched[cid][c].append(addr)
    save()

    labels = " + ".join(CHAINS[c]["name"] for c in chains)
    await update.message.reply_text(
        f"✅ Đang theo dõi `{addr}`\n🌐 Mạng: {labels}", parse_mode="Markdown"
    )

async def unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    if not ctx.args:
        await update.message.reply_text("⚠️ Dùng: /unwatch 0xAddress")
        return
    addr = ctx.args[0].lower()
    if cid in watched:
        for c in CHAINS:
            if addr in watched[cid][c]:
                watched[cid][c].remove(addr)
        save()
    await update.message.reply_text(f"🗑️ Đã bỏ theo dõi `{addr}`", parse_mode="Markdown")

async def list_wallets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
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
                all_addrs = list({
                    a for data in watched.values()
                    for a in data.get(chain_id, [])
                })
                print(f"[poll] {chain_id}: {len(all_addrs)} addrs", flush=True)
                for addr in all_addrs:
                    tx = await get_latest_tx(session, chain_id, addr)
                    if not tx:
                        continue
                    key = f"{chain_id}:{addr}"
                    txhash = tx["hash"]
                    if last_tx.get(key) == txhash:
                        continue
                    last_tx[key] = txhash

                    tokens = await get_token_transfers(session, chain_id, addr, tx["blockNumber"], txhash)
                    kind, detail = classify_tx(tx, addr, tokens)
                    short = f"`{addr[:6]}...{addr[-4:]}`"

                    msg = (
                        f"{kind} — {short}\n"
                        f"🌐 *{chain['name']}*\n"
                        f"├ {detail}\n"
                        f"└ [Xem TX]({chain['explorer_tx']}/{txhash})"
                    )
                    for cid, data in watched.items():
                        if addr in data.get(chain_id, []):
                            try:
                                await app.bot.send_message(int(cid), msg, parse_mode="Markdown")
                            except Exception as e:
                                print(f"[error] send_message: {e}", flush=True)

            await asyncio.sleep(30)

async def post_init(app: Application):
    load()
    asyncio.create_task(poll_loop(app))

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("watch", watch))
    app.add_handler(CommandHandler("unwatch", unwatch))
    app.add_handler(CommandHandler("list", list_wallets))
    app.run_polling()

if __name__ == "__main__":
    main()
