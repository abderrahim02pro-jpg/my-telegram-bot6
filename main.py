import os
import sqlite3
from threading import Thread
from flask import Flask
import telebot
from telebot.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

# ==========================================
# 1. إعدادات البيئة والأمان (Security Setup)
# ==========================================
BOT_TOKEN = os.environ.get("8892223974:AAHTrRcVB-C8M_mgwi1YOn7bDN4T7vuA3Xs")
ADMIN_ID_RAW = os.environ.get("6536672093", "0")

if not BOT_TOKEN:
    raise ValueError("❌ خطأ: لم يتم العثور على BOT_TOKEN في متغيرات البيئة.")

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    raise ValueError("❌ خطأ: ADMIN_ID يجب أن يكون رقماً صحيحاً.")

DB_NAME = "bot_database.db"
VALID_OPERATORS = ["MOBILIS", "DJEZZY", "OOREDOO"]

# ==========================================
# 2. سيرفر الحفاظ على التشغيل (Keep Alive)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 البوت يعمل بنجاح 24/7!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def start_keep_alive():
    server_thread = Thread(target=run_flask, daemon=True)
    server_thread.start()

# ==========================================
# 3. إدارة قاعدة البيانات (SQLite Architecture)
# ==========================================
def get_db():
    return sqlite3.connect(DB_NAME, check_same_thread=False)

