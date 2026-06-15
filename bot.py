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

TOKEN = "8838203470:AAGph39y3fybTOEAaHXz0mDzYL5IINY4LVo"
BOT_USERNAME = "Annchattingbot"
VIP_LIMIT = 10

# ---------------- DATABASE ---------------- #

conn = sqlite3.connect("anon_chat.db", check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    status TEXT DEFAULT 'ready',
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
CREATE TABLE IF NOT EXISTS waiting_queue (
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

def set_premium(uid):
    cur.execute("UPDATE users SET premium=1 WHERE user_id=?", (uid,))
    conn.commit()

def get_ref(uid):
    cur.execute("SELECT referral_count FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()
    return r[0] if r else 0

def add_ref(uid):
    cur.execute("SELECT referral_count FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()

    if not r:
        return 0

    count = r[0] + 1

    cur.execute("UPDATE users SET referral_count=? WHERE user_id=?", (count, uid))
    conn.commit()

    return count

def in_chat(uid):
    cur.execute("SELECT * FROM active_chats WHERE user1=? OR user2=?", (uid, uid))
    return cur.fetchone()

def add_queue(uid):
    ts = int(time.time())
    if is_premium(uid):
        ts -= 10000
    cur.execute("INSERT OR REPLACE INTO waiting_queue VALUES (?,?)", (uid, ts))
    conn.commit()

def remove_queue(uid):
    cur.execute("DELETE FROM waiting_queue WHERE user_id=?", (uid,))
    conn.commit()

def end_chat(uid):
    cur.execute("DELETE FROM active_chats WHERE user1=? OR user2=?", (uid, uid))
    conn.commit()

# ---------------- MENU ---------------- #

def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Next", callback_data="next")],
        [InlineKeyboardButton("❌ End", callback_data="end")],
        [InlineKeyboardButton("💰 Referral", callback_data="referral")]
    ])

# ---------------- VIP CHECK ---------------- #

async def check_vip(bot, uid):
    count = get_ref(uid)

    if count >= VIP_LIMIT and not is_premium(uid):
        set_premium(uid)

        await bot.send_message(
            uid,
            "💎 VIP UNLOCKED!\n\n"
            "You reached 10 referrals 🎉\n"
            "You are now VIP!"
        )

# ---------------- START (REF SYSTEM) ---------------- #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    create_user(uid)

    # referral tracking
    if context.args:
        try:
            ref_id = int(context.args[0])

            if ref_id != uid:
                add_ref(ref_id)
                await check_vip(context.bot, ref_id)
        except:
            pass

    await update.message.reply_text(
        "Welcome 👋 Start chatting!",
        reply_markup=menu()
    )

# ---------------- MATCHING ---------------- #

async def do_next(uid, context, send):
    if in_chat(uid):
        await send("Already in chat")
        return

    add_queue(uid)
    await send("Searching partner...")

    cur.execute("""
        SELECT user_id FROM waiting_queue
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

# ---------------- END ---------------- #

async def do_end(uid, send):
    end_chat(uid)
    remove_queue(uid)
    await send("Chat ended")

# ---------------- REFERRAL PANEL ---------------- #

async def do_referral(uid, send):
    ref = get_ref(uid)
    prem = is_premium(uid)

    link = f"https://t.me/{BOT_USERNAME}?start={uid}"

    text = (
        "💰 REFERRAL SYSTEM 💰\n\n"
        f"👥 Referrals: {ref}/{VIP_LIMIT}\n"
        f"⭐ Status: {'VIP ACTIVE' if prem else 'FREE USER'}\n\n"
        f"Invite Link:\n{link}"
    )

    await send(text)

# ---------------- BUTTONS ---------------- #

async def next_btn(update, context):
    q = update.callback_query
    await q.answer()
    await do_next(q.from_user.id, context, q.message.reply_text)

async def end_btn(update, context):
    q = update.callback_query
    await q.answer()
    await do_end(q.from_user.id, q.message.reply_text)

async def referral_btn(update, context):
    q = update.callback_query
    await q.answer()
    await do_referral(q.from_user.id, q.message.reply_text)

# ---------------- COMMANDS ---------------- #

async def next_cmd(update, context):
    await do_next(update.effective_user.id, context, update.message.reply_text)

async def end_cmd(update, context):
    await do_end(update.effective_user.id, update.message.reply_text)

async def referral_cmd(update, context):
    await do_referral(update.effective_user.id, update.message.reply_text)

# ---------------- CHAT ---------------- #

async def forward(update, context):
    uid = update.effective_user.id

    chat = in_chat(uid)
    if not chat:
        return

    u1, u2 = chat
    partner = u2 if uid == u1 else u1

    await context.bot.send_message(partner, update.message.text)

# ---------------- APP ---------------- #

app = Application.builder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("next", next_cmd))
app.add_handler(CommandHandler("end", end_cmd))
app.add_handler(CommandHandler("referral", referral_cmd))

app.add_handler(CallbackQueryHandler(next_btn, pattern="^next$"))
app.add_handler(CallbackQueryHandler(end_btn, pattern="^end$"))
app.add_handler(CallbackQueryHandler(referral_btn, pattern="^referral$"))

app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward))

print("Bot running...")
app.run_polling()