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

# ---------------- ADMIN CONFIG ---------------- #
ADMIN_IDS = {
    8550879731,  # Admin 1
    8637459083,  # Admin 2
    8946438351   # Admin 3
}

# ---------------- CHANNEL CONFIG ---------------- #
CHANNEL_USERNAME = "@AnnAnonymousChatbot"       # ← Change to your channel username
CHANNEL_ID       = "@AnnAnonymousChatbot"       # ← Same or use numeric ID like -1001234567890
FREE_CHAT_LIMIT  = 3                    # Chats allowed before join required

# ---------------- PREMIUM PLANS ---------------- #
PLANS = {
    "lite":    {"label": "💎 Premium Lite",    "price": 15,  "days": 1,  "hours": 24},
    "weekly":  {"label": "💎 Premium Weekly",  "price": 59,  "days": 7,  "hours": 168},
    "plus":    {"label": "💎 Premium Plus",    "price": 99,  "days": 15, "hours": 360},
    "monthly": {"label": "💎 Premium Monthly", "price": 179, "days": 30, "hours": 720},
}

# UPI / Payment info shown to users
PAYMENT_UPI = "yourname@upi"           # ← Change to your UPI ID
PAYMENT_NOTE = "Send payment screenshot to @CEOANNCHATTINGBOT after paying."  # ← Change

# ---------------- DATABASE ---------------- #
conn = sqlite3.connect("anon_chat.db", check_same_thread=False)
cur  = conn.cursor()

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

def add_column_if_not_exists(table, column, definition):
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.commit()
    except sqlite3.OperationalError:
        pass