def init_db():
    with get_db() as conn:
        cursor = conn.cursor()
        
        # 1. جدول المستخدمين
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance REAL DEFAULT 0.0,
                pending_balance REAL DEFAULT 0.0
            )
        ''')
        
        # 2. جدول الإيميلات
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE,
                status TEXT DEFAULT 'available',
                reserved_by INTEGER DEFAULT NULL
            )
        ''')
        
        # 3. جدول عمليات السحب
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                phone_number TEXT,
                operator TEXT,
                status TEXT DEFAULT 'pending'
            )
        ''')
        
        # 4. جدول الإعدادات
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # القيم الافتراضية للإعدادات
        defaults = [
            ('reward_per_email', '50'),
            ('min_withdrawal', '100'),
            ('msg_success', 'تم شحن رصيدك بنجاح بمبلغ {amount} دج!'),
            ('msg_reject', 'تم رفض طلب السحب الخاص بك. يرجى إعادة إرسال رقم هاتف صالح للاستقبال.')
        ]
        cursor.executemany("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", defaults)
        conn.commit()

def get_setting(key: str) -> str:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else ""

def set_setting(key: str, value: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()

def get_or_create_user(user_id: int, username: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, balance, pending_balance FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            cursor.execute(
                "INSERT INTO users (user_id, username, balance, pending_balance) VALUES (?, ?, 0.0, 0.0)",
                (user_id, username or "Unknown")
            )
            conn.commit()
            return {"user_id": user_id, "username": username, "balance": 0.0, "pending_balance": 0.0}
        return {"user_id": row[0], "username": row[1], "balance": row[2], "pending_balance": row[3]}

def get_available_emails():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, email FROM emails WHERE status = 'available'")
        return cursor.fetchall()

def reserve_email(email_id: int, user_id: int) -> bool:
    """حجز الإيميل لمنع مستخدمين متعددين من اختياره في نفس الوقت"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE emails SET status = 'reserved', reserved_by = ? WHERE id = ? AND status = 'available'",
            (user_id, email_id)
        )
        conn.commit()
        return cursor.rowcount > 0

def get_email_by_id(email_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, email, status, reserved_by FROM emails WHERE id = ?", (email_id,))
        return cursor.fetchone()

def approve_email_task(email_id: int, user_id: int, reward: float) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM emails WHERE id = ?", (email_id,))
        row = cursor.fetchone()
        if not row or row[0] != 'reserved':
            return False
        cursor.execute("UPDATE emails SET status = 'completed' WHERE id = ?", (email_id,))
        cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (reward, user_id))
        conn.commit()
        return True

def reject_email_task(email_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM emails WHERE id = ?", (email_id,))
        row = cursor.fetchone()
        if not row or row[0] != 'reserved':
            return False
        cursor.execute("UPDATE emails SET status = 'available', reserved_by = NULL WHERE id = ?", (email_id,))
        conn.commit()
        return True

def batch_add_emails(email_list: list) -> int:
    added = 0
    with get_db() as conn:
        cursor = conn.cursor()
        for e in email_list:
            cleaned = e.strip()
            if cleaned:
                try:
                    cursor.execute("INSERT INTO emails (email) VALUES (?)", (cleaned,))
                    added += 1
                except sqlite3.IntegrityError:
                    pass
        conn.commit()
    return added

def create_withdrawal(user_id: int, amount: float, phone: str, operator: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row or row[0] < amount:
            return None
        
        cursor.execute(
            "UPDATE users SET balance = balance - ?, pending_balance = pending_balance + ? WHERE user_id = ?",
            (amount, amount, user_id)
        )
        cursor.execute(
            "INSERT INTO withdrawals (user_id, amount, phone_number, operator, status) VALUES (?, ?, ?, ?, 'pending')",
            (user_id, amount, phone, operator)
        )
        w_id = cursor.lastrowid
        conn.commit()
        return w_id

def approve_withdrawal(w_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, amount, status FROM withdrawals WHERE id = ?", (w_id,))
        row = cursor.fetchone()
        if not row or row[2] != 'pending':
            return None
        user_id, amount, _ = row
        cursor.execute("UPDATE users SET pending_balance = pending_balance - ? WHERE user_id = ?", (amount, user_id))
        cursor.execute("UPDATE withdrawals SET status = 'approved' WHERE id = ?", (w_id,))
        conn.commit()
        return {"user_id": user_id, "amount": amount}

def reject_withdrawal(w_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, amount, status FROM withdrawals WHERE id = ?", (w_id,))
        row = cursor.fetchone()
        if not row or row[2] != 'pending':
            return None
        user_id, amount, _ = row
        cursor.execute(
            "UPDATE users SET pending_balance = pending_balance - ?, balance = balance + ? WHERE user_id = ?",
            (amount, amount, user_id)
        )
        cursor.execute("UPDATE withdrawals SET status = 'rejected' WHERE id = ?", (w_id,))
        conn.commit()
        return {"user_id": user_id, "amount": amount}

# ==========================================
# 4. واجهة التفاعل للبوت (Telegram Bot Rules)
# ==========================================
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

MAIN_KEYBOARD = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
MAIN_KEYBOARD.add(KeyboardButton("📧 الإيميلات المتاحة"), KeyboardButton("💰 حسابي وسحب الأرباح"))

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

@bot.message_handler(commands=['start'])
def handle_start(message):
    get_or_create_user(message.from_user.id, message.from_user.username)
    welcome_text = (
        f"مرحباً بك <b>{message.from_user.first_name}</b> في بوت المهام المصغرة! 🇩🇿\n\n"
        "قم بإنشاء الإيميلات المطلوبة واكسب رصيداً يمكنك سحبه مباشرة عبر خدمات الفليكسي (Mobilis, Djezzy, Ooredoo).\n\n"
        "استخدم القائمة أدناه للبدء 👇"
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=MAIN_KEYBOARD)

@bot.message_handler(func=lambda msg: msg.text == "📧 الإيميلات المتاحة")
def handle_available_emails(message):
    emails = get_available_emails()
    if not emails:
        bot.send_message(message.chat.id, "❌ لا توجد إيميلات متاحة للعمل حالياً. يرجى المحاولة لاحقاً.")
        return

    markup = InlineKeyboardMarkup()
    for e_id, email_str in emails:
        markup.add(InlineKeyboardButton(text=f"✉️ {email_str}", callback_data=f"select_email_{e_id}"))

    bot.send_message(message.chat.id, "اختر إيميل لبدء المهمة:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("select_email_"))
def handle_email_selection(call):
    email_id = int(call.data.split("_")[2])
    user_id = call.from_user.id

    if not reserve_email(email_id, user_id):
        bot.answer_callback_query(call.id, "⚠️ عذراً، تم حجز هذا الإيميل من قبل مستخدم آخر!", show_alert=True)
        return

    email_data = get_email_by_id(email_id)
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(text="تم إنشاء الإيميل بالفعل ✅", callback_data=f"done_email_{email_id}"))

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=(
            f"لقد قمت بحجز الإيميل التالي:\n<code>{email_data[1]}</code>\n\n"
            "يرجى إنشاؤه فوراً ثم اضغط على الزر أدناه لتأكيد الإنجاز."
        ),
        reply_markup=markup
    )
    bot.answer_callback_query(call.id, "تم حجز الإيميل لك!")

@bot.callback_query_handler(func=lambda call: call.data.startswith("done_email_"))
def handle_email_done(call):
    email_id = int(call.data.split("_")[2])
    user_id = call.from_user.id
    email_data = get_email_by_id(email_id)

    if not email_data or email_data[3] != user_id:
        bot.answer_callback_query(call.id, "حدث خطأ أو أن الإيميل غير محجوز باسمك.", show_alert=True)
        return

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="تم إرسال المهمة للمراجعة من قبل الإدارة ⏳. سيتم إضافة الرصيد لحسابك فور التحقق."
    )

    admin_markup = InlineKeyboardMarkup()
    admin_markup.row(
        InlineKeyboardButton("موافقة (إضافة رصيد) ✅", callback_data=f"app_e_{email_id}_{user_id}"),
        InlineKeyboardButton("رفض ❌", callback_data=f"rej_e_{email_id}_{user_id}")
    )

    reward_str = get_setting("reward_per_email")
    bot.send_message(
        ADMIN_ID,
        f"📥 <b>مهمة إنشاء إيميل جديدة للمراجعة</b>:\n\n"
        f"• الإيميل: <code>{email_data[1]}</code>\n"
        f"• المستخدم: @{call.from_user.username or 'بدون_معرف'} (ID: <code>{user_id}</code>)\n"
        f"• القيمة: {reward_str} دج",
        reply_markup=admin_markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith(("app_e_", "rej_e_")))
def handle_admin_email_review(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "غير مصرح لك القيام بهذا الإجراء.", show_alert=True)
        return

    parts = call.data.split("_")
    action = parts[0]
    email_id = int(parts[2])
    target_user_id = int(parts[3])

    if action == "app":
        reward = float(get_setting("reward_per_email"))
        if approve_email_task(email_id, target_user_id, reward):
            bot.edit_message_text(
                f"{call.message.text}\n\n✅ <b>تمت الموافقة وإضافة الرصيد للمستخدم.</b>",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id
            )
            user_msg = get_setting("msg_success").replace("{amount}", str(reward))
            bot.send_message(target_user_id, user_msg)
        else:
            bot.answer_callback_query(call.id, "تعذر معالجة الطلب.", show_alert=True)

    elif action == "rej":
        if reject_email_task(email_id):
            bot.edit_message_text(
                f"{call.message.text}\n\n❌ <b>تم رفض المهمة وإعادة الإيميل للقائمة.</b>",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id
            )
            bot.send_message(target_user_id, "⚠️ تم رفض مهمة إنشاء الإيميل الخاصة بك. يرجى اتباع التعليمات بدقة.")
        else:
            bot.answer_callback_query(call.id, "تعذر معالجة الطلب.", show_alert=True)

@bot.message_handler(func=lambda msg: msg.text == "💰 حسابي وسحب الأرباح")
def handle_account_info(message):
    user = get_or_create_user(message.from_user.id, message.from_user.username)
    min_w = float(get_setting("min_withdrawal"))

    text = (
        f"📊 <b>تفاصيل حسابك</b>:\n\n"
        f"• الرصيد المتاح: <b>{user['balance']:.2f} دج</b>\n"
        f"• الرصيد المعلق: <b>{user['pending_balance']:.2f} دج</b>\n"
        f"• الحد الأدنى للسحب: <b>{min_w:.2f} دج</b>"
    )

    markup = InlineKeyboardMarkup()
    if user['balance'] >= min_w:
        markup.add(InlineKeyboardButton("طلب سحب رصيد (فليكسي) 📱", callback_data="start_withdrawal"))

    bot.send_message(message.chat.id, text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "start_withdrawal")
def prompt_withdrawal_data(call):
    user = get_or_create_user(call.from_user.id, call.from_user.username)
    min_w = float(get_setting("min_withdrawal"))

    if user['balance'] < min_w:
        bot.answer_callback_query(call.id, "رصيدك غير كافٍ للسحب حالياً.", show_alert=True)
        return

    msg = bot.send_message(
        call.message.chat.id,
        "يرجى إرسال بيانات السحب بالصيغة التالية:\n\n"
        "<code>المتعامل رقم_الهاتف المبلغ</code>\n\n"
        "أمثلة:\n"
        "• <code>Mobilis 0661234567 100</code>\n"
        "• <code>Djezzy 0771234567 200</code>\n"
        "• <code>Ooredoo 0551234567 150</code>"
    )
    bot.register_next_step_handler(msg, process_withdrawal_input)
    bot.answer_callback_query(call.id)

def process_withdrawal_input(message):
    try:
        parts = message.text.strip().split()
        if len(parts) != 3:
            raise ValueError()

        operator = parts[0].upper()
        phone = parts[1]
        amount = float(parts[2])

        if operator not in VALID_OPERATORS:
            bot.send_message(message.chat.id, "❌ متعامل غير معروف! يرجى اختيار (Mobilis, Djezzy, أو Ooredoo).")
            return

        min_w = float(get_setting("min_withdrawal"))
        if amount < min_w:
            bot.send_message(message.chat.id, f"❌ المبلغ المطلوب أقل من الحد الأدنى للسحب ({min_w} دج).")
            return

        w_id = create_withdrawal(message.from_user.id, amount, phone, operator)
        if not w_id:
            bot.send_message(message.chat.id, "❌ رصيدك الحالي لا يكفي لطلب هذا المبلغ.")
            return

        bot.send_message(
            message.chat.id,
            "✅ تم تقديم طلب السحب بنجاح وهو قيد المراجعة.\n"
            "تم خصم المبلغ ونقله للرصيد المعلق مؤقتاً."
        )

        admin_markup = InlineKeyboardMarkup()
        admin_markup.row(
            InlineKeyboardButton("تم الشحن (موافقة) ✅", callback_data=f"app_w_{w_id}"),
            InlineKeyboardButton("رفض (رقم خاطئ) ❌", callback_data=f"rej_w_{w_id}")
        )

        bot.send_message(
            ADMIN_ID,
            f"📱 <b>طلب سحب رصيد جديد (فليكسي)</b>:\n\n"
            f"• المستخدم: @{message.from_user.username or 'بدون_معرف'} (ID: <code>{message.from_user.id}</code>)\n"
            f"• المتعامل: <b>{operator}</b>\n"
            f"• الرقم: <code>{phone}</code>\n"
            f"• المبلغ: <b>{amount} دج</b>",
            reply_markup=admin_markup
        )

    except Exception:
        bot.send_message(message.chat.id, "❌ صيغة المدخلات غير صحيحة. يرجى المحاولة مجدداً والالتزام بالصيغة: <code>المتعامل رقم_الهاتف المبلغ</code>")

@bot.callback_query_handler(func=lambda call: call.data.startswith(("app_w_", "rej_w_")))
def handle_admin_withdrawal_review(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "غير مصرح لك القيام بهذا الإجراء.", show_alert=True)
        return

    parts = call.data.split("_")
    action = parts[0]
    w_id = int(parts[2])

    if action == "app":
        res = approve_withdrawal(w_id)
        if res:
            bot.edit_message_text(
                f"{call.message.text}\n\n✅ <b>تمت الموافقة وتأكيد شحن الفليكسي.</b>",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id
            )
            bot.send_message(res['user_id'], f"🎉 تم شحن رصيدك (فليكسي) بنجاح بمبلغ {res['amount']} دج!")
        else:
            bot.answer_callback_query(call.id, "الطلب غير موجود أو تم معالجته سابقاً.", show_alert=True)

    elif action == "rej":
        res = reject_withdrawal(w_id)
        if res:
            bot.edit_message_text(
                f"{call.message.text}\n\n❌ <b>تم رفض طلب السحب وإعادة الرصيد للمستخدم.</b>",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id
            )
            bot.send_message(res['user_id'], get_setting("msg_reject"))
        else:
            bot.answer_callback_query(call.id, "الطلب غير موجود أو تم معالجته سابقاً.", show_alert=True)

# --- أوامر التحكم للأدمن ---
@bot.message_handler(commands=['add_emails'])
def admin_add_emails(message):
    if not is_admin(message.from_user.id):
        return
    raw_text = message.text.replace("/add_emails", "").strip()
    if not raw_text:
        bot.reply_to(message, "الصيغة: <code>/add_emails e1@gmail.com, e2@gmail.com</code>")
        return
    count = batch_add_emails(raw_text.split(","))
    bot.reply_to(message, f"✅ تم إضافة {count} إيميل متاح بنجاح!")

@bot.message_handler(commands=['set_reward'])
def admin_set_reward(message):
    if not is_admin(message.from_user.id):
        return
    val = message.text.replace("/set_reward", "").strip()
    if not val or not val.isdigit():
        bot.reply_to(message, "الصيغة: <code>/set_reward 50</code>")
        return
    set_setting("reward_per_email", val)
    bot.reply_to(message, f"✅ تم تحديث مكافأة الإيميل إلى: <b>{val} دج</b>")

@bot.message_handler(commands=['set_min'])
def admin_set_min(message):
    if not is_admin(message.from_user.id):
        return
    val = message.text.replace("/set_min", "").strip()
    if not val or not val.isdigit():
        bot.reply_to(message, "الصيغة: <code>/set_min 100</code>")
        return
    set_setting("min_withdrawal", val)
    bot.reply_to(message, f"✅ تم تحديث الحد الأدنى للسحب إلى: <b>{val} دج</b>")

@bot.messag
