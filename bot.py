import os
import sqlite3
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------- TOKEN (SAFE) ---------------- #

TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TOKEN:
    raise Exception("TELEGRAM_TOKEN is not set in environment variables!")

# ---------------- DATABASE ---------------- #

conn = sqlite3.connect("anon_chat.db", check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    referral_count INTEGER DEFAULT 0,
    premium INTEGER DEFAULT 0
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS active_chats (
    user1 INTEGER,
    user2 INTEGER
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS queue (
    user_id INTEGER PRIMARY KEY,
    timestamp INTEGER
)
""")

conn.commit()

# ---------------- HELPERS ---------------- #

def create_user(uid):
    cur.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
    conn.commit()

def is_premium(uid):
    cur.execute("SELECT premium FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()
    return r and r[0] == 1

def get_ref(uid):
    cur.execute("SELECT referral_count FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()
    return r[0] if r else 0

def add_ref(uid):
    count = get_ref(uid) + 1
    cur.execute("UPDATE users SET referral_count=? WHERE user_id=?", (count, uid))
    conn.commit()
    return count

def set_premium(uid):
    cur.execute("UPDATE users SET premium=1 WHERE user_id=?", (uid,))
    conn.commit()

def in_chat(uid):
    cur.execute("SELECT * FROM active_chats WHERE user1=? OR user2=?", (uid, uid))
    return cur.fetchone()

def add_queue(uid):
    ts = int(time.time())
    if is_premium(uid):
        ts -= 10000
    cur.execute("INSERT OR REPLACE INTO queue VALUES (?,?)", (uid, ts))
    conn.commit()

def remove_queue(uid):
    cur.execute("DELETE FROM queue WHERE user_id=?", (uid,))
    conn.commit()

def end_chat(uid):
    cur.execute("DELETE FROM active_chats WHERE user1=? OR user2=?", (uid, uid))
    conn.commit()

# ---------------- UI ---------------- #

def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Next", callback_data="next")],
        [InlineKeyboardButton("❌ End", callback_data="end")],
        [InlineKeyboardButton("💰 Referral", callback_data="ref")]
    ])

# ---------------- VIP CHECK ---------------- #

async def check_vip(bot, uid):
    if get_ref(uid) >= 10 and not is_premium(uid):
        set_premium(uid)
        await bot.send_message(uid, "💎 VIP UNLOCKED! You reached 10 referrals!")

# ---------------- START ---------------- #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    create_user(uid)

    if context.args:
        try:
            ref_id = int(context.args[0])
            if ref_id != uid:
                add_ref(ref_id)
                await check_vip(context.bot, ref_id)
        except:
            pass

    await update.message.reply_text("Welcome 👋", reply_markup=menu())

# ---------------- MATCHING ---------------- #

async def next_user(uid, context, send):
    if in_chat(uid):
        await send("Already in chat")
        return

    add_queue(uid)
    await send("Searching...")

    cur.execute("""
        SELECT user_id FROM queue
        WHERE user_id != ?
        ORDER BY timestamp ASC
        LIMIT 1
    """, (uid,))

    row = cur.fetchone()

    if not row:
        return

    partner = row[0]

    remove_queue(uid)
    remove_queue(partner)

    cur.execute("INSERT INTO active_chats VALUES (?,?)", (uid, partner))
    conn.commit()

    await context.bot.send_message(uid, "Connected!", reply_markup=menu())
    await context.bot.send_message(partner, "Connected!", reply_markup=menu())

# ---------------- REFERRAL ---------------- #

async def referral(uid, send):
    ref = get_ref(uid)
    prem = is_premium(uid)

    link = f"https://t.me/YOUR_BOT_USERNAME?start={uid}"

    await send(
        f"💰 Referral System\n\n"
        f"👥 Referrals: {ref}/10\n"
        f"⭐ Status: {'VIP' if prem else 'FREE'}\n\n"
        f"Link:\n{link}"
    )

# ---------------- BUTTONS ---------------- #

async def btn_next(update, context):
    q = update.callback_query
    await q.answer()
    await next_user(q.from_user.id, context, q.message.reply_text)

async def btn_end(update, context):
    q = update.callback_query
    await q.answer()
    end_chat(q.from_user.id)
    remove_queue(q.from_user.id)
    await q.message.reply_text("Chat ended")

async def btn_ref(update, context):
    q = update.callback_query
    await q.answer()
    await referral(q.from_user.id, q.message.reply_text)

# ---------------- APP ---------------- #

app = Application.builder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(btn_next, pattern="^next$"))
app.add_handler(CallbackQueryHandler(btn_end, pattern="^end$"))
app.add_handler(CallbackQueryHandler(btn_ref, pattern="^ref$"))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: None))

print("Bot running...")
app.run_polling()