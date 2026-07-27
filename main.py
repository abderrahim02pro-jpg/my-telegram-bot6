🤖 بوت تيليجرام احترافي - كود واحد متكامل main.py

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
📧 بوت إدارة الإيميلات - النسخة الاحترافية
جميع الحقوق محفوظة © 2024
"""

import logging
import sqlite3
import re
import os
import sys
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Dict, List, Optional, Tuple, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# ===================== الإعدادات =====================

# 🔑 ضع توكن البوت هنا (من @BotFather)
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"

# 👤 ضع معرف الأدمن هنا (اذهب الى @userinfobot)
ADMIN_ID = 123456789

# ⏱️ مهلة الحجز بالثواني (5 دقائق)
REQUEST_TIMEOUT = 300

# 📂 مسار قاعدة البيانات
DB_PATH = "emails.db"

# ===================== تسجيل الأخطاء =====================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===================== قاعدة البيانات =====================

class Database:
    """التحكم في قاعدة البيانات"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    @contextmanager
    def get_connection(self):
        """مدير سياق للاتصال بقاعدة البيانات"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        except Exception as e:
            logger.error(f"خطأ في قاعدة البيانات: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_db(self):
        """إنشاء الجداول إذا لم تكن موجودة"""
        with self.get_connection() as conn:
            # جدول الإيميلات
            conn.execute('''
                CREATE TABLE IF NOT EXISTS emails (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    first_name TEXT NOT NULL,
                    last_name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    balance REAL NOT NULL,
                    status TEXT DEFAULT 'AVAILABLE',
                    reserved_by INTEGER DEFAULT NULL,
                    reserved_at TIMESTAMP DEFAULT NULL,
                    sold_to INTEGER DEFAULT NULL,
                    sold_at TIMESTAMP DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # جدول المستخدمين
            conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    total_emails INTEGER DEFAULT 0,
                    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # جدول المعاملات
            conn.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email_id INTEGER,
                    user_id INTEGER,
                    action TEXT,
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            logger.info("✅ تم تهيئة قاعدة البيانات بنجاح")
    
    # ========== دوال الإيميلات ==========
    
    def add_email(self, first_name: str, last_name: str, email: str, password: str, balance: float) -> Tuple[bool, str]:
        """إضافة إيميل جديد"""
        with self.get_connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO emails (first_name, last_name, email, password, balance) VALUES (?, ?, ?, ?, ?)",
                    (first_name.strip(), last_name.strip(), email.strip(), password.strip(), balance)
                )
                conn.commit()
                return True, "✅ تمت إضافة الإيميل بنجاح"
            except sqlite3.IntegrityError:
                return False, "❌ هذا الإيميل موجود مسبقاً"
            except Exception as e:
                logger.error(f"خطأ في إضافة الإيميل: {e}")
                return False, f"❌ خطأ: {str(e)}"
    
    def get_available_emails(self) -> List[Dict]:
        """جلب الإيميلات المتاحة"""
        with self.get_connection() as conn:
            self._clean_expired_reservations(conn)
            
            emails = conn.execute('''
                SELECT id, email, balance, first_name, last_name 
                FROM emails 
                WHERE status = 'AVAILABLE' 
                ORDER BY created_at ASC
            ''').fetchall()
            return [dict(row) for row in emails]
    
    def get_email_by_id(self, email_id: int) -> Optional[Dict]:
        """جلب إيميل بواسطة المعرف"""
        with self.get_connection() as conn:
            email = conn.execute(
                "SELECT * FROM emails WHERE id = ?",
                (email_id,)
            ).fetchone()
            return dict(email) if email else None
    
    def reserve_email(self, email_id: int, user_id: int) -> Tuple[bool, str]:
        """حجز إيميل مؤقتاً"""
        with self.get_connection() as conn:
            # التحقق من أن الإيميل متاح
            email = conn.execute(
                "SELECT status, reserved_by FROM emails WHERE id = ?",
                (email_id,)
            ).fetchone()
            
            if not email:
                return False, "❌ الإيميل غير موجود"
            
            if email['status'] != 'AVAILABLE':
                return False, "❌ الإيميل غير متاح حالياً"
            
            # تنفيذ الحجز
            conn.execute('''
                UPDATE emails 
                SET status = 'PENDING', 
                    reserved_by = ?, 
                    reserved_at = CURRENT_TIMESTAMP 
                WHERE id = ? AND status = 'AVAILABLE'
            ''', (user_id, email_id))
            
            if conn.total_changes == 0:
                return False, "❌ فشل الحجز، حاول مرة أخرى"
            
            conn.commit()
            return True, "✅ تم حجز الإيميل بنجاح"
    
    def approve_email(self, email_id: int, user_id: int) -> Tuple[bool, Any]:
        """الموافقة على طلب إيميل"""
        with self.get_connection() as conn:
            # جلب بيانات الإيميل
            email = conn.execute(
                "SELECT * FROM emails WHERE id = ? AND status = 'PENDING'",
                (email_id,)
            ).fetchone()
            
            if not email:
                return False, "❌ الإيميل غير موجود أو ليس بحالة معلقة"
            
            # تحديث الحالة إلى مباع
            conn.execute('''
                UPDATE emails 
                SET status = 'SOLD', 
                    sold_to = ?, 
                    sold_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            ''', (user_id, email_id))
            
            # تحديث عدد إيميلات المستخدم
            conn.execute('''
                INSERT INTO users (user_id, total_emails) 
                VALUES (?, 1) 
                ON CONFLICT(user_id) DO UPDATE 
                SET total_emails = total_emails + 1
            ''', (user_id,))
            
            # تسجيل المعاملة
            conn.execute('''
                INSERT INTO transactions (email_id, user_id, action, details) 
                VALUES (?, ?, 'APPROVE', ?)
            ''', (email_id, user_id, f"تمت الموافقة على {email['email']}"))
            
            conn.commit()
            return True, dict(email)
    
    def reject_email(self, email_id: int) -> Tuple[bool, Any]:
        """رفض طلب إيميل وإعادته للقائمة"""
        with self.get_connection() as conn:
            email = conn.execute(
                "SELECT * FROM emails WHERE id = ? AND status = 'PENDING'",
                (email_id,)
            ).fetchone()
            
            if not email:
                return False, "❌ الإيميل غير موجود أو ليس بحالة معلقة"
            
            conn.execute('''
                UPDATE emails 
                SET status = 'AVAILABLE', 
                    reserved_by = NULL, 
                    reserved_at = NULL 
                WHERE id = ?
            ''', (email_id,))
            
            # تسجيل المعاملة
            conn.execute('''
                INSERT INTO transactions (email_id, action, details) 
                VALUES (?, 'REJECT', ?)
            ''', (email_id, f"تم رفض {email['email']}"))
            
            conn.commit()
            return True, dict(email)
    
    def _clean_expired_reservations(self, conn):
        """تنظيف الحجوزات المنتهية"""
        expiry_time = datetime.now() - timedelta(seconds=REQUEST_TIMEOUT)
        
        conn.execute('''
            UPDATE emails 
            SET status = 'AVAILABLE', 
                reserved_by = NULL, 
                reserved_at = NULL 
            WHERE status = 'PENDING' 
            AND reserved_at < ?
        ''', (expiry_time,))
        conn.commit()
    
    def get_all_emails(self) -> List[Dict]:
        """جلب جميع الإيميلات"""
        with self.get_connection() as conn:
            emails = conn.execute('''
                SELECT id, email, status, balance, first_name, last_name, 
                       reserved_by, sold_to, created_at 
                FROM emails 
                ORDER BY created_at DESC
            ''').fetchall()
            return [dict(row) for row in emails]
    
    def get_pending_emails(self) -> List[Dict]:
        """جلب الإيميلات المعلقة مع معلومات المستخدم"""
        with self.get_connection() as conn:
            emails = conn.execute('''
                SELECT e.*, 
                       u.first_name as user_first_name, 
                       u.last_name as user_last_name,
                       u.username as user_username
                FROM emails e
                LEFT JOIN users u ON e.reserved_by = u.user_id
                WHERE e.status = 'PENDING'
                ORDER BY e.reserved_at ASC
            ''').fetchall()
            return [dict(row) for row in emails]
    
    def get_user_emails(self, user_id: int) -> List[Dict]:
        """جلب إيميلات المستخدم"""
        with self.get_connection() as conn:
            emails = conn.execute(
                "SELECT * FROM emails WHERE sold_to = ? ORDER BY sold_at DESC",
                (user_id,)
            ).fetchall()
            return [dict(row) for row in emails]
    
    def delete_email(self, email_id: int) -> Tuple[bool, str]:
        """حذف إيميل (فقط إذا كان متاحاً)"""
        with self.get_connection() as conn:
            email = conn.execute(
                "SELECT status FROM emails WHERE id = ?",
                (email_id,)
            ).fetchone()
            
            if not email:
                return False, "❌ الإيميل غير موجود"
            
            if email['status'] == 'SOLD':
                return False, "❌ لا يمكن حذف إيميل مباع"
            
            conn.execute("DELETE FROM emails WHERE id = ?", (email_id,))
            conn.commit()
            return True, "✅ تم حذف الإيميل بنجاح"
    
    def get_stats(self) -> Dict:
        """جلب الإحصائيات"""
        with self.get_connection() as conn:
            stats = {}
            
            # عدد الإيميلات حسب الحالة
            result = conn.execute('''
                SELECT status, COUNT(*) as count 
                FROM emails 
                GROUP BY status
            ''').fetchall()
            
            for row in result:
                stats[row['status'].lower()] = row['count']
            
            # إجمالي الرصيد
            total = conn.execute(
                "SELECT SUM(balance) as total FROM emails WHERE status = 'AVAILABLE'"
            ).fetchone()
            stats['total_balance'] = total['total'] or 0
            
            # عدد المستخدمين
            users = conn.execute("SELECT COUNT(*) as count FROM users").fetchone()
            stats['total_users'] = users['count']
            
            # إجمالي الإيميلات
            stats['total_emails'] = sum(stats.get(s, 0) for s in ['available', 'pending', 'sold'])
            
            return stats
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        """جلب معلومات المستخدم"""
        with self.get_connection() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,)
            ).fetchone()
            return dict(user) if user else None
    
    def save_user(self, user_id: int, username: str = None, first_name: str = None, last_name: str = None):
        """حفظ أو تحديث معلومات المستخدم"""
        with self.get_connection() as conn:
            conn.execute('''
                INSERT INTO users (user_id, username, first_name, last_name) 
                VALUES (?, ?, ?, ?) 
                ON CONFLICT(user_id) DO UPDATE 
                SET username = COALESCE(?, username),
                    first_name = COALESCE(?, first_name),
                    last_name = COALESCE(?, last_name)
            ''', (user_id, username, first_name, last_name, username, first_name, last_name))
            conn.commit()

# ===================== دوال مساعدة =====================

def validate_email(email: str) -> bool:
    """التحقق من صحة البريد الإلكتروني"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email.strip()) is not None