add_column_if_not_exists("users", "gender",             "TEXT DEFAULT 'Not set'")
add_column_if_not_exists("users", "age",                "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "total_dialogs",      "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "today_dialogs",      "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "last_dialog_date",   "TEXT DEFAULT ''")
add_column_if_not_exists("users", "sent_messages",      "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "received_messages",  "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "premium_preference", "TEXT DEFAULT 'Any'")
# Premium expiry: Unix timestamp when premium expires (0 = not premium)
add_column_if_not_exists("users", "premium_expiry",     "INTEGER DEFAULT 0")
# Free chat counter for non-channel-members
add_column_if_not_exists("users", "free_chats_used",    "INTEGER DEFAULT 0")
# Whether user has joined channel (1 = verified)
add_column_if_not_exists("users", "channel_joined",     "INTEGER DEFAULT 0")

# Clear stale queue on startup
cur.execute("DELETE FROM queue")
conn.commit()

# ---------------- STATE MACHINE ---------------- #
user_states = {}

# ---------------- HELPERS ---------------- #
def create_user(uid):
    cur.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
    conn.commit()

def is_premium(uid):
    """Returns True if user has active premium (paid or referral)."""
    cur.execute("SELECT premium, premium_expiry FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()
    if not r:
        return False
    prem, expiry = r
    if prem == 1:
        # Check if time-limited premium has expired
        if expiry > 0 and int(time.time()) > expiry:
            # Expire it
            cur.execute("UPDATE users SET premium=0, premium_expiry=0 WHERE user_id=?", (uid,))
            conn.commit()
            return False
        return True
    return False

def get_premium_expiry_str(uid):
    cur.execute("SELECT premium_expiry FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()
    if not r or r[0] == 0:
        return "Lifetime"
    remaining = r[0] - int(time.time())
    if remaining <= 0:
        return "Expired"
    hours   = remaining // 3600
    minutes = (remaining % 3600) // 60
    if hours >= 24:
        days = hours // 24
        return f"{days}d {hours % 24}h remaining"
    return f"{hours}h {minutes}m remaining"

def set_premium_timed(uid, hours):
    """Grant premium for a specific number of hours."""
    expiry = int(time.time()) + (hours * 3600)
    cur.execute("UPDATE users SET premium=1, premium_expiry=? WHERE user_id=?", (expiry, uid))
    conn.commit()

def set_premium_permanent(uid):
    """Grant permanent premium (expiry=0 means no expiry)."""
    cur.execute("UPDATE users SET premium=1, premium_expiry=0 WHERE user_id=?", (uid,))
    conn.commit()

def get_ref(uid):
    cur.execute("SELECT referral_count FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()
    return r[0] if r else 0

def add_ref(uid):
    count = get_ref(uid) + 1
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
        SELECT user_id, gender, age, total_dialogs, today_dialogs, last_dialog_date,
               sent_messages, received_messages, premium_preference,
               premium_expiry, free_chats_used, channel_joined
        FROM users WHERE user_id=?
    """, (uid,))
    return cur.fetchone()

def update_profile_gender(uid, gender):
    cur.execute("UPDATE users SET gender=? WHERE user_id=?", (gender, uid))
    conn.commit()

def update_profile_age(uid, age):
    cur.execute("UPDATE users SET age=? WHERE user_id=?", (age, uid))
    conn.commit()

def update_premium_preference(uid, pref):
    cur.execute("UPDATE users SET premium_preference=? WHERE user_id=?", (pref, uid))
    conn.commit()

def increment_dialogs(uid):
    today_str = datetime.date.today().isoformat()
    profile   = get_user_profile(uid)
    if not profile:
        create_user(uid)
        profile = get_user_profile(uid)
    total     = profile[3] + 1
    last_date = profile[5]
    today     = 1 if last_date != today_str else profile[4] + 1
    cur.execute("""
        UPDATE users SET total_dialogs=?, today_dialogs=?, last_dialog_date=?
        WHERE user_id=?
    """, (total, today, today_str, uid))
    conn.commit()

def get_free_chats_used(uid):
    cur.execute("SELECT free_chats_used FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()
    return r[0] if r else 0

def increment_free_chats(uid):
    cur.execute("UPDATE users SET free_chats_used = free_chats_used + 1 WHERE user_id=?", (uid,))
    conn.commit()

def has_joined_channel(uid):
    cur.execute("SELECT channel_joined FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()
    return r and r[0] == 1

def set_channel_joined(uid):
    cur.execute("UPDATE users SET channel_joined=1 WHERE user_id=?", (uid,))
    conn.commit()

async def verify_channel_membership(bot, uid):
    """Check Telegram API if user is actually in the channel."""
    try:
        member = await bot.get_chat_member(CHANNEL_ID, uid)
        if member.status in ("member", "administrator", "creator"):
            set_channel_joined(uid)
            return True
    except Exception as e:
        print(f"ERROR checking channel membership for {uid}: {e}")
    return False

async def check_channel_gate(bot, uid):
    """
    Returns True if user can chat (is premium, already joined, or has free chats left).
    Returns False if user must join channel first.
    """
    if is_premium(uid):
        return True
    if has_joined_channel(uid):
        return True
    # Re-verify via API in case they joined but we didn't record it
    if await verify_channel_membership(bot, uid):
        return True
    used = get_free_chats_used(uid)
    if used < FREE_CHAT_LIMIT:
        return True
    return False

# ---------------- ADMIN HELPERS ---------------- #
def is_admin(uid):
    return uid in ADMIN_IDS

def get_all_users(limit=10, offset=0):
    cur.execute("""
        SELECT user_id, gender, age, total_dialogs, today_dialogs,
               sent_messages, received_messages, referral_count, premium, last_dialog_date
        FROM users ORDER BY total_dialogs DESC LIMIT ? OFFSET ?
    """, (limit, offset))
    return cur.fetchall()

def get_user_count():
    cur.execute("SELECT COUNT(*) FROM users")
    return cur.fetchone()[0]

def get_active_chat_count():
    cur.execute("SELECT COUNT(*) FROM active_chats")
    return cur.fetchone()[0]

def get_queue_count():
    cur.execute("SELECT COUNT(*) FROM queue")
    return cur.fetchone()[0]

def get_vip_count():
    now = int(time.time())
    cur.execute("SELECT COUNT(*) FROM users WHERE premium=1 AND (premium_expiry=0 OR premium_expiry>?)", (now,))
    return cur.fetchone()[0]

def get_active_today_count():
    today_str = datetime.date.today().isoformat()
    cur.execute("SELECT COUNT(*) FROM users WHERE last_dialog_date=?", (today_str,))
    return cur.fetchone()[0]

def get_total_messages():
    cur.execute("SELECT SUM(sent_messages) FROM users")
    result = cur.fetchone()[0]
    return result or 0

# ---------------- UI MENUS ---------------- #
def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Next", callback_data="next"),
         InlineKeyboardButton("❌ End",  callback_data="end")],
        [InlineKeyboardButton("👤 Profile",  callback_data="profile"),
         InlineKeyboardButton("💰 Referral", callback_data="ref")],
        [InlineKeyboardButton("⭐ Premium",  callback_data="premium_menu")]
    ])

def profile_menu(is_vip=False):
    buttons = [
        [InlineKeyboardButton("👫 Set Gender", callback_data="set_gender"),
         InlineKeyboardButton("🔞 Set Age",    callback_data="set_age")]
    ]
    if is_vip:
        buttons.append([InlineKeyboardButton("🎯 Set Preference", callback_data="set_pref")])
    buttons.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(buttons)

def gender_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👨 Male",   callback_data="gender_M"),
         InlineKeyboardButton("👩 Female", callback_data="gender_F")],
        [InlineKeyboardButton("🔙 Back to Profile", callback_data="profile")]
    ])

def preference_selection_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👨 Male",   callback_data="setpref_M"),
         InlineKeyboardButton("👩 Female", callback_data="setpref_F")],
        [InlineKeyboardButton("🟢 Any",    callback_data="setpref_Any")],
        [InlineKeyboardButton("🔙 Back to Profile", callback_data="profile")]
    ])

def back_only_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Profile", callback_data="profile")]
    ])

def premium_plans_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Lite — ₹15 / 1 Day",     callback_data="buy_lite")],
        [InlineKeyboardButton("💎 Weekly — ₹59 / 7 Days",  callback_data="buy_weekly")],
        [InlineKeyboardButton("💎 Plus — ₹99 / 15 Days",   callback_data="buy_plus")],
        [InlineKeyboardButton("💎 Monthly — ₹179 / 30 Days", callback_data="buy_monthly")],
        [InlineKeyboardButton("👥 Refer & Earn Premium",    callback_data="ref")],
        [InlineKeyboardButton("🔙 Back to Main Menu",       callback_data="back_to_menu")]
    ])

def channel_join_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")],
        [InlineKeyboardButton("✅ I've Joined",  callback_data="check_joined")]
    ])

# ---------------- VIP CHECK (REFERRAL) ---------------- #
async def check_vip_referral(bot, uid):
    """Each referral gives 6 hours of premium. Stack on existing expiry."""
    add_ref(uid)
    # Extend existing expiry or start from now
    cur.execute("SELECT premium, premium_expiry FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()
    now = int(time.time())
    if r and r[0] == 1 and r[1] > now:
        new_expiry = r[1] + (6 * 3600)   # Stack 6h on top
    else:
        new_expiry = now + (6 * 3600)

    cur.execute("UPDATE users SET premium=1, premium_expiry=? WHERE user_id=?", (new_expiry, uid))
    conn.commit()

    dt = datetime.datetime.fromtimestamp(new_expiry).strftime("%d %b %Y %I:%M %p")
    await bot.send_message(
        uid,
        f"🎁 *Referral Premium Activated!*\n\n"
        f"You earned *6 Hours Premium* for your referral!\n"
        f"⏳ Expires: {dt}\n\n"
        f"Enjoy gender preference matching & priority queue!",
        parse_mode="Markdown",
        reply_markup=preference_selection_menu()
    )

async def ask_preference(bot, uid):
    try:
        await bot.send_message(
            uid,
            "💎 *VIP Preference Setup*\n\n"
            "Select your preferred gender for matching:",
            reply_markup=preference_selection_menu(),
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"ERROR: preference prompt failed for {uid}: {e}")

# ---------------- START ---------------- #
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    create_user(uid)
    if context.args:
        try:
            ref_id = int(context.args[0])
            if ref_id != uid:
                await check_vip_referral(context.bot, ref_id)
        except:
            pass
    await update.message.reply_text("Welcome 👋", reply_markup=menu())

# ---------------- CHANNEL GATE ---------------- #
async def send_channel_gate(send_fn):
    await send_fn(
        "🔒 *Free Chat Limit Reached!*\n\n"
        "To continue chatting, please join our official channel.\n\n"
        f"📢 Join Channel: @AnnAnonymousChatbot\n\n"
        "After joining, click the button below to continue chatting.\n\n"
        "🎁 Channel members get uninterrupted access to Ann Chat.",
        parse_mode="Markdown",
        reply_markup=channel_join_menu()
    )

# ---------------- MATCHING ---------------- #
async def next_user(uid, context, send):
    # Channel gate check
    can_chat = await check_channel_gate(context.bot, uid)
    if not can_chat:
        async def gate_send(text, parse_mode=None, reply_markup=None):
            await send(text, parse_mode=parse_mode, reply_markup=reply_markup) \
                if callable(send) else None
        await send_channel_gate(send)
        return

    chat = in_chat(uid)
    if chat:
        u1, u2 = chat
        partner = u2 if uid == u1 else u1
        end_chat(uid)
        try:
            await context.bot.send_message(partner, "🤚 Your partner left the chat", reply_markup=menu())
        except Exception as e:
            print(f"ERROR: notify partner on skip: {e}")
        await send("🤚 You left the chat")

    # Fetch user details for scoring
    cur.execute("SELECT gender, premium, premium_preference FROM users WHERE user_id=?", (uid,))
    row_a     = cur.fetchone()
    gender_a  = row_a[0] if row_a else 'Not set'
    premium_a = row_a[1] if row_a else 0
    pref_a    = row_a[2] if row_a else 'Any'

    cur.execute("""
        SELECT q.user_id, u.gender, u.premium, u.premium_preference, q.timestamp
        FROM queue q JOIN users u ON q.user_id = u.user_id
        WHERE q.user_id != ?
    """, (uid,))
    candidates = cur.fetchall()

    if not candidates:
        add_queue(uid)
        await send("Searching...")
        return

    scored = []
    for cid, c_gender, c_premium, c_pref, c_ts in candidates:
        a_score = (2 if c_gender == pref_a else 0) if (premium_a == 1 and pref_a in ('M','F')) else 1
        b_score = (2 if gender_a == c_pref else 0) if (c_premium == 1 and c_pref in ('M','F')) else 1
        scored.append((a_score + b_score, c_ts, cid))

    scored.sort(key=lambda x: (-x[0], x[1]))
    partner = scored[0][2]

    remove_queue(uid)
    remove_queue(partner)
    cur.execute("INSERT INTO active_chats VALUES (?,?)", (uid, partner))
    conn.commit()

    increment_dialogs(uid)
    increment_dialogs(partner)

    # Increment free chat counter for non-members
    if not has_joined_channel(uid) and not is_premium(uid):
        increment_free_chats(uid)
    if not has_joined_channel(partner) and not is_premium(partner):
        increment_free_chats(partner)

    match_text = (
        "🎉 Match successful!\n\n"
        "/next – End and start a new match\n"
        "/stop – End the current chat"
    )
    await context.bot.send_message(uid, match_text, reply_markup=menu())
    await context.bot.send_message(partner, match_text, reply_markup=menu())

# ---------------- REFERRAL ---------------- #
async def referral(uid, send):
    ref  = get_ref(uid)
    prem = is_premium(uid)
    link = f"https://t.me/Annchattingbot?start={uid}"
    expiry_str = get_premium_expiry_str(uid) if prem else "—"
    await send(
        f"💰 *Referral System*\n\n"
        f"👥 Referrals: `{ref}`\n"
        f"⭐ Status: {'💎 Premium' if prem else '👤 FREE'}\n"
        f"⏳ Expiry: {expiry_str}\n\n"
        f"🎁 Every referral = *6 Hours Premium*!\n"
        f"Referrals stack — invite more, get more time!\n\n"
        f"Your link:\n`{link}`",
        parse_mode="Markdown"
    )

# ---------------- PREMIUM MENU HANDLER ---------------- #
async def show_premium_menu(uid, send_fn):
    prem = is_premium(uid)
    expiry_str = get_premium_expiry_str(uid) if prem else None

    status_line = f"✅ *Active Premium* — {expiry_str}" if prem else "❌ No Active Premium"

    text = (
        f"⭐️ *ANN PREMIUM PLANS*\n\n"
        f"{status_line}\n\n"
        f"────────────────────\n\n"
        f"🎁 *Referral Premium*\n"
        f"• 6 Hours Premium per successful referral\n"
        f"• Gender Preference Matching\n"
        f"• Unlimited Skips\n"
        f"• Priority Matching\n\n"
        f"────────────────────\n\n"
        f"💎 *Premium Lite* — ₹15\n"
        f"⏳ Duration: 1 Day\n\n"
        f"💎 *Premium Weekly* — ₹59\n"
        f"⏳ Duration: 7 Days\n\n"
        f"💎 *Premium Plus* — ₹99\n"
        f"⏳ Duration: 15 Days\n\n"
        f"💎 *Premium Monthly* — ₹179\n"
        f"⏳ Duration: 30 Days\n\n"
        f"────────────────────\n\n"
        f"🎯 *Premium Features*\n\n"
        f"✅ Gender Preference Matching\n"
        f"✅ Unlimited Partner Skips\n"
        f"✅ Faster Matchmaking\n"
        f"✅ Priority Queue Access\n"
        f"✅ Premium Badge\n"
        f"✅ Exclusive Premium Features\n\n"
        f"────────────────────\n\n"
        f"👥 *Refer & Earn*\n\n"
        f"Invite friends and earn 🎁 6 Hours Premium per referral!"
    )
    await send_fn(text, parse_mode="Markdown", reply_markup=premium_plans_menu())

async def show_plan_payment(uid, plan_key, send_fn):
    plan = PLANS[plan_key]
    text = (
        f"{plan['label']}\n\n"
        f"💰 Price: ₹{plan['price']}\n"
        f"⏳ Duration: {plan['days']} Day{'s' if plan['days'] > 1 else ''}\n\n"
        f"────────────────────\n\n"
        f"📲 *How to Pay:*\n\n"
        f"1️⃣ Send ₹{plan['price']} to:\n"
        f"`{PAYMENT_UPI}`\n\n"
        f"2️⃣ {PAYMENT_NOTE}\n\n"
        f"3️⃣ Admin will activate your premium within minutes.\n\n"
        f"────────────────────\n\n"
        f"🎯 *Benefits:*\n"
        f"✅ Gender Preference Matching\n"
        f"✅ Unlimited Skips\n"
        f"✅ Priority Matching\n"
        + (f"✅ Premium Badge\n" if plan['days'] >= 7 else "")
        + (f"✅ Faster Match Priority\n" if plan['days'] >= 15 else "")
        + (f"✅ Highest Queue Priority\n" if plan['days'] >= 30 else "")
    )
    back_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Plans", callback_data="premium_menu")]
    ])
    await send_fn(text, parse_mode="Markdown", reply_markup=back_btn)

# ---------------- BUTTONS ---------------- #
async def btn_next(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    user_states.pop(uid, None)
    await next_user(uid, context, q.message.reply_text)

async def btn_end(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    user_states.pop(uid, None)
    await end_chat_flow(uid, context, q.message.reply_text)

async def btn_ref(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    user_states.pop(uid, None)
    await referral(uid, q.message.reply_text)

async def btn_premium_menu(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    user_states.pop(uid, None)
    async def send_fn(text, parse_mode=None, reply_markup=None):
        await q.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    await show_premium_menu(uid, send_fn)

async def btn_buy_plan(update, context):
    q = update.callback_query
    await q.answer()
    uid      = q.from_user.id
    plan_key = q.data.split("buy_")[1]   # lite / weekly / plus / monthly
    async def send_fn(text, parse_mode=None, reply_markup=None):
        await q.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    await show_plan_payment(uid, plan_key, send_fn)

async def btn_check_joined(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    joined = await verify_channel_membership(context.bot, uid)
    if joined:
        await q.message.edit_text(
            "✅ *Channel membership verified!*\n\nYou now have unlimited access to Ann Chat. Enjoy! 🎉",
            parse_mode="Markdown",
            reply_markup=menu()
        )
    else:
        await q.answer("❌ You haven't joined yet. Please join and try again.", show_alert=True)

# ---------------- PROFILE SYSTEM & BUTTONS ---------------- #
async def show_profile(uid, send_or_edit, reply_markup=None):
    profile = get_user_profile(uid)
    if not profile:
        create_user(uid)
        profile = get_user_profile(uid)

    _, gender, age, total_dialogs, today_dialogs, last_dialog_date, \
        sent_messages, received_messages, pref, premium_expiry, free_chats_used, channel_joined = profile

    today_str = datetime.date.today().isoformat()
    if last_dialog_date != today_str:
        today_dialogs = 0

    gender_display = gender if gender else "Not set"
    age_display    = str(age) if (age and age > 0) else "Not set"
    is_vip         = is_premium(uid)
    pref_display   = pref if is_vip else "Locked 🔒"
    expiry_str     = get_premium_expiry_str(uid) if is_vip else "—"

    profile_text = (
        f"#️⃣ ID — `{uid}`\n\n"
        f"👫 Gender — {gender_display}\n"
        f"🔞 Age — {age_display}\n"
        f"🎯 Preference — {pref_display}\n"
        f"⭐ Premium — {'💎 Active' if is_vip else '❌ None'}\n"
        f"⏳ Expiry — {expiry_str}\n\n"
        f"⭐️ Dialogs\n"
        f"├ Total: {total_dialogs}\n"
        f"└ Today: {today_dialogs}\n\n"
        f"✉️ Messages\n"
        f"├ Sent: {sent_messages}\n"
        f"└ Received: {received_messages}"
    )
    await send_or_edit(profile_text, reply_markup=reply_markup or profile_menu(is_vip=is_vip))

async def btn_profile(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    user_states.pop(uid, None)
    async def edit_msg(text, reply_markup):
        await q.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    await show_profile(uid, edit_msg)

async def btn_set_gender(update, context):
    q = update.callback_query
    await q.answer()
    await q.message.edit_text("Select your gender:", reply_markup=gender_menu())

async def btn_gender_select(update, context):
    q = update.callback_query
    await q.answer()
    uid        = q.from_user.id
    gender_str = "M" if q.data.split("_")[1] == "M" else "F"
    update_profile_gender(uid, gender_str)
    async def edit_msg(text, reply_markup):
        await q.message.edit_text(f"✅ Gender set to {gender_str}!\n\n" + text,
                                  reply_markup=reply_markup, parse_mode="Markdown")
    await show_profile(uid, edit_msg)

async def btn_set_age(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    user_states[uid] = "awaiting_age"
    await q.message.edit_text(
        "🔢 Please send your age (a number between 1 and 99):",
        reply_markup=back_only_menu()
    )

async def btn_set_preference(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not is_premium(uid):
        await q.message.reply_text(
            "🔒 Gender preference is a *Premium* feature!\n\n"
            "Tap ⭐ Premium to upgrade or refer friends to earn free premium.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⭐ View Premium Plans", callback_data="premium_menu")]
            ])
        )
        return
    await q.message.edit_text(
        "Select your preferred gender for matching:",
        reply_markup=preference_selection_menu()
    )

async def btn_preference_select(update, context):
    q = update.callback_query
    await q.answer()
    uid       = q.from_user.id
    pref_code = q.data.split("pref_")[1]
    update_premium_preference(uid, pref_code)
    async def edit_msg(text, reply_markup):
        await q.message.edit_text(
            f"✅ Preference set to {pref_code}!\n\n" + text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    await show_profile(uid, edit_msg)

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
            print(f"ERROR: notify partner on leave: {e}")
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
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    await show_profile(uid, reply_msg)

async def premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_states.pop(uid, None)
    async def send_fn(text, parse_mode=None, reply_markup=None):
        await update.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    await show_premium_menu(uid, send_fn)

# ---------------- ADMIN COMMANDS ---------------- #
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    total        = get_user_count()
    vip          = get_vip_count()
    active_today = get_active_today_count()
    active_chats = get_active_chat_count()
    in_queue     = get_queue_count()
    total_msgs   = get_total_messages()
    await update.message.reply_text(
        f"📊 *Bot Statistics*\n\n"
        f"👥 Total Users: `{total}`\n"
        f"💎 Active Premium: `{vip}`\n"
        f"🟢 Active Today: `{active_today}`\n"
        f"💬 Active Chats: `{active_chats}`\n"
        f"🔎 In Queue: `{in_queue}`\n"
        f"✉️ Total Messages: `{total_msgs}`",
        parse_mode="Markdown"
    )

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    page = 1
    if context.args:
        try:
            page = max(1, int(context.args[0]))
        except ValueError:
            pass
    page_size   = 10
    offset      = (page - 1) * page_size
    users       = get_all_users(limit=page_size, offset=offset)
    total       = get_user_count()
    total_pages = max(1, (total + page_size - 1) // page_size)

    await update.message.reply_text(
        f"📊 *Bot Summary*\n"
        f"👥 Total: `{total}` | 💬 Chats: `{get_active_chat_count()}` | 🔎 Queue: `{get_queue_count()}`\n"
        f"📄 Page `{page}` / `{total_pages}`",
        parse_mode="Markdown"
    )
    if not users:
        await update.message.reply_text("No users found.")
        return

    lines = []
    for u in users:
        user_id, gender, age, total_d, today_d, sent, received, refs, prem, last_date = u
        status      = "💎" if prem else "👤"
        gender_icon = "👨" if gender == "M" else "👩" if gender == "F" else "❓"
        age_display = str(age) if age and age > 0 else "N/A"
        lines.append(
            f"{status} `{user_id}`\n"
            f"  {gender_icon} {gender or 'N/A'} | 🔞 {age_display} | 👥 Refs: {refs}\n"
            f"  💬 Dialogs: {total_d} (Today: {today_d})\n"
            f"  ✉️ Sent: {sent} | Rcvd: {received}\n"
            f"  📅 Last: {last_date or 'Never'}"
        )
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")
    if total_pages > 1:
        next_hint = f"Use `/users {page+1}` for next" if page < total_pages else f"Use `/users {page-1}` to go back"
        await update.message.reply_text(f"📄 Page {page}/{total_pages} — {next_hint}", parse_mode="Markdown")

async def admin_find_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: `/finduser <user_id>`", parse_mode="Markdown")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Invalid user ID.")
        return

    profile = get_user_profile(target_id)
    if not profile:
        await update.message.reply_text("❌ User not found.")
        return

    _, gender, age, total_d, today_d, last_date, sent, received, pref, premium_expiry, free_used, ch_joined = profile
    refs  = get_ref(target_id)
    prem  = is_premium(target_id)
    in_c  = bool(in_chat(target_id))
    in_q  = bool(cur.execute("SELECT 1 FROM queue WHERE user_id=?", (target_id,)).fetchone())

    expiry_str = get_premium_expiry_str(target_id) if prem else "—"
    activity = "💬 In chat" if in_c else ("🔎 In queue" if in_q else "💤 Idle")

    await update.message.reply_text(
        f"🔍 *User Details*\n\n"
        f"🆔 ID: `{target_id}`\n"
        f"👫 Gender: {gender or 'Not set'}\n"
        f"🔞 Age: {age if age and age > 0 else 'Not set'}\n"
        f"🎯 Preference: {pref or 'Any'}\n"
        f"⭐ Premium: {'💎 Active' if prem else '❌ None'}\n"
        f"⏳ Expiry: {expiry_str}\n"
        f"📡 Activity: {activity}\n"
        f"📢 Channel joined: {'✅' if ch_joined else '❌'}\n"
        f"🆓 Free chats used: {free_used}/{FREE_CHAT_LIMIT}\n\n"
        f"💬 Total Dialogs: `{total_d}` | Today: `{today_d}`\n"
        f"✉️ Sent: `{sent}` | Rcvd: `{received}`\n"
        f"👥 Referrals: `{refs}`\n"
        f"📆 Last Active: {last_date or 'Never'}",
        parse_mode="Markdown"
    )

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: `/broadcast <message>`", parse_mode="Markdown")
        return
    message    = " ".join(context.args)
    cur.execute("SELECT user_id FROM users")
    all_users  = cur.fetchall()
    sent_count = 0
    fail_count = 0
    await update.message.reply_text(f"📢 Broadcasting to {len(all_users)} users...")
    for (user_id,) in all_users:
        try:
            await context.bot.send_message(user_id, f"📢 *Announcement*\n\n{message}", parse_mode="Markdown")
            sent_count += 1
        except Exception:
            fail_count += 1
    await update.message.reply_text(
        f"✅ Broadcast complete!\n📨 Sent: {sent_count}\n❌ Failed: {fail_count}"
    )

async def admin_setpremium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /setpremium <user_id> [days]
    days optional — omit for permanent, or specify e.g. /setpremium 123 7 for 7 days
    """
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: `/setpremium <user_id> [days]`", parse_mode="Markdown")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Invalid user ID.")
        return

    days = None
    if len(context.args) >= 2:
        try:
            days = int(context.args[1])
        except ValueError:
            pass

    profile = get_user_profile(target_id)
    if not profile:
        await update.message.reply_text("❌ User not found.")
        return

    if days:
        set_premium_timed(target_id, days * 24)
        label = f"{days} day(s)"
    else:
        set_premium_permanent(target_id)
        label = "Permanent"

    await update.message.reply_text(
        f"💎 User `{target_id}` granted *{label}* Premium!", parse_mode="Markdown"
    )
    try:
        await context.bot.send_message(
            target_id, f"💎 You have been granted *{label} Premium* by the admin!", parse_mode="Markdown"
        )
        await ask_preference(context.bot, target_id)
    except Exception:
        pass

async def admin_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /revoke <user_id> — Remove premium from a user
    """
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: `/revoke <user_id>`", parse_mode="Markdown")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Invalid user ID.")
        return
    cur.execute("UPDATE users SET premium=0, premium_expiry=0 WHERE user_id=?", (target_id,))
    conn.commit()
    await update.message.reply_text(f"✅ Premium revoked for user `{target_id}`.", parse_mode="Markdown")
    try:
        await context.bot.send_message(target_id, "ℹ️ Your premium has been revoked by an admin.")
    except Exception:
        pass

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    await update.message.reply_text(
        "🛠 *Admin Commands*\n\n"
        "`/stats` — Bot statistics\n"
        "`/users [page]` — List all users\n"
        "`/finduser <id>` — Lookup specific user\n"
        "`/setpremium <id> [days]` — Grant premium (omit days = permanent)\n"
        "`/revoke <id>` — Revoke premium\n"
        "`/broadcast <msg>` — Message all users\n"
        "`/adminhelp` — This help message",
        parse_mode="Markdown"
    )

# ---------------- MESSAGE HANDLER ---------------- #
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = update.message.text

    if user_states.get(uid) == "awaiting_age":
        try:
            age = int(text)
            if 1 <= age <= 99:
                update_profile_age(uid, age)
                user_states.pop(uid, None)
                async def reply_msg(msg_text, reply_markup):
                    await update.message.reply_text(
                        f"✅ Age set to {age}!\n\n" + msg_text,
                        reply_markup=reply_markup,
                        parse_mode="Markdown"
                    )
                await show_profile(uid, reply_msg)
                return
        except ValueError:
            pass
        await update.message.reply_text(
            "⚠️ Invalid age. Enter a number (1–99) or tap Back to Profile:",
            reply_markup=back_only_menu()
        )
        return

    await forward_message(update, context)

# ---------------- MESSAGE FORWARDING ---------------- #
async def forward_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    chat = in_chat(uid)
    if not chat:
        await update.message.reply_text(
            "You are not connected to anyone. Press 🔎 Next to find a partner.",
            reply_markup=menu()
        )
        return
    u1, u2  = chat
    partner = u2 if uid == u1 else u1
    try:
        await context.bot.send_message(chat_id=partner, text=update.message.text)
        cur.execute("UPDATE users SET sent_messages = sent_messages + 1 WHERE user_id=?", (uid,))
        cur.execute("UPDATE users SET received_messages = received_messages + 1 WHERE user_id=?", (partner,))
        conn.commit()
    except Exception as e:
        print(f"ERROR: forward from {uid} to {partner}: {e}")
        await update.message.reply_text(
            "⚠️ Could not deliver your message. Your partner may have left."
        )
        end_chat(uid)

# ---------------- APP ---------------- #
app = Application.builder().token(TOKEN).build()

# User commands
app.add_handler(CommandHandler("start",    start))
app.add_handler(CommandHandler("next",     next_cmd))
app.add_handler(CommandHandler("end",      end_cmd))
app.add_handler(CommandHandler("stop",     end_cmd))
app.add_handler(CommandHandler("referral", referral_cmd))
app.add_handler(CommandHandler("profile",  profile_cmd))
app.add_handler(CommandHandler("premium",  premium_cmd))

# Admin commands
app.add_handler(CommandHandler("stats",      admin_stats))
app.add_handler(CommandHandler("users",      admin_users))
app.add_handler(CommandHandler("finduser",   admin_find_user))
app.add_handler(CommandHandler("setpremium", admin_setpremium))
app.add_handler(CommandHandler("revoke",     admin_revoke))
app.add_handler(CommandHandler("broadcast",  admin_broadcast))
app.add_handler(CommandHandler("adminhelp",  admin_help))

# Callback Queries
app.add_handler(CallbackQueryHandler(btn_next,              pattern="^next$"))
app.add_handler(CallbackQueryHandler(btn_end,               pattern="^end$"))
app.add_handler(CallbackQueryHandler(btn_ref,               pattern="^ref$"))
app.add_handler(CallbackQueryHandler(btn_profile,           pattern="^profile$"))
app.add_handler(CallbackQueryHandler(btn_premium_menu,      pattern="^premium_menu$"))
app.add_handler(CallbackQueryHandler(btn_buy_plan,          pattern="^buy_(lite|weekly|plus|monthly)$"))
app.add_handler(CallbackQueryHandler(btn_check_joined,      pattern="^check_joined$"))
app.add_handler(CallbackQueryHandler(btn_set_gender,        pattern="^set_gender$"))
app.add_handler(CallbackQueryHandler(btn_gender_select,     pattern="^gender_(M|F)$"))
app.add_handler(CallbackQueryHandler(btn_set_age,           pattern="^set_age$"))
app.add_handler(CallbackQueryHandler(btn_set_preference,    pattern="^set_pref$"))
app.add_handler(CallbackQueryHandler(btn_preference_select, pattern="^setpref_(M|F|Any)$"))
app.add_handler(CallbackQueryHandler(btn_back_to_menu,      pattern="^back_to_menu$"))

# Message Handler
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

if __name__ == "__main__":
    print("Bot running...")
    app.run_polling()
