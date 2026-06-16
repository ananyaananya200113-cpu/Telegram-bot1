import os
import sqlite3
import time
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)
# ---------------- CONFIGURATION ---------------- #
# CHANNEL CONFIG
CHANNEL_USERNAME = "@AnnAnonymousChatbot"  # 📢 Official channel username (must start with @)
# UPI CONFIG
UPI_ID = "your-upi-id@okaxis"  # 💳 UPI Address for payments (GPay, PhonePe, Paytm)
# SUPPORT CONFIG
SUPPORT_CONTACT = "@YourSupportUsername"  # 📥 Support Telegram username for payment screenshot verification
# ---------------- TOKEN (SAFE) ---------------- #
TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TOKEN:
    raise Exception("TELEGRAM_TOKEN is not set in environment variables!")
# ---------------- ADMIN CONFIG ---------------- #
# To get your ID: open Telegram → search @userinfobot → send /start
ADMIN_IDS = {
    8550879731,  # Admin 1
    8637459083,  # Admin 2
    8946438351   # Admin 3
}
# ---------------- DATABASE ---------------- #
conn = sqlite3.connect("anon_chat.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    referral_count INTEGER DEFAULT 0,
    premium INTEGER DEFAULT 0,
    gender TEXT DEFAULT 'Not set',
    age INTEGER DEFAULT 0,
    total_dialogs INTEGER DEFAULT 0,
    today_dialogs INTEGER DEFAULT 0,
    last_dialog_date TEXT DEFAULT '',
    sent_messages INTEGER DEFAULT 0,
    received_messages INTEGER DEFAULT 0,
    premium_preference TEXT DEFAULT 'Any',
    premium_expiry INTEGER DEFAULT 0,
    free_chats_used INTEGER DEFAULT 0
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
# Setup database columns for profile, preference, and subscription fields
add_column_if_not_exists("users", "gender", "TEXT DEFAULT 'Not set'")
add_column_if_not_exists("users", "age", "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "total_dialogs", "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "today_dialogs", "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "last_dialog_date", "TEXT DEFAULT ''")
add_column_if_not_exists("users", "sent_messages", "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "received_messages", "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "premium_preference", "TEXT DEFAULT 'Any'")
add_column_if_not_exists("users", "premium_expiry", "INTEGER DEFAULT 0")
add_column_if_not_exists("users", "free_chats_used", "INTEGER DEFAULT 0")
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
    cur.execute("SELECT premium, premium_expiry FROM users WHERE user_id=?", (uid,))
    r = cur.fetchone()
    if not r:
        return False
    prem, expiry = r
    if prem == 1:
        # If premium_expiry is 0, it means unlimited/permanent premium
        if expiry == 0 or expiry > int(time.time()):
            return True
    return False
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
def add_premium_hours(uid, hours):
    now = int(time.time())
    cur.execute("SELECT premium, premium_expiry FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    if not row:
        create_user(uid)
        row = (0, 0)
    
    prem, expiry = row
    if prem == 1 and expiry > now:
        new_expiry = expiry + (hours * 3600)
    else:
        new_expiry = now + (hours * 3600)
        
    cur.execute("UPDATE users SET premium=1, premium_expiry=? WHERE user_id=?", (new_expiry, uid))
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
        SELECT user_id, gender, age, total_dialogs, today_dialogs, last_dialog_date, sent_messages, received_messages, premium_preference, premium_expiry
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
    profile = get_user_profile(uid)
    if not profile:
        create_user(uid)
        profile = get_user_profile(uid)
    total = profile[3] + 1
    last_date = profile[5]
    today = 1 if last_date != today_str else profile[4] + 1
    cur.execute("""
        UPDATE users
        SET total_dialogs=?, today_dialogs=?, last_dialog_date=?
        WHERE user_id=?
    """, (total, today, today_str, uid))
    conn.commit()
# Check if user is in channel
async def is_channel_member(bot, uid):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=uid)
        return member.status in ('creator', 'administrator', 'member')
    except Exception as e:
        print(f"ERROR: failed to verify channel membership for {uid}: {e}")
        # Fail-safe: if bot is not in channel yet, allow chat to prevent blocking everyone
        return True
# ---------------- ADMIN HELPERS ---------------- #
def is_admin(uid):
    return uid in ADMIN_IDS
def get_all_users(limit=50, offset=0):
    cur.execute("""
        SELECT user_id, gender, age, total_dialogs, today_dialogs,
               sent_messages, received_messages, referral_count, premium, last_dialog_date
        FROM users
        ORDER BY total_dialogs DESC
        LIMIT ? OFFSET ?
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
    cur.execute("SELECT COUNT(*) FROM users WHERE premium=1")
    return cur.fetchone()[0]
def get_active_today_count():
    today_str = datetime.date.today().isoformat()
    cur.execute("SELECT COUNT(*) FROM users WHERE last_dialog_date=?", (today_str,))
    return cur.fetchone()[0]
def get_total_messages():
    cur.execute("SELECT SUM(sent_messages) FROM users")
    result = cur.fetchone()[0]
    return result or 0
# ---------------- UI ---------------- #
def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Next", callback_data="next"),
         InlineKeyboardButton("❌ End", callback_data="end")],
        [InlineKeyboardButton("👤 Profile", callback_data="profile"),
         InlineKeyboardButton("💰 Referral", callback_data="ref")],
        [InlineKeyboardButton("💎 Premium Shop", callback_data="premium")]
    ])
def profile_menu(is_vip=False):
    buttons = [
        [InlineKeyboardButton("👫 Set Gender", callback_data="set_gender"),
         InlineKeyboardButton("🔞 Set Age", callback_data="set_age")]
    ]
    if is_vip:
        buttons.append([InlineKeyboardButton("🎯 Set Preference", callback_data="set_pref")])
    buttons.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(buttons)
def gender_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👨 Male", callback_data="gender_M"),
         InlineKeyboardButton("👩 Female", callback_data="gender_F")],
        [InlineKeyboardButton("🔙 Back to Profile", callback_data="profile")]
    ])
def preference_selection_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👨 Male", callback_data="setpref_M"),
         InlineKeyboardButton("👩 Female", callback_data="setpref_F")],
        [InlineKeyboardButton("🟢 Any", callback_data="setpref_Any")],
        [InlineKeyboardButton("🔙 Back to Profile", callback_data="profile")]
    ])
def back_only_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Profile", callback_data="profile")]
    ])
def back_to_shop_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Shop", callback_data="premium")]
    ])
def joined_channel_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ I've Joined", callback_data="check_joined")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_menu")]
    ])
# ---------------- VIP CHECK ---------------- #
async def check_vip(bot, uid):
    # Grant 6 hours of premium for successful referral
    if get_ref(uid) >= 10 and not is_premium(uid):
        # Notify once when they reach the 10 refs milestone
        await bot.send_message(uid, "💎 VIP UNLOCKED! You reached 10 referrals!")
    
    add_premium_hours(uid, 6)
    await bot.send_message(uid, "🎁 Referral successful! 6 Hours Premium VIP has been added to your account!")
    await ask_preference(bot, uid)
async def ask_preference(bot, uid):
    text = (
        "💎 *VIP Preference Setup*\n\n"
        "Please select your preferred gender for matching. The bot will prioritize connecting you with this gender first!"
    )
    try:
        await bot.send_message(uid, text, reply_markup=preference_selection_menu(), parse_mode="Markdown")
    except Exception as e:
        print(f"ERROR: failed to send preference prompt to {uid}: {e}")
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
        except Exception as e:
            print(f"ERROR processing referral start: {e}")
    await update.message.reply_text("Welcome 👋", reply_markup=menu())
# ---------------- MATCHING ---------------- #
async def next_user(uid, context, send):
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
    # Check join channel restrictions for free users
    is_vip = is_premium(uid)
    if not is_vip:
        joined = await is_channel_member(context.bot, uid)
        if not joined:
            cur.execute("SELECT free_chats_used FROM users WHERE user_id=?", (uid,))
            row = cur.fetchone()
            used = row[0] if row else 0
            if used >= 3:
                # Block search and show joined prompt
                await show_join_channel_prompt(uid, context, send)
                return
    # Fetch searching user details
    cur.execute("SELECT gender, premium, premium_preference FROM users WHERE user_id=?", (uid,))
    row_a = cur.fetchone()
    gender_a = row_a[0] if row_a else 'Not set'
    premium_a = row_a[1] if row_a else 0
    pref_a = row_a[2] if row_a else 'Any'
    # Get active matchmaking candidates in queue
    cur.execute("""
        SELECT q.user_id, u.gender, u.premium, u.premium_preference, q.timestamp
        FROM queue q
        JOIN users u ON q.user_id = u.user_id
        WHERE q.user_id != ?
    """, (uid,))
    candidates = cur.fetchall()
    if not candidates:
        add_queue(uid)
        await send("Searching...")
        return
    # Score candidates based on preference match compatibility
    scored_candidates = []
    for cid, c_gender, c_premium, c_pref, c_ts in candidates:
        # A's preference score
        if premium_a == 1 and pref_a in ('M', 'F'):
            a_score = 2 if c_gender == pref_a else 0
        else:
            a_score = 1
        # Candidate's preference score
        if c_premium == 1 and c_pref in ('M', 'F'):
            b_score = 2 if gender_a == c_pref else 0
        else:
            b_score = 1
        total_score = a_score + b_score
        scored_candidates.append((total_score, c_ts, cid))
    # Sort: highest score first, then oldest queue timestamp first
    scored_candidates.sort(key=lambda x: (-x[0], x[1]))
    
    partner = scored_candidates[0][2]
    # Dequeue both and create the chat
    remove_queue(uid)
    remove_queue(partner)
    cur.execute("INSERT INTO active_chats VALUES (?,?)", (uid, partner))
    conn.commit()
    # Track free chat limits if they are not in the channel
    # User A
    if not is_premium(uid) and not await is_channel_member(context.bot, uid):
        cur.execute("UPDATE users SET free_chats_used = free_chats_used + 1 WHERE user_id=?", (uid,))
    # User B (Partner)
    if not is_premium(partner) and not await is_channel_member(context.bot, partner):
        cur.execute("UPDATE users SET free_chats_used = free_chats_used + 1 WHERE user_id=?", (partner,))
    conn.commit()
    increment_dialogs(uid)
    increment_dialogs(partner)
    match_text = (
        "🎉 Match successful!\n\n"
        "/next – End and start a new match\n"
        "/stop – End the current chat"
    )
    await context.bot.send_message(uid, match_text, reply_markup=menu())
    await context.bot.send_message(partner, match_text, reply_markup=menu())
# ---------------- CHANNEL JOIN PROMPT ---------------- #
async def show_join_channel_prompt(uid, context, send):
    text = (
        "🔒 *Free Chat Limit Reached!*\n\n"
        "To continue chatting, please join our official channel.\n\n"
        f"📢 Join Channel: {CHANNEL_USERNAME}\n\n"
        "After joining, click the button below to continue chatting.\n\n"
        "🎁 Channel members get uninterrupted access to Ann Chat."
    )
    await send(text, reply_markup=joined_channel_menu(), parse_mode="Markdown")
async def btn_check_joined(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    
    joined = await is_channel_member(context.bot, uid)
    if joined:
        await q.message.edit_text(
            "✅ Thank you for joining! You now have uninterrupted access to Ann Chat.",
            reply_markup=menu()
        )
        await next_user(uid, context, q.message.reply_text)
    else:
        await q.message.reply_text(
            f"❌ You have not joined the channel yet. Please join {CHANNEL_USERNAME} first.",
            reply_markup=joined_channel_menu()
        )
# ---------------- REFERRAL Flow ---------------- #
async def referral(uid, send):
    ref = get_ref(uid)
    prem = is_premium(uid)
    link = f"https://t.me/Annchattingbot?start={uid}"
    
    await send(
        f"💰 *Referral System*\n\n"
        f"👥 Referrals: `{ref}`\n"
        f"⭐ Status: `{'VIP' if prem else 'FREE'}`\n\n"
        f"Invite your friends and earn:\n"
        f"🎁 *6 Hours Premium* for every successful referral!\n\n"
        f"🔗 *Your Referral Link:*\n"
        f"{link}",
        parse_mode="Markdown"
    )
# ---------------- PREMIUM PLANS SHOP Flow ---------------- #
async def show_premium_plans(uid, send_or_edit):
    profile = get_user_profile(uid)
    expiry = profile[9] if (profile and len(profile) > 9) else 0
    now = int(time.time())
    
    if is_premium(uid):
        if expiry == 0:
            status_text = "⭐ Status: *Premium (Lifetime)*\n\n"
        else:
            time_left = expiry - now
            if time_left > 0:
                hours = time_left // 3600
                minutes = (time_left % 3600) // 60
                status_text = f"⭐ Status: *Premium ({hours}h {minutes}m remaining)*\n\n"
            else:
                status_text = "⭐ Status: *Free*\n\n"
    else:
        status_text = "⭐ Status: *Free*\n\n"
    plans_text = (
        f"🛒 *ANN PREMIUM SHOP*\n\n"
        f"{status_text}"
        f"Select a premium package below to view details, benefits, and payment methods:"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Premium Lite (1 Day) - ₹15", callback_data="viewplan_lite")],
        [InlineKeyboardButton("💎 Premium Weekly (7 Days) - ₹59", callback_data="viewplan_weekly")],
        [InlineKeyboardButton("💎 Premium Plus (15 Days) - ₹99", callback_data="viewplan_plus")],
        [InlineKeyboardButton("💎 Premium Monthly (30 Days) - ₹179", callback_data="viewplan_monthly")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_menu")]
    ])
    await send_or_edit(plans_text, reply_markup=markup, parse_mode="Markdown")
async def btn_view_plan(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    plan = q.data.split("_")[1]  # 'lite', 'weekly', 'plus', 'monthly'
    
    # Plans configuration details
    details = {
        "lite": {
            "title": "Premium Lite",
            "price_inr": 15,
            "stars": 15,
            "duration": "1 Day",
            "benefits": "• Gender Preference Matching\n• Unlimited Partner Skips\n• Priority Match Queue Access"
        },
        "weekly": {
            "title": "Premium Weekly",
            "price_inr": 59,
            "stars": 59,
            "duration": "7 Days",
            "benefits": "• Gender Preference Matching\n• Unlimited Partner Skips\n• Priority Match Queue Access\n• Premium Badge Display"
        },
        "plus": {
            "title": "Premium Plus",
            "price_inr": 99,
            "stars": 99,
            "duration": "15 Days",
            "benefits": "• Gender Preference Matching\n• Unlimited Partner Skips\n• Priority Match Queue Access\n• Premium Badge Display\n• Faster Match Priority"
        },
        "monthly": {
            "title": "Premium Monthly",
            "price_inr": 179,
            "stars": 179,
            "duration": "30 Days",
            "benefits": "• Gender Preference Matching\n• Unlimited Partner Skips\n• Priority Match Queue Access\n• Premium Badge Display\n• Faster Match Priority\n• Highest Match Queue Priority"
        }
    }
    
    plan_info = details.get(plan)
    if not plan_info:
        return
        
    text = (
        f"💎 *{plan_info['title']}*\n\n"
        f"⏳ Duration: *{plan_info['duration']}*\n"
        f"💰 Price: *₹{plan_info['price_inr']}* or *{plan_info['stars']} Telegram Stars*\n\n"
        f"🎯 *Benefits:*\n"
        f"{plan_info['benefits']}\n\n"
        f"Select a payment method below to purchase:"
    )
    
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ Pay via Telegram Stars (Auto)", callback_data=f"stars_{plan}")],
        [InlineKeyboardButton("💳 Pay via UPI", callback_data=f"upi_{plan}")],
        [InlineKeyboardButton("🔙 Back to Shop", callback_data="premium")]
    ])
    
    await q.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
async def btn_stars_payment(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    plan = q.data.split("_")[1] # e.g. "lite", "weekly", etc.
    
    details = {
        "lite": {"price": 15, "days": 1, "title": "Premium Lite"},
        "weekly": {"price": 59, "days": 7, "title": "Premium Weekly"},
        "plus": {"price": 99, "days": 15, "title": "Premium Plus"},
        "monthly": {"price": 179, "days": 30, "title": "Premium Monthly"}
    }
    
    plan_info = details.get(plan)
    if not plan_info:
        return
        
    try:
        await context.bot.send_invoice(
            chat_id=uid,
            title=plan_info["title"],
            description=f"Upgrade to Premium for {plan_info['days']} days.",
            payload=f"premium_{plan}_{uid}",
            provider_token="", # Empty for Telegram Stars
            currency="XTR",
            prices=[LabeledPrice(f"{plan_info['title']} ({plan_info['days']} Days)", plan_info["price"])],
            start_parameter=f"premium-{plan}"
        )
    except Exception as e:
        print(f"Error sending invoice: {e}")
        await q.message.reply_text("❌ Failed to initiate Telegram Stars checkout. Please try again or contact support.")
async def btn_upi_payment(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    plan = q.data.split("_")[1] # e.g. "lite", "weekly", etc.
    
    details = {
        "lite": {"price": 15, "days": 1, "title": "Premium Lite"},
        "weekly": {"price": 59, "days": 7, "title": "Premium Weekly"},
        "plus": {"price": 99, "days": 15, "title": "Premium Plus"},
        "monthly": {"price": 179, "days": 30, "title": "Premium Monthly"}
    }
    
    plan_info = details.get(plan)
    if not plan_info:
        return
        
    upi_url = f"upi://pay?pa={UPI_ID}&pn=AnnChatBot&am={plan_info['price']}&cu=INR&tn=Premium_{plan}_{uid}"
    
    text = (
        f"💳 *UPI Payment - {plan_info['title']}*\n\n"
        f"💰 Amount: *₹{plan_info['price']}*\n"
        f"⏳ Duration: *{plan_info['days']} Days*\n\n"
        f"1. Click the button below to pay using GPay, PhonePe, Paytm, or any UPI app.\n"
        f"2. Take a screenshot of the successful transaction.\n"
        f"3. Send the screenshot to our support team: {SUPPORT_CONTACT} along with your User ID: `{uid}`.\n\n"
        f"⚠️ Admin will verify and activate your Premium manually."
    )
    
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Open UPI App to Pay", url=upi_url)],
        [InlineKeyboardButton("🔙 Back to Plan Details", callback_data=f"viewplan_{plan}")]
    ])
    
    await q.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
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
    
    async def edit_msg(text, reply_markup, parse_mode=None):
        await q.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        
    await referral(uid, edit_msg)
async def btn_premium(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    user_states.pop(uid, None)
    
    async def edit_msg(text, reply_markup, parse_mode=None):
        await q.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        
    await show_premium_plans(uid, edit_msg)
# ---------------- PROFILE SYSTEM & BUTTONS ---------------- #
async def show_profile(uid, send_or_edit, reply_markup=None):
    profile = get_user_profile(uid)
    if not profile:
        create_user(uid)
        profile = get_user_profile(uid)
    _, gender, age, total_dialogs, today_dialogs, last_dialog_date, sent_messages, received_messages, pref, expiry = profile
    today_str = datetime.date.today().isoformat()
    if last_dialog_date != today_str:
        today_dialogs = 0
    gender_display = gender if gender else "Not set"
    age_display = str(age) if (age and age > 0) else "Not set"
    
    is_vip = is_premium(uid)
    badge = " 💎" if is_vip else ""
    pref_display = pref if is_vip else "Locked 🔒"
    profile_text = (
        f"#️⃣ ID — {uid}{badge}\n\n"
        f"👫 Gender — {gender_display}\n"
        f"🔞 Age — {age_display}\n"
        f"🎯 Preference — {pref_display}\n\n"
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
    gender_code = q.data.split("_")[1]
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
async def btn_set_preference(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not is_premium(uid):
        await q.message.reply_text(
            "🔒 Setting matching preferences is a VIP feature!\n\n"
            "Refer 10 friends to unlock VIP status automatically.",
            reply_markup=menu()
        )
        return
    await q.message.edit_text("Select your preferred gender for matching:", reply_markup=preference_selection_menu())
async def btn_preference_select(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    pref_code = q.data.split("pref_")[1]  # 'M', 'F', or 'Any'
    update_premium_preference(uid, pref_code)
    async def edit_msg(text, reply_markup):
        await q.message.edit_text(f"✅ Preference set to {pref_code}!\n\n" + text, reply_markup=reply_markup)
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
    
    async def reply_msg(text, reply_markup, parse_mode=None):
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        
    await referral(uid, reply_msg)
async def premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_states.pop(uid, None)
    
    async def reply_msg(text, reply_markup, parse_mode=None):
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        
    await show_premium_plans(uid, reply_msg)
async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_states.pop(uid, None)
    async def reply_msg(text, reply_markup):
        await update.message.reply_text(text, reply_markup=reply_markup)
    await show_profile(uid, reply_msg)
# ---------------- ADMIN COMMANDS ---------------- #
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    total       = get_user_count()
    vip         = get_vip_count()
    active_today= get_active_today_count()
    active_chats= get_active_chat_count()
    in_queue    = get_queue_count()
    total_msgs  = get_total_messages()
    await update.message.reply_text(
        f"📊 *Bot Statistics*\n\n"
        f"👥 Total Users: `{total}`\n"
        f"💎 VIP Users: `{vip}`\n"
        f"🟢 Active Today: `{active_today}`\n"
        f"💬 Active Chats: `{active_chats}`\n"
        f"🔎 In Queue: `{in_queue}`\n"
        f"✉️ Total Messages Sent: `{total_msgs}`",
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
    page_size = 10
    offset    = (page - 1) * page_size
    users     = get_all_users(limit=page_size, offset=offset)
    total     = get_user_count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    await update.message.reply_text(
        f"📊 *Bot Summary*\n"
        f"👥 Total Users: `{total}` | 💬 Active Chats: `{get_active_chat_count()}` | 🔎 In Queue: `{get_queue_count()}`\n"
        f"📄 Page `{page}` / `{total_pages}`",
        parse_mode="Markdown"
    )
    if not users:
        await update.message.reply_text("No users found.")
        return
    lines = []
    for u in users:
        user_id, gender, age, total_d, today_d, sent, received, refs, prem, last_date = u
        status       = "💎" if prem else "👤"
        gender_icon  = "👨" if gender == "M" else "👩" if gender == "F" else "❓"
        age_display  = str(age) if age and age > 0 else "N/A"
        last_display = last_date if last_date else "Never"
        lines.append(
            f"{status} `{user_id}`\n"
            f"  {gender_icon} {gender or 'N/A'} | 🔞 {age_display} | 👥 Refs: {refs}\n"
            f"  💬 Dialogs: {total_d} (Today: {today_d})\n"
            f"  ✉️ Sent: {sent} | Rcvd: {received}\n"
            f"  📅 Last active: {last_display}"
        )
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")
    if total_pages > 1:
        await update.message.reply_text(
            f"📄 Page {page}/{total_pages}  →  Use `/users {page + 1}` for next page" if page < total_pages
            else f"✅ Last page. Use `/users {page - 1}` to go back.",
            parse_mode="Markdown"
        )
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
        await update.message.reply_text("⚠️ Invalid user ID. It must be a number.")
        return
    profile = get_user_profile(target_id)
    if not profile:
        await update.message.reply_text("❌ User not found in database.")
        return
    user_id, gender, age, total_d, today_d, last_date, sent, received, pref, expiry = profile
    refs  = get_ref(target_id)
    prem  = is_premium(target_id)
    in_c  = bool(in_chat(target_id))
    in_q  = bool(cur.execute("SELECT 1 FROM queue WHERE user_id=?", (target_id,)).fetchone())
    status_line = "💎 VIP" if prem else "👤 Free"
    if in_c:
        activity = "💬 Currently in chat"
    elif in_q:
        activity = "🔎 In matchmaking queue"
    else:
        activity = "💤 Idle"
    expiry_display = "Lifetime" if expiry == 0 else datetime.datetime.fromtimestamp(expiry).strftime('%Y-%m-%d %H:%M:%S')
    await update.message.reply_text(
        f"🔍 *User Details*\n\n"
        f"🆔 ID: `{user_id}`\n"
        f"👫 Gender: {gender or 'Not set'}\n"
        f"🔞 Age: {age if age and age > 0 else 'Not set'}\n"
        f"🎯 Preference: {pref or 'Any'}\n"
        f"⭐ Status: {status_line}\n"
        f"⏳ Expiry: {expiry_display}\n"
        f"📡 Activity: {activity}\n\n"
        f"💬 Total Dialogs: `{total_d}`\n"
        f"📅 Today's Dialogs: `{today_d}`\n"
        f"📆 Last Active: {last_date or 'Never'}\n\n"
        f"✉️ Sent: `{sent}` | Received: `{received}`\n"
        f"👥 Referrals: `{refs}`",
        parse_mode="Markdown"
    )
async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    if not context.args:
        await update.message.reply_text("Usage: `/broadcast <your message>`", parse_mode="Markdown")
        return
    message = " ".join(context.args)
    cur.execute("SELECT user_id FROM users")
    all_users = cur.fetchall()
    sent_count  = 0
    fail_count  = 0
    await update.message.reply_text(f"📢 Broadcasting to {len(all_users)} users...")
    for (user_id,) in all_users:
        try:
            await context.bot.send_message(user_id, f"📢 *Announcement*\n\n{message}", parse_mode="Markdown")
            sent_count += 1
        except Exception:
            fail_count += 1
    await update.message.reply_text(
        f"✅ Broadcast complete!\n"
        f"📨 Sent: {sent_count}\n"
        f"❌ Failed: {fail_count} (blocked/deactivated accounts)"
    )
async def admin_setpremium(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    days = 30
    if len(context.args) > 1:
        try:
            days = int(context.args[1])
        except ValueError:
            pass
    profile = get_user_profile(target_id)
    if not profile:
        await update.message.reply_text("❌ User not found.")
        return
    now = int(time.time())
    new_expiry = now + (days * 24 * 3600)
    cur.execute("UPDATE users SET premium=1, premium_expiry=? WHERE user_id=?", (new_expiry, target_id))
    conn.commit()
    await update.message.reply_text(f"💎 User `{target_id}` has been granted VIP status for {days} days!", parse_mode="Markdown")
    try:
        await context.bot.send_message(target_id, f"💎 You have been granted VIP status for {days} days by the admin!")
        await ask_preference(context.bot, target_id)
    except Exception:
        pass
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    await update.message.reply_text(
        "🛠 *Admin Commands*\n\n"
        "`/stats` — Bot statistics overview\n"
        "`/users [page]` — List all users (10 per page)\n"
        "`/finduser <id>` — Look up a specific user\n"
        "`/setpremium <id> [days]` — Grant VIP to a user for custom days\n"
        "`/broadcast <msg>` — Send message to all users\n"
        "`/adminhelp` — Show this help message",
        parse_mode="Markdown"
    )
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
                user_states.pop(uid, None)
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
        await context.bot.send_message(chat_id=partner, text=update.message.text)
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
# ---------------- PAYMENT GATEWAY CALLBACKS ---------------- #
async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    if query.invoice_payload.startswith("premium_"):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="An error occurred during verification. Please try again.")
async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    payload = payment.invoice_payload
    uid = update.effective_user.id
    
    parts = payload.split("_")
    if len(parts) >= 3:
        plan = parts[1]
        
        days_map = {
            "lite": 1,
            "weekly": 7,
            "plus": 15,
            "monthly": 30
        }
        days = days_map.get(plan, 30)
        
        # Credit user hours
        add_premium_hours(uid, days * 24)
        
        text = (
            f"🎉 *Payment Successful!*\n\n"
            f"Thank you for your purchase! Your account has been upgraded to *Premium VIP* for *{days} Days*.\n\n"
            f"Explore preference matchmaking, skips, and priority access now!"
        )
        await update.message.reply_text(text, parse_mode="Markdown")
        await ask_preference(context.bot, uid)
# ---------------- APP ---------------- #
app = Application.builder().token(TOKEN).build()
# User commands
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("next", next_cmd))
app.add_handler(CommandHandler("end", end_cmd))
app.add_handler(CommandHandler("stop", end_cmd))
app.add_handler(CommandHandler("referral", referral_cmd))
app.add_handler(CommandHandler("premium", premium_cmd))
app.add_handler(CommandHandler("profile", profile_cmd))
# Admin commands
app.add_handler(CommandHandler("stats", admin_stats))
app.add_handler(CommandHandler("users", admin_users))
app.add_handler(CommandHandler("finduser", admin_find_user))
app.add_handler(CommandHandler("setpremium", admin_setpremium))
app.add_handler(CommandHandler("broadcast", admin_broadcast))
app.add_handler(CommandHandler("adminhelp", admin_help))
# Callback Queries
app.add_handler(CallbackQueryHandler(btn_next, pattern="^next$"))
app.add_handler(CallbackQueryHandler(btn_end, pattern="^end$"))
app.add_handler(CallbackQueryHandler(btn_ref, pattern="^ref$"))
app.add_handler(CallbackQueryHandler(btn_premium, pattern="^premium$"))
app.add_handler(CallbackQueryHandler(btn_view_plan, pattern="^viewplan_(lite|weekly|plus|monthly)$"))
app.add_handler(CallbackQueryHandler(btn_stars_payment, pattern="^stars_(lite|weekly|plus|monthly)$"))
app.add_handler(CallbackQueryHandler(btn_upi_payment, pattern="^upi_(lite|weekly|plus|monthly)$"))
app.add_handler(CallbackQueryHandler(btn_check_joined, pattern="^check_joined$"))
app.add_handler(CallbackQueryHandler(btn_profile, pattern="^profile$"))
app.add_handler(CallbackQueryHandler(btn_set_gender, pattern="^set_gender$"))
app.add_handler(CallbackQueryHandler(btn_gender_select, pattern="^gender_(M|F)$"))
app.add_handler(CallbackQueryHandler(btn_set_age, pattern="^set_age$"))
app.add_handler(CallbackQueryHandler(btn_set_preference, pattern="^set_pref$"))
app.add_handler(CallbackQueryHandler(btn_preference_select, pattern="^setpref_(M|F|Any)$"))
app.add_handler(CallbackQueryHandler(btn_back_to_menu, pattern="^back_to_menu$"))
# PreCheckout and Payment Handlers
app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
# Message Handlers
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
if __name__ == "__main__":
    print("Bot running...")
    app.run_polling()
