import os
import sqlite3
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
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
    premium INTEGER DEFAULT 0,
    gender TEXT DEFAULT NULL,
    age INTEGER DEFAULT NULL,
    total_dialogs INTEGER DEFAULT 0,
    today_dialogs INTEGER DEFAULT 0,
    last_dialog_date TEXT DEFAULT NULL,
    sent_messages INTEGER DEFAULT 0,
    received_messages INTEGER DEFAULT 0
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

# Clear stale queue entries on startup
cur.execute("DELETE FROM queue")
conn.commit()

# ---------------- CONVERSATION STATES ---------------- #

GENDER, AGE = range(2)

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

def get_partner(uid):
    """Return the partner's user_id if uid is in an active chat, else None."""
    chat = in_chat(uid)
    if not chat:
        return None
    u1, u2 = chat
    return u2 if uid == u1 else u1

def increment_sent(uid):
    cur.execute("UPDATE users SET sent_messages = sent_messages + 1 WHERE user_id=?", (uid,))
    conn.commit()

def increment_received(uid):
    cur.execute("UPDATE users SET received_messages = received_messages + 1 WHERE user_id=?", (uid,))
    conn.commit()

def increment_dialogs(uid):
    """Increment total dialogs and today's dialogs (resets daily)."""
    today = time.strftime("%Y-%m-%d")
    cur.execute("SELECT last_dialog_date, today_dialogs FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    if row:
        last_date, today_count = row
        if last_date == today:
            new_today = today_count + 1
        else:
            new_today = 1
        cur.execute("""
            UPDATE users
            SET total_dialogs = total_dialogs + 1,
                today_dialogs = ?,
                last_dialog_date = ?
            WHERE user_id=?
        """, (new_today, today, uid))
        conn.commit()

def get_profile(uid):
    cur.execute("""
        SELECT user_id, gender, age, total_dialogs, today_dialogs,
               sent_messages, received_messages
        FROM users WHERE user_id=?
    """, (uid,))
    return cur.fetchone()

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

CONNECTED_MSG = (
    "🎉 Match successful!\n\n"
    "/next – End and start a new match\n"
    "/stop – End the current chat"
)

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

    # Count a new dialog for both users
    increment_dialogs(uid)
    increment_dialogs(partner)

    await context.bot.send_message(uid, CONNECTED_MSG, reply_markup=menu())
    await context.bot.send_message(partner, CONNECTED_MSG, reply_markup=menu())

# ---------------- REFERRAL ---------------- #

async def referral(uid, send):
    ref = get_ref(uid)
    prem = is_premium(uid)

    link = f"https://t.me/Annchattingbot?start={uid}"

    await send(
        f"💰 Referral System\n\n"
        f"👥 Referrals: {ref}/10\n"
        f"⭐ Status: {'VIP' if prem else 'FREE'}\n\n"
        f"Link:\n{link}"
    )

# ---------------- PROFILE SETUP (ConversationHandler) ---------------- #

async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show profile or prompt setup if gender/age not set."""
    uid = update.effective_user.id
    create_user(uid)
    row = get_profile(uid)

    if row:
        user_id, gender, age, total_dialogs, today_dialogs, sent, received = row

        # If profile not fully set up, start setup flow
        if not gender or not age:
            await update.message.reply_text(
                "Let's set up your profile!\n\nPlease enter your gender (M / F):"
            )
            return GENDER

        gender_display = "M" if gender == "M" else "F"
        text = (
            f"#️⃣ ID — {user_id}\n\n"
            f"👫 Gender — {gender_display}\n"
            f"🔞 Age — {age}\n\n"
            f"⭐️ Dialogs\n"
            f"├ Total: {total_dialogs}\n"
            f"└ Today: {today_dialogs}\n\n"
            f"✉️ Messages\n"
            f"├ Sent: {sent}\n"
            f"└ Received: {received}"
        )
        await update.message.reply_text(text)
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "Let's set up your profile!\n\nPlease enter your gender (M / F):"
        )
        return GENDER

async def get_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip().upper()

    if text not in ("M", "F"):
        await update.message.reply_text("Please enter M or F:")
        return GENDER

    context.user_data["gender"] = text
    await update.message.reply_text("Got it! Now enter your age:")
    return AGE

async def get_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()

    if not text.isdigit() or not (10 <= int(text) <= 100):
        await update.message.reply_text("Please enter a valid age (10–100):")
        return AGE

    age = int(text)
    gender = context.user_data.get("gender", "M")

    cur.execute(
        "UPDATE users SET gender=?, age=? WHERE user_id=?",
        (gender, age, uid)
    )
    conn.commit()

    await update.message.reply_text(
        f"✅ Profile saved!\n\nGender: {gender}\nAge: {age}\n\nUse /profile to view your profile anytime."
    )
    return ConversationHandler.END

async def cancel_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Profile setup cancelled.")
    return ConversationHandler.END

# ---------------- BUTTONS ---------------- #

async def btn_next(update, context):
    q = update.callback_query
    await q.answer()

    uid = q.from_user.id
    partner = get_partner(uid)

    if partner:
        # User is skipping from an active chat — notify partner
        end_chat(uid)
        try:
            await context.bot.send_message(
                partner,
                "🤚 Your partner left the chat",
                reply_markup=menu()
            )
        except Exception as e:
            print(f"ERROR: could not notify partner {partner}: {e}")

    await next_user(uid, context, q.message.reply_text)

async def btn_end(update, context):
    q = update.callback_query
    await q.answer()

    uid = q.from_user.id
    partner = get_partner(uid)

    end_chat(uid)
    remove_queue(uid)

    await q.message.reply_text("🤚 You left the chat", reply_markup=menu())

    if partner:
        try:
            await context.bot.send_message(
                partner,
                "🤚 Your partner left the chat",
                reply_markup=menu()
            )
        except Exception as e:
            print(f"ERROR: could not notify partner {partner}: {e}")

async def btn_ref(update, context):
    q = update.callback_query
    await q.answer()
    await referral(q.from_user.id, q.message.reply_text)


# ---------------- COMMANDS ---------------- #

async def next_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    partner = get_partner(uid)

    if partner:
        # Notify partner they were skipped
        end_chat(uid)
        try:
            await context.bot.send_message(
                partner,
                "🤚 Your partner left the chat",
                reply_markup=menu()
            )
        except Exception as e:
            print(f"ERROR: could not notify partner {partner}: {e}")

    await next_user(uid, context, update.message.reply_text)

async def end_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    partner = get_partner(uid)

    end_chat(uid)
    remove_queue(uid)

    await update.message.reply_text("🤚 You left the chat", reply_markup=menu())

    if partner:
        try:
            await context.bot.send_message(
                partner,
                "🤚 Your partner left the chat",
                reply_markup=menu()
            )
        except Exception as e:
            print(f"ERROR: could not notify partner {partner}: {e}")

# /stop is an alias for /end
async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await end_cmd(update, context)

async def referral_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await referral(
        update.effective_user.id,
        update.message.reply_text
    )


# ---------------- MESSAGE FORWARDING ---------------- #

async def forward_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    chat = in_chat(uid)

    print(f"DEBUG: uid={uid}, in_chat={chat}")

    if not chat:
        await update.message.reply_text(
            "You are not connected to anyone. Press 🔎 Next to find a partner."
        )
        return

    u1, u2 = chat
    partner = u2 if uid == u1 else u1

    try:
        await context.bot.send_message(
            chat_id=partner,
            text=update.message.text
        )
        increment_sent(uid)
        increment_received(partner)
        print(f"DEBUG: forwarded message from {uid} to {partner}")
    except Exception as e:
        print(f"ERROR: failed to forward message from {uid} to {partner}: {e}")
        await update.message.reply_text(
            "⚠️ Could not deliver your message. Your partner may have blocked the bot or left."
        )
        end_chat(uid)


# ---------------- APP ---------------- #

app = Application.builder().token(TOKEN).build()

# Profile setup conversation
profile_conv = ConversationHandler(
    entry_points=[CommandHandler("profile", profile_cmd)],
    states={
        GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_gender)],
        AGE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, get_age)],
    },
    fallbacks=[CommandHandler("cancel", cancel_profile)],
)

# Commands
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("next", next_cmd))
app.add_handler(CommandHandler("end", end_cmd))
app.add_handler(CommandHandler("stop", stop_cmd))
app.add_handler(CommandHandler("referral", referral_cmd))
app.add_handler(profile_conv)

app.add_handler(CallbackQueryHandler(btn_next, pattern="^next$"))
app.add_handler(CallbackQueryHandler(btn_end, pattern="^end$"))
app.add_handler(CallbackQueryHandler(btn_ref, pattern="^ref$"))

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        forward_message
    )
)

print("Bot running...")
app.run_polling()
