import os
import sqlite3
from threading import Thread
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = "8758366357:AAGPz5MnqyZOjBeTtxPn1FdTM2HMLpDY3Ug"
ADMIN_ID = 6536672093

bot = telebot.TeleBot(BOT_TOKEN)

DB_NAME = "bot_database.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            balance REAL DEFAULT 0.0,
            status TEXT DEFAULT 'AVAILABLE',
            assigned_user_id INTEGER DEFAULT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS withdrawal_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            email_id INTEGER NOT NULL,
            status TEXT DEFAULT 'PENDING'
        )
    """)
    conn.commit()
    conn.close()

init_db()

def add_full_email_db(first_name, last_name, email, password, balance):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO emails (first_name, last_name, email, password, balance, status)
            VALUES (?, ?, ?, ?, ?, 'AVAILABLE')
        """, (first_name, last_name, email, password, balance))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False

def get_available_emails_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, first_name, last_name, email, balance FROM emails WHERE status = 'AVAILABLE'")
    rows = cursor.fetchall()
    conn.close()
    return rows

def request_email_withdraw(user_id, email_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE emails SET status = 'PENDING', assigned_user_id = ? WHERE id = ? AND status = 'AVAILABLE'", (user_id, email_id))
    if cursor.rowcount > 0:
        cursor.execute("INSERT INTO withdrawal_requests (user_id, email_id) VALUES (?, ?)", (user_id, email_id))
        req_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return req_id
    conn.close()
    return None

def approve_withdrawal_db(req_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, email_id FROM withdrawal_requests WHERE id = ?", (req_id,))
    row = cursor.fetchone()
    if row:
        user_id, email_id = row
        cursor.execute("UPDATE emails SET status = 'SOLD' WHERE id = ?", (email_id,))
        cursor.execute("UPDATE withdrawal_requests SET status = 'APPROVED' WHERE id = ?", (req_id,))
        conn.commit()
        conn.close()
        return user_id, email_id
    conn.close()
    return None, None

def reject_withdrawal_db(req_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, email_id FROM withdrawal_requests WHERE id = ?", (req_id,))
    row = cursor.fetchone()
    if row:
        user_id, email_id = row
        cursor.execute("UPDATE emails SET status = 'AVAILABLE', assigned_user_id = NULL WHERE id = ?", (email_id,))
        cursor.execute("UPDATE withdrawal_requests SET status = 'REJECTED' WHERE id = ?", (req_id,))
        conn.commit()
        conn.close()
        return user_id, email_id
    conn.close()
    return None, None

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running 24/7!"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def admin_panel_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("➕ إضافة إيميل جديد", callback_data="admin_add_full_email"),
        InlineKeyboardButton("📋 عرض الإيميلات المتاحة", callback_data="admin_list_emails")
    )
    return markup

@bot.message_handler(commands=['start', 'admin'])
def start_cmd(message):
    if message.from_user.id == ADMIN_ID:
        bot.reply_to(message, "⚙️ **لوحة تحكم الأدمن:**\nاختر العملية المطلوبة:", reply_markup=admin_panel_keyboard(), parse_mode="Markdown")
    else:
        show_user_emails(message.chat.id)

def show_user_emails(chat_id):
    emails = get_available_emails_db()
    if not emails:
        bot.send_message(chat_id, "📭 لا توجد إيميلات متاحة حالياً للسحب.")
        return
    
    markup = InlineKeyboardMarkup()
    for e in emails:
        btn_text = f"📧 {e[3]} | الرصيد: {e[4]}$"
        markup.row(InlineKeyboardButton(btn_text, callback_data=f"userclaim_{e[0]}"))
        
    bot.send_message(chat_id, "🎁 **الإيميلات المتاحة للسحب:**\nاضغط على الإيميل الذي تريد طلبه:", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    if call.data == "admin_add_full_email" and call.from_user.id == ADMIN_ID:
        msg = bot.send_message(
            call.message.chat.id, 
            "✏️ أرسل بيانات الإيميل مفصولة بكلمة **`|`** بالنظام التالي:\n\n"
            "`الاسم | اللقب | الإيميل | كلمة السر | رصيد السحب`\n\n"
            "*مثال:*\n`محمد | أحمد | test@gmail.com | pass123 | 50`",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, process_add_full_email)

    elif call.data == "admin_list_emails" and call.from_user.id == ADMIN_ID:
        emails = get_available_emails_db()
        if not emails:
            bot.send_message(call.message.chat.id, "📭 لا توجد إيميلات متاحة.")
        else:
            txt = "📋 **قائمة الإيميلات المتاحة:**\n\n"
            for e in emails:
                txt += f"• **الاسم:** {e[1]} {e[2]}\n  **الإيميل:** `{e[3]}`\n  **الرصيد:** {e[4]}$\n\n"
            bot.send_message(call.message.chat.id, txt, parse_mode="Markdown")

    elif call.data.startswith("userclaim_"):
        email_id = int(call.data.split("_")[1])
        req_id = request_email_withdraw(call.from_user.id, email_id)
        
        if req_id:
            bot.answer_callback_query(call.id, "تم إرسال طلبك للأدمن للموافقة ⏳", show_alert=True)
            bot.send_message(call.message.chat.id, "⏳ **تم تقديم طلبك بنجاح.**\nسيرسل لك البوت البيانات فور موافقة الأدمن.")
            
            admin_markup = InlineKeyboardMarkup()
            admin_markup.row(
                InlineKeyboardButton("✅ موافقة", callback_data=f"admapp_{req_id}"),
                InlineKeyboardButton("❌ رفض وإرجاع", callback_data=f"admrej_{req_id}")
            )
            bot.send_message(
                ADMIN_ID,
                f"🔔 **طلب سحب جديد!**\n\n"
                f"👤 **المستخدم:** [{call.from_user.first_name}](tg://user?id={call.from_user.id})\n"
                f"🆔 **المعرف:** `{call.from_user.id}`",
                reply_markup=admin_markup,
                parse_mode="Markdown"
            )
        else:
            bot.answer_callback_query(call.id, "❌ عفواً، هذا الإيميل قيد المراجعة أو لم يعد متاحاً!", show_alert=True)

    elif call.data.startswith("admapp_") and call.from_user.id == ADMIN_ID:
        req_id = int(call.data.split("_")[1])
        user_id, email_id = approve_withdrawal_db(req_id)
        if user_id:
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("SELECT first_name, last_name, email, password, balance FROM emails WHERE id = ?", (email_id,))
            item = c.fetchone()
            conn.close()
            
            bot.send_message(
                user_id,
                f"🎉 **تمت الموافقة على طلبك!**\n\n"
                f"👤 **الاسم الكامل:** {item[0]} {item[1]}\n"
                f"📧 **الإيميل:** `{item[2]}`\n"
                f"🔑 **كلمة السر:** `{item[3]}`\n"
                f"💰 **رصيد السحب:** {item[4]}$",
                parse_mode="Markdown"
            )
            bot.edit_message_text("✅ تم قبول الطلب وإرسال البيانات للمستخدم وخروج الإيميل من قائمة المتاح.", call.message.chat.id, call.message.message_id)

    elif call.data.startswith("admrej_") and call.from_user.id == ADMIN_ID:
        req_id = int(call.data.split("_")[1])
        user_id, email_id = reject_withdrawal_db(req_id)
        if user_id:
            bot.send_message(user_id, "❌ **عذراً، تم رفض طلب السحب الخاص بك من قبل الأدمن.**")
            bot.edit_message_text("❌ تم رفض الطلب وإعادة الإيميل للقائمة المتاحة تلقائياً.", call.message.chat.id, call.message.message_id)

def process_add_full_email(message):
    try:
        data = [item.strip() for item in message.text.split("|")]
        if len(data) != 5:
            bot.reply_to(message, "❌ صيغة البيانات غير صحيحة. يرجى التأكد من فصل العناصر الخمسة بـ `|`", parse_mode="Markdown")
            return
        
        first_name, last_name, email, password, balance = data[0], data[1], data[2], data[3], float(data[4])
        
        if add_full_email_db(first_name, last_name, email, password, balance):
            bot.reply_to(message, f"✅ **تم إضافة الإيميل بنجاح!**\n• الإيميل: `{email}`\n• الرصيد: {balance}$", parse_mode="Markdown", reply_markup=admin_panel_keyboard())
        else:
            bot.reply_to(message, "⚠️ هذا الإيميل مسجل مسبقاً!", reply_markup=admin_panel_keyboard())
    except Exception as e:
        bot.reply_to(message, f"❌ حدث خطأ في البيانات: {e}")

if __name__ == "__main__":
    print("Bot is starting...")
    Thread(target=run_flask).start()
    bot.infinity_polling(skip_pending=True)