def format_email_message(email_data: Dict, include_password: bool = True) -> str:
    """تنسيق رسالة الإيميل"""
    msg = f"📧 **البريد:** `{email_data['email']}`\n"
    msg += f"👤 **الاسم:** {email_data['first_name']} {email_data['last_name']}\n"
    
    if include_password:
        msg += f"🔑 **كلمة السر:** `{email_data['password']}`\n"
    
    msg += f"💰 **الرصيد:** {email_data['balance']}$"
    return msg

def format_stats_message(stats: Dict) -> str:
    """تنسيق رسالة الإحصائيات"""
    msg = "📊 **إحصائيات البوت**\n\n"
    msg += f"📧 **إيميلات متاحة:** {stats.get('available', 0)}\n"
    msg += f"⏳ **طلبات معلقة:** {stats.get('pending', 0)}\n"
    msg += f"✅ **إيميلات مباعة:** {stats.get('sold', 0)}\n"
    msg += f"💰 **إجمالي الرصيد:** {stats.get('total_balance', 0):.2f}$\n"
    msg += f"👥 **عدد المستخدمين:** {stats.get('total_users', 0)}"
    return msg

def is_admin(user_id: int) -> bool:
    """التحقق من صلاحيات الأدمن"""
    return user_id == ADMIN_ID

