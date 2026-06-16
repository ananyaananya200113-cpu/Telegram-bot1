import os
import sqlite3
import time
import datetime
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
# Helper to dynamically add columns for profiles if they don't exist
def add_column_if_not_exists(table, column, definition):
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()
    except sqlite3.OperationalError:
        pass
# Setup database columns for new profile fields
add_column_if_not_exists("users", "gender", "TEXT DEFAULT 'Not set'")
add_column_if_not_exists("users", "age", "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "total_dialogs", "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "today_dialogs", "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "last_dialog_date", "TEXT DEFAULT ''")
add_column_if_not_exists("users", "sent_messages", "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "received_messages", "INTEGER DEFAULT 0")
# Clear stale queue entries on startup (avoids mismatches from
# previous/duplicate bot instances)
cur.execute("DELETE FROM queue")
conn.commit()
# ---------------- STATE MACHINE ---------------- #
user_states = {}  # In-memory dictionary to track current state of users (e.g. 'awaiting_age')
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
def get_user_profile(uid):
    cur.execute("""
        SELECT user_id, gender, age, total_dialogs, today_dialogs, last_dialog_date, sent_messages, received_messages 
        FROM users WHERE user_id=?
    """, (uid,))
    return cur.fetchone()
def update_profile_gender(uid, gender):
    cur.execute("UPDATE users SET gender=? WHERE user_id=?", (gender, uid))
    conn.commit()
def update_profile_age(uid, age):
    cur.execute("UPDATE users SET age=? WHERE user_id=?", (age, uid))
    conn.commit()
def increment_dialogs(uid):
    today_str = datetime.date.today().isoformat()
    profile = get_user_profile(uid)
    if not profile:
        create_user(uid)
        profile = get_user_profile(uid)
    
    # Column mapping:
    # 0: user_id, 1: gender, 2: age, 3: total_dialogs, 4: today_dialogs, 5: last_dialog_date
    total = profile[3] + 1
    last_date = profile[5]
    if last_date != today_str:
        today = 1
    else:
        today = profile[4] + 1
        
    cur.execute("""
        UPDATE users 
        SET total_dialogs=?, today_dialogs=?, last_dialog_date=? 
        WHERE user_id=?
    """, (total, today, today_str, uid))
    conn.commit()
# ---------------- UI ---------------- #
def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Next", callback_data="next"),
         InlineKeyboardButton("❌ End", callback_data="end")],
        [InlineKeyboardButton("👤 Profile", callback_data="profile"),
         InlineKeyboardButton("💰 Referral", callback_data="ref")]
    ])
def profile_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👫 Set Gender", callback_data="set_gender"),
         InlineKeyboardButton("🔞 Set Age", callback_data="set_age")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_menu")]
    ])
def gender_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👨 Male", callback_data="gender_M"),
         InlineKeyboardButton("👩 Female", callback_data="gender_F")],
        [InlineKeyboardButton("🔙 Back to Profile", callback_data="profile")]
    ])
def back_only_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Profile", callback_data="profile")]
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
    # First, handle skipping the current chat if the user is in one
    chat = in_chat(uid)
    if chat:
        u1, u2 = chat
        partner = u2 if uid == u1 else u1
        end_chat(uid)
        
        try:
            await context.bot.send_message(partner, "🤚 Your partner left the chat", reply_markup=menu())
        except Exception as e:
            print(f"ERROR: failed to notify partner on skip: {e}")
            
        await send("🤚 You left the chat")
    # Queue up the user and notify
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
    # Increment dialog counts for both users
    increment_dialogs(uid)
    increment_dialogs(partner)
    # Success messages
    match_text = (
        "🎉 Match successful!\n\n"
        "/next – End and start a new match\n"
        "/stop – End the current chat"
    )
    await context.bot.send_message(uid, match_text, reply_markup=menu())
    await context.bot.send_message(partner, match_text, reply_markup=menu())
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
# ---------------- BUTTONS ---------------- #
async def btn_next(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    user_states.pop(uid, None) # Cancel profile editing state if any
    await next_user(uid, context, q.message.reply_text)
async def btn_end(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    user_states.pop(uid, None) # Cancel profile editing state if any
    await end_chat_flow(uid, context, q.message.reply_text)
async def btn_ref(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    user_states.pop(uid, None) # Cancel profile editing state if any
    await referral(uid, q.message.reply_text)
# ---------------- PROFILE SYSTEM & BUTTONS ---------------- #
async def show_profile(uid, send_or_edit, reply_markup=None):
    profile = get_user_profile(uid)
    if not profile:
        create_user(uid)
        profile = get_user_profile(uid)
        
    _, gender, age, total_dialogs, today_dialogs, last_dialog_date, sent_messages, received_messages = profile
    
    # Check if today's dialog count is stale
    today_str = datetime.date.today().isoformat()
    if last_dialog_date != today_str:
        today_dialogs = 0
        
    gender_display = gender if gender else "Not set"
    age_display = str(age) if (age and age > 0) else "Not set"
    
    profile_text = (
        f"#️⃣ ID — {uid}\n\n"
        f"👫 Gender — {gender_display}\n"
        f"🔞 Age — {age_display}\n\n"
        f"⭐️ Dialogs\n"
        f"├ Total: {total_dialogs}\n"
        f"└ Today: {today_dialogs}\n\n"
        f"✉️ Messages\n"
        f"├ Sent: {sent_messages}\n"
        f"└ Received: {received_messages}"
    )
    
    await send_or_edit(profile_text, reply_markup=reply_markup or profile_menu())
async def btn_profile(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    user_states.pop(uid, None) # Clear any states
    
    async def edit_msg(text, reply_markup):
        await q.message.edit_text(text, reply_markup=reply_markup)
        
    await show_profile(uid, edit_msg)
async def btn_set_gender(update, context):
    q = update.callback_query
    await q.answer()
    await q.message.edit_text("Select your gender:", reply_markup=gender_menu())
async def btn_gender_select(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    gender_code = q.data.split("_")[1] # 'M' or 'F'
    gender_str = "M" if gender_code == "M" else "F"
    update_profile_gender(uid, gender_str)
    
    async def edit_msg(text, reply_markup):
        await q.message.edit_text(f"✅ Gender set to {gender_str}!\n\n" + text, reply_markup=reply_markup)
        
    await show_profile(uid, edit_msg)
async def btn_set_age(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    user_states[uid] = "awaiting_age"
    await q.message.edit_text("🔢 Please send your age (a number between 1 and 99):", reply_markup=back_only_menu())
async def btn_back_to_menu(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    user_states.pop(uid, None)
    await q.message.edit_text("Welcome 👋", reply_markup=menu())
# ---------------- COMMANDS ---------------- #
async def next_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_states.pop(uid, None)
    await next_user(uid, context, update.message.reply_text)
async def end_chat_flow(uid, context, send):
    chat = in_chat(uid)
    end_chat(uid)
    remove_queue(uid)
    if chat:
        u1, u2 = chat
        partner = u2 if uid == u1 else u1
        try:
            await context.bot.send_message(partner, "🤚 Your partner left the chat", reply_markup=menu())
        except Exception as e:
            print(f"ERROR: failed to notify partner on leave: {e}")
        await send("🤚 You left the chat")
    else:
        await send("You are not in an active chat.")
async def end_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_states.pop(uid, None)
    await end_chat_flow(uid, context, update.message.reply_text)
async def referral_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_states.pop(uid, None)
    await referral(uid, update.message.reply_text)
async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_states.pop(uid, None)
    
    async def reply_msg(text, reply_markup):
        await update.message.reply_text(text, reply_markup=reply_markup)
        
    await show_profile(uid, reply_msg)
# ---------------- MESSAGE HANDLER (FORWARDING & STATE CAPTURE) ---------------- #
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    
    # Check if we are awaiting profile input
    if user_states.get(uid) == "awaiting_age":
        try:
            age = int(text)
            if 1 <= age <= 99:
                update_profile_age(uid, age)
                user_states.pop(uid, None) # Clear state
                
                async def reply_msg(msg_text, reply_markup):
                    await update.message.reply_text(f"✅ Age set to {age}!\n\n" + msg_text, reply_markup=reply_markup)
                    
                await show_profile(uid, reply_msg)
                return
        except ValueError:
            pass
            
        await update.message.reply_text(
            "⚠️ Invalid age. Please enter a valid number (e.g. 24) or use the 'Back to Profile' button to cancel:",
            reply_markup=back_only_menu()
        )
        return
        
    # Default to message forwarding
    await forward_message(update, context)
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
        
        # Increment message counters
        cur.execute("UPDATE users SET sent_messages = sent_messages + 1 WHERE user_id=?", (uid,))
        cur.execute("UPDATE users SET received_messages = received_messages + 1 WHERE user_id=?", (partner,))
        conn.commit()
        
        print(f"DEBUG: forwarded message from {uid} to {partner}")
    except Exception as e:
        print(f"ERROR: failed to forward message from {uid} to {partner}: {e}")
        await update.message.reply_text(
            "⚠️ Could not deliver your message. Your partner may have blocked the bot or left."
        )
        end_chat(uid)
# ---------------- APP ---------------- #
app = Application.builder().token(TOKEN).build()
# Commands
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("next", next_cmd))
app.add_handler(CommandHandler("end", end_cmd))
app.add_handler(CommandHandler("stop", end_cmd)) # Support /stop to end chat
app.add_handler(CommandHandler("referral", referral_cmd))
app.add_handler(CommandHandler("profile", profile_cmd))
# Callback Queries
app.add_handler(CallbackQueryHandler(btn_next, pattern="^next$"))
app.add_handler(CallbackQueryHandler(btn_end, pattern="^end$"))
app.add_handler(CallbackQueryHandler(btn_ref, pattern="^ref$"))
app.add_handler(CallbackQueryHandler(btn_profile, pattern="^profile$"))
app.add_handler(CallbackQueryHandler(btn_set_gender, pattern="^set_gender$"))
app.add_handler(CallbackQueryHandler(btn_gender_select, pattern="^gender_(M|F)$"))
app.add_handler(CallbackQueryHandler(btn_set_age, pattern="^set_age$"))
app.add_handler(CallbackQueryHandler(btn_back_to_menu, pattern="^back_to_menu$"))
# Message Handlers
app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_message
    )
)
if __name__ == "__main__":
    print("Bot running...")
    app.run_polling()