# ===================== الأزرار التفاعلية =====================

def get_email_list_keyboard(emails: List[Dict]) -> InlineKeyboardMarkup:
    """إنشاء أزرار الإيميلات المتاحة"""
    keyboard = []
    for email in emails:
        btn = InlineKeyboardButton(
            text=f"📧 {email['email']} | 💰 {email['balance']}$",
            callback_data=f"request_{email['id']}"
        )
        keyboard.append([btn])
    
    keyboard.append([InlineKeyboardButton("🔄 تحديث", callback_data="refresh_list")])
    keyboard.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")])
    return InlineKeyboardMarkup(keyboard)

def get_admin_actions_keyboard(email_id: int, user_id: int, email: Dict) -> InlineKeyboardMarkup:
    """أزرار إجراءات الأدمن"""
    keyboard = [
        [
            InlineKeyboardButton(
                f"✅ موافقة",
                callback_data=f"admin_approve_{email_id}_{user_id}"
            ),
            InlineKeyboardButton(
                f"❌ رفض",
                callback_data=f"admin_reject_{email_id}_{user_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "📧 عرض التفاصيل",
                callback_data=f"admin_details_{email_id}"
            )
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """القائمة الرئيسية للمستخدم"""
    keyboard = [
        [InlineKeyboardButton("📋 عرض الإيميلات", callback_data="list_emails")],
        [InlineKeyboardButton("📊 إيميلاتي", callback_data="my_emails")],
        [InlineKeyboardButton("❓ المساعدة", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_menu_keyboard() -> InlineKeyboardMarkup:
    """قائمة الأدمن"""
    keyboard = [
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")],
        [InlineKeyboardButton("📧 جميع الإيميلات", callback_data="admin_list_all")],
        [InlineKeyboardButton("⏳ الطلبات المعلقة", callback_data="admin_pending")],
        [InlineKeyboardButton("➕ إضافة إيميل", callback_data="admin_add_email")],
        [InlineKeyboardButton("🗑️ حذف إيميل", callback_data="admin_delete_email")],
        [InlineKeyboardButton("🔄 تنظيف الحجوزات", callback_data="admin_clean")],
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_confirmation_keyboard(action: str, email_id: int) -> InlineKeyboardMarkup:
    """أزرار تأكيد للحذف"""
    keyboard = [
        [
            InlineKeyboardButton("✅ نعم", callback_data=f"confirm_{action}_{email_id}"),
            InlineKeyboardButton("❌ لا", callback_data="cancel")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard(callback_data: str = "back") -> InlineKeyboardMarkup:
    """زر العودة"""
    keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data=callback_data)]]
    return InlineKeyboardMarkup(keyboard)

# ===================== معالجات البوت =====================

# حالات المحادثة
(WAITING_EMAIL_DATA, WAITING_EMAIL_DELETE) = range(2)

class EmailBot:
    """البوت الرئيسي"""
    
    def __init__(self):
        self.db = Database()
        self.application = None
    
    def start(self):
        """تشغيل البوت"""
        # إنشاء التطبيق
        self.application = Application.builder().token(BOT_TOKEN).build()
        
        # تسجيل المعالجات
        self._register_handlers()
        
        # تشغيل البوت
        logger.info("🚀 بدء تشغيل البوت...")
        self.application.run_polling()
    
    def _register_handlers(self):
        """تسجيل جميع المعالجات"""
        app = self.application
        
        # أوامر المستخدمين
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CommandHandler("help", self.help_command))
        app.add_handler(CommandHandler("list", self.list_command))
        app.add_handler(CommandHandler("my", self.my_emails_command))
        
        # أوامر الأدمن
        app.add_handler(CommandHandler("admin", self.admin_command))
        app.add_handler(CommandHandler("stats", self.stats_command))
        
        # معالجة الأزرار
        app.add_handler(CallbackQueryHandler(self.handle_callback, pattern="^(?!confirm_)"))
        app.add_handler(CallbackQueryHandler(self.handle_confirm, pattern="^confirm_"))
        
        # محادثة إضافة الإيميل
        conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_add_email, pattern="^admin_add_email$")],
            states={
                WAITING_EMAIL_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_email_data)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_add_email)],
        )
        app.add_handler(conv_handler)
        
        # محادثة حذف الإيميل
        delete_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_delete_email, pattern="^admin_delete_email$")],
            states={
                WAITING_EMAIL_DELETE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_email_delete)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_delete_email)],
        )
        app.add_handler(delete_conv)
        
        # معالجة الأخطاء
        app.add_error_handler(self.error_handler)
    
    # ========== أوامر المستخدمين ==========
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """أمر /start"""
        user = update.effective_user
        self.db.save_user(user.id, user.username, user.first_name, user.last_name)
        
        welcome_msg = (
            f"👋 مرحباً {user.first_name}!\n\n"
            "📧 **بوت إدارة الإيميلات**\n\n"
            "يمكنك استخدام هذا البوت للحصول على إيميلات جاهزة برصيد سحب.\n\n"
            "📋 **الأوامر المتاحة:**\n"
            "/list - عرض الإيميلات المتاحة\n"
            "/my - عرض الإيميلات التي حصلت عليها\n"
            "/help - عرض المساعدة\n\n"
            "🔐 جميع الإيميلات مجهزة مسبقاً."
        )
        
        await update.message.reply_text(
            welcome_msg,
            reply_markup=get_main_menu_keyboard(),
            parse_mode='Markdown'
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """أمر /help"""
        help_msg = (
            "❓ **المساعدة**\n\n"
            "🔹 **كيف يعمل البوت؟**\n"
            "1. استخدم /list لعرض الإيميلات المتاحة\n"
            "2. اختر الإيميل الذي تريده\n"
            "3. انتظر موافقة الأدمن\n"
            "4. ستصل إليك بيانات الإيميل بعد الموافقة\n\n"
            "🔹 **الأوامر:**\n"
            "/start - بدء البوت\n"
            "/list - عرض الإيميلات\n"
            "/my - إيميلاتي\n"
            "/help - هذه الرسالة\n\n"
            "⏳ **ملاحظة:** يتم حجز الإيميل عند طلبه لمدة 5 دقائق"
        )
        
        await update.message.reply_text(
            help_msg,
            reply_markup=get_back_keyboard("main_menu"),
            parse_mode='Markdown'
        )
    
    async def list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """أمر /list - عرض الإيميلات المتاحة"""
        await self.show_available_emails(update, context)
    
    async def my_emails_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """أمر /my - عرض إيميلات المستخدم"""
        user_id = update.effective_user.id
        emails = self.db.get_user_emails(user_id)
        
        if not emails:
            msg = "📭 لم تحصل على أي إيميلات بعد"
        else:
            msg = "📧 **الإيميلات التي حصلت عليها**\n\n"
            for i, email in enumerate(emails, 1):
                msg += f"{i}. `{email['email']}`\n"
                msg += f"   👤 {email['first_name']} {email['last_name']}\n"
                msg += f"   💰 {email['balance']}$\n"
                if email.get('sold_at'):
                    msg += f"   📅 {email['sold_at'][:10]}\n"
                msg += "\n"
        
        await update.message.reply_text(
            msg,
            reply_markup=get_back_keyboard("main_menu"),
            parse_mode='Markdown'
        )
    
    # ========== أوامر الأدمن ==========
    
    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """أمر /admin - لوحة تحكم الأدمن"""
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ غير مصرح لك باستخدام هذا الأمر.")
            return
        
        await update.message.reply_text(
            "🔧 **لوحة تحكم الأدمن**\n\n"
            "اختر الإجراء المناسب من القائمة:",
            reply_markup=get_admin_menu_keyboard(),
            parse_mode='Markdown'
        )
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """أمر /stats - عرض الإحصائيات"""
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("⛔ غير مصرح لك.")
            return
        
        stats = self.db.get_stats()
        await update.message.reply_text(
            format_stats_message(stats),
            parse_mode='Markdown'
        )
    
    # ========== عرض الإيميلات المتاحة ==========
    
    async def show_available_emails(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """عرض الإيميلات المتاحة مع أزرار"""
        emails = self.db.get_available_emails()
        
        if not emails:
            msg = "📭 **لا توجد إيميلات متاحة حالياً.**\n\nيرجى المحاولة لاحقاً."
            reply_markup = get_back_keyboard("main_menu")
        else:
            msg = f"📋 **الإيميلات المتاحة** ({len(emails)})\n\nاختر الإيميل الذي تريده:"
            reply_markup = get_email_list_keyboard(emails)
        
        # التعامل مع الرسالة (أمر أو كولباك)
        if hasattr(update, 'message') and update.message:
            await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
        elif hasattr(update, 'callback_query'):
            await update.callback_query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    
    # ========== معالجة الأزرار ==========
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة جميع الأزرار"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = query.from_user.id
        
        # حفظ معلومات المستخدم
        user = query.from_user
        self.db.save_user(user.id, user.username, user.first_name, user.last_name)
        
        # القائمة الرئيسية
        if data == "main_menu":
            await query.edit_message_text(
                "🏠 **القائمة الرئيسية**\n\nاختر أحد الخيارات:",
                reply_markup=get_main_menu_keyboard(),
                parse_mode='Markdown'
            )
            return
        
        # عرض الإيميلات
        if data == "list_emails" or data == "refresh_list":
            await self.show_available_emails(update, context)
            return
        
        # إيميلاتي
        if data == "my_emails":
            emails = self.db.get_user_emails(user_id)
            
            if not emails:
                msg = "📭 لم تحصل على أي إيميلات بعد"
            else:
                msg = "📧 **الإيميلات التي حصلت عليها**\n\n"
                for i, email in enumerate(emails, 1):
                    msg += f"{i}. `{email['email']}`\n"
                    msg += f"   👤 {email['first_name']} {email['last_name']}\n"
                    msg += f"   💰 {email['balance']}$\n"
                    if email.get('sold_at'):
                        msg += f"   📅 {email['sold_at'][:10]}\n"
                    msg += "\n"
            
            await query.edit_message_text(
                msg,
                reply_markup=get_back_keyboard("main_menu"),
                parse_mode='Markdown'
            )
            return
        
        # المساعدة
        if data == "help":
            await query.edit_message_text(
                "❓ **المساعدة**\n\n"
                "🔹 **كيف يعمل البوت؟**\n"
                "1. استخدم /list لعرض الإيميلات المتاحة\n"
                "2. اختر الإيميل الذي تريده\n"
                "3. انتظر موافقة الأدمن\n"
                "4. ستصل إليك بيانات الإيميل بعد الموافقة\n\n"
                "🔹 **الأوامر:**\n"
                "/start - بدء البوت\n"
                "/list - عرض الإيميلات\n"
                "/my - إيميلاتي\n"
                "/help - هذه الرسالة\n\n"
                "⏳ **ملاحظة:** يتم حجز الإيميل عند طلبه لمدة 5 دقائق",
                reply_markup=get_back_keyboard("main_menu"),
                parse_mode='Markdown'
            )
            return
        
        # طلب إيميل
        if data.startswith("request_"):
            await self.handle_email_request(update, context)
            return
        
        # ===== إجراءات الأدمن =====
        if not is_admin(user_id):
            await query.edit_message_text("⛔ غير مصرح لك.")
            return
        
        # لوحة الأدمن
        if data == "admin_stats":
            stats = self.db.get_stats()
            await query.edit_message_text(
                format_stats_message(stats),
                reply_markup=get_back_keyboard("admin"),
                parse_mode='Markdown'
            )
            return
        
        if data == "admin_list_all":
            emails = self.db.get_all_emails()
            if not emails:
                msg = "📭 لا توجد إيميلات"
            else:
                msg = "📊 **جميع الإيميلات**\n\n"
                for e in emails[:20]:  # عرض أول 20 فقط
                    status_map = {'AVAILABLE': '✅ متاحة', 'PENDING': '⏳ معلقة', 'SOLD': '❌ مباعة'}
                    msg += f"📧 `{e['email']}` - {status_map.get(e['status'], e['status'])}\n"
                    msg += f"   💰 {e['balance']}$ | 👤 {e['first_name']} {e['last_name']}\n\n"
                if len(emails) > 20:
                    msg += f"\n... وعرض {len(emails) - 20} إيميل آخر"
            
            await query.edit_message_text(
                msg,
                reply_markup=get_back_keyboard("admin"),
                parse_mode='Markdown'
            )
            return
        
        if data == "admin_pending":
            emails = self.db.get_pending_emails()
            if not emails:
                msg = "📭 لا توجد طلبات معلقة"
            else:
                msg = "⏳ **الطلبات المعلقة**\n\n"
                for e in emails:
                    msg += f"📧 `{e['email']}`\n"
                    msg += f"👤 {e.get('user_first_name', 'مستخدم')} (ID: {e['reserved_by']})\n"
                    msg += f"💰 {e['balance']}$\n"
                    msg += f"⏰ {e['reserved_at'][:16] if e.get('reserved_at') else ''}\n\n"
            
            await query.edit_message_text(
                msg,
                reply_markup=get_back_keyboard("admin"),
                parse_mode='Markdown'
            )
            return
        
        if data == "admin_clean":
            self.db.release_expired_reservations()
            await query.edit_message_text(
                "✅ تم تنظيف الحجوزات المنتهية بنجاح.",
                reply_markup=get_back_keyboard("admin")
            )
            return
        
        if data == "admin_exit":
            await query.edit_message_text(
                "👋 تم الخروج من لوحة التحكم.",
                reply_markup=get_main_menu_keyboard()
            )
            return
        
        # معالجة قرارات الأدمن
        if data.startswith("admin_"):
            await self.handle_admin_action(update, context)
            return
        
        # الرجوع
        if data == "back" or data == "admin":
            await query.edit_message_text(
                "🔧 **لوحة تحكم الأدمن**\n\nاختر الإجراء المناسب:",
                reply_markup=get_admin_menu_keyboard(),
                parse_mode='Markdown'
            )
            return
        
        if data == "cancel":
            await query.edit_message_text("❌ تم الإلغاء.", reply_markup=get_main_menu_keyboard())
            return
    
    async def handle_email_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة طلب إيميل من المستخدم"""
        query = update.callback_query
        user_id = query.from_user.id
        email_id = int(query.data.split('_')[1])
        
        # محاولة حجز الإيميل
        success, message = self.db.reserve_email(email_id, user_id)
        
        if not success:
            await query.edit_message_text(
                f"{message}\n\n🔄 حاول تحديث القائمة.",
                reply_markup=get_back_keyboard("list_emails")
            )
            return
        
        # جلب بيانات الإيميل
        email = self.db.get_email_by_id(email_id)
        if not email:
            await query.edit_message_text("❌ حدث خطأ، حاول مرة أخرى.")
            return
        
        # إعلام المستخدم
        await query.edit_message_text(
            "✅ **تم إرسال طلبك إلى الأدمن**\n\n"
            f"📧 الإيميل: `{email['email']}`\n"
            f"💰 الرصيد: {email['balance']}$\n\n"
            "⏳ يرجى الانتظار حتى موافقة الأدمن.\n"
            "📌 سيتم إعلامك عند الرد.",
            parse_mode='Markdown'
        )
        
        # إرسال إشعار للأدمن
        user_info = {
            'first_name': query.from_user.first_name,
            'last_name': query.from_user.last_name,
            'user_id': user_id,
            'username': query.from_user.username
        }
        
        admin_msg = (
            "📩 **طلب إيميل جديد**\n\n"
            f"📧 **الإيميل:** `{email['email']}`\n"
            f"👤 **الاسم:** {email['first_name']} {email['last_name']}\n"
            f"💰 **الرصيد:** {email['balance']}$\n\n"
            f"👤 **مقدم الطلب:** {user_info['first_name']} {user_info['last_name']}\n"
            f"🆔 **المعرف:** `{user_id}`\n"
            f"📝 **اليوزر:** @{user_info['username'] or 'غير موجود'}"
        )
        
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_msg,
            reply_markup=get_admin_actions_keyboard(email_id, user_id, email),
            parse_mode='Markdown'
        )
    
    async def handle_admin_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """معالجة إجراءات الأدمن (موافقة/رفض)"""
        query = update.callback_query
        data = query.data.split('_')
