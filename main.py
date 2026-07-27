#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import sqlite3
import re
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

# ===================== الاعدادات =====================

BOT_TOKEN = "8758366357:AAGPz5MnqyZOjBeTtxPn1FdTM2HMLpDY3Ug"
ADMIN_ID = 6536672093
REQUEST_TIMEOUT = 300
DB_PATH = "emails.db"

# ===================== تسجيل الاخطاء =====================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===================== قاعدة البيانات =====================

class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        except Exception as e:
            logger.error(f"Database error: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_db(self):
        with self.get_connection() as conn:
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
            logger.info("Database initialized successfully")
    
    def add_email(self, first_name: str, last_name: str, email: str, password: str, balance: float) -> Tuple[bool, str]:
        with self.get_connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO emails (first_name, last_name, email, password, balance) VALUES (?, ?, ?, ?, ?)",
                    (first_name.strip(), last_name.strip(), email.strip(), password.strip(), balance)
                )
                conn.commit()
                return True, "Email added successfully"
            except sqlite3.IntegrityError:
                return False, "This email already exists"
            except Exception as e:
                logger.error(f"Error adding email: {e}")
                return False, f"Error: {str(e)}"
    
    def get_available_emails(self) -> List[Dict]:
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
        with self.get_connection() as conn:
            email = conn.execute(
                "SELECT * FROM emails WHERE id = ?",
                (email_id,)
            ).fetchone()
            return dict(email) if email else None
    
    def reserve_email(self, email_id: int, user_id: int) -> Tuple[bool, str]:
        with self.get_connection() as conn:
            email = conn.execute(
                "SELECT status, reserved_by FROM emails WHERE id = ?",
                (email_id,)
            ).fetchone()
            
            if not email:
                return False, "Email not found"
            
            if email['status'] != 'AVAILABLE':
                return False, "Email is not available"
            
            conn.execute('''
                UPDATE emails 
                SET status = 'PENDING', 
                    reserved_by = ?, 
                    reserved_at = CURRENT_TIMESTAMP 
                WHERE id = ? AND status = 'AVAILABLE'
            ''', (user_id, email_id))
            
            if conn.total_changes == 0:
                return False, "Failed to reserve email"
            
            conn.commit()
            return True, "Email reserved successfully"
    
    def approve_email(self, email_id: int, user_id: int) -> Tuple[bool, Any]:
        with self.get_connection() as conn:
            email = conn.execute(
                "SELECT * FROM emails WHERE id = ? AND status = 'PENDING'",
                (email_id,)
            ).fetchone()
            
            if not email:
                return False, "Email not found or not pending"
            
            conn.execute('''
                UPDATE emails 
                SET status = 'SOLD', 
                    sold_to = ?, 
                    sold_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            ''', (user_id, email_id))
            
            conn.execute('''
                INSERT INTO users (user_id, total_emails) 
                VALUES (?, 1) 
                ON CONFLICT(user_id) DO UPDATE 
                SET total_emails = total_emails + 1
            ''', (user_id,))
            
            conn.execute('''
                INSERT INTO transactions (email_id, user_id, action, details) 
                VALUES (?, ?, 'APPROVE', ?)
            ''', (email_id, user_id, f"Approved {email['email']}"))
            
            conn.commit()
            return True, dict(email)
    
    def reject_email(self, email_id: int) -> Tuple[bool, Any]:
        with self.get_connection() as conn:
            email = conn.execute(
                "SELECT * FROM emails WHERE id = ? AND status = 'PENDING'",
                (email_id,)
            ).fetchone()
            
            if not email:
                return False, "Email not found or not pending"
            
            conn.execute('''
                UPDATE emails 
                SET status = 'AVAILABLE', 
                    reserved_by = NULL, 
                    reserved_at = NULL 
                WHERE id = ?
            ''', (email_id,))
            
            conn.execute('''
                INSERT INTO transactions (email_id, action, details) 
                VALUES (?, 'REJECT', ?)
            ''', (email_id, f"Rejected {email['email']}"))
            
            conn.commit()
            return True, dict(email)
    
    def _clean_expired_reservations(self, conn):
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
        with self.get_connection() as conn:
            emails = conn.execute('''
                SELECT id, email, status, balance, first_name, last_name, 
                       reserved_by, sold_to, created_at 
                FROM emails 
                ORDER BY created_at DESC
            ''').fetchall()
            return [dict(row) for row in emails]
    
    def get_pending_emails(self) -> List[Dict]:
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
        with self.get_connection() as conn:
            emails = conn.execute(
                "SELECT * FROM emails WHERE sold_to = ? ORDER BY sold_at DESC",
                (user_id,)
            ).fetchall()
            return [dict(row) for row in emails]
    
    def delete_email(self, email_id: int) -> Tuple[bool, str]:
        with self.get_connection() as conn:
            email = conn.execute(
                "SELECT status FROM emails WHERE id = ?",
                (email_id,)
            ).fetchone()
            
            if not email:
                return False, "Email not found"
            
            if email['status'] == 'SOLD':
                return False, "Cannot delete sold email"
            
            conn.execute("DELETE FROM emails WHERE id = ?", (email_id,))
            conn.commit()
            return True, "Email deleted successfully"
    
    def get_stats(self) -> Dict:
        with self.get_connection() as conn:
            stats = {}
            
            result = conn.execute('''
                SELECT status, COUNT(*) as count 
                FROM emails 
                GROUP BY status
            ''').fetchall()
            
            for row in result:
                stats[row['status'].lower()] = row['count']
            
            total = conn.execute(
                "SELECT SUM(balance) as total FROM emails WHERE status = 'AVAILABLE'"
            ).fetchone()
            stats['total_balance'] = total['total'] or 0
            
            users = conn.execute("SELECT COUNT(*) as count FROM users").fetchone()
            stats['total_users'] = users['count']
            
            stats['total_emails'] = sum(stats.get(s, 0) for s in ['available', 'pending', 'sold'])
            
            return stats
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        with self.get_connection() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,)
            ).fetchone()
            return dict(user) if user else None
    
    def save_user(self, user_id: int, username: str = None, first_name: str = None, last_name: str = None):
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
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email.strip()) is not None

def format_email_message(email_data: Dict, include_password: bool = True) -> str:
    msg = f"Email: `{email_data['email']}`\n"
    msg += f"Name: {email_data['first_name']} {email_data['last_name']}\n"
    
    if include_password:
        msg += f"Password: `{email_data['password']}`\n"
    
    msg += f"Balance: {email_data['balance']}$"
    return msg

def format_stats_message(stats: Dict) -> str:
    msg = "Bot Statistics\n\n"
    msg += f"Available emails: {stats.get('available', 0)}\n"
    msg += f"Pending requests: {stats.get('pending', 0)}\n"
    msg += f"Sold emails: {stats.get('sold', 0)}\n"
    msg += f"Total balance: {stats.get('total_balance', 0):.2f}$\n"
    msg += f"Total users: {stats.get('total_users', 0)}"
    return msg

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

# ===================== الازرار =====================

def get_email_list_keyboard(emails: List[Dict]) -> InlineKeyboardMarkup:
    keyboard = []
    for email in emails:
        btn = InlineKeyboardButton(
            text=f"{email['email']} | {email['balance']}$",
            callback_data=f"request_{email['id']}"
        )
        keyboard.append([btn])
    
    keyboard.append([InlineKeyboardButton("Refresh", callback_data="refresh_list")])
    keyboard.append([InlineKeyboardButton("Main Menu", callback_data="main_menu")])
    return InlineKeyboardMarkup(keyboard)

def get_admin_actions_keyboard(email_id: int, user_id: int, email: Dict) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(
                "Approve",
                callback_data=f"admin_approve_{email_id}_{user_id}"
            ),
            InlineKeyboardButton(
                "Reject",
                callback_data=f"admin_reject_{email_id}_{user_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "View Details",
                callback_data=f"admin_details_{email_id}"
            )
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("View Emails", callback_data="list_emails")],
        [InlineKeyboardButton("My Emails", callback_data="my_emails")],
        [InlineKeyboardButton("Help", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton("All Emails", callback_data="admin_list_all")],
        [InlineKeyboardButton("Pending Requests", callback_data="admin_pending")],
        [InlineKeyboardButton("Add Email", callback_data="admin_add_email")],
        [InlineKeyboardButton("Delete Email", callback_data="admin_delete_email")],
        [InlineKeyboardButton("Clean Reservations", callback_data="admin_clean")],
        [InlineKeyboardButton("Main Menu", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_confirmation_keyboard(action: str, email_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("Yes", callback_data=f"confirm_{action}_{email_id}"),
            InlineKeyboardButton("No", callback_data="cancel")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard(callback_data: str = "back") -> InlineKeyboardMarkup:
    keyboard = [[InlineKeyboardButton("Back", callback_data=callback_data)]]
    return InlineKeyboardMarkup(keyboard)

# ===================== معالجات البوت =====================

WAITING_EMAIL_DATA, WAITING_EMAIL_DELETE = range(2)

class EmailBot:
    def __init__(self):
        self.db = Database()
        self.application = None
    
    def start(self):
        self.application = Application.builder().token(BOT_TOKEN).build()
        self._register_handlers()
        logger.info("Bot is starting...")
        self.application.run_polling()
    
    def _register_handlers(self):
        app = self.application
        
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CommandHandler("help", self.help_command))
        app.add_handler(CommandHandler("list", self.list_command))
        app.add_handler(CommandHandler("my", self.my_emails_command))
        app.add_handler(CommandHandler("admin", self.admin_command))
        app.add_handler(CommandHandler("stats", self.stats_command))
        
        app.add_handler(CallbackQueryHandler(self.handle_callback, pattern="^(?!confirm_)"))
        app.add_handler(CallbackQueryHandler(self.handle_confirm, pattern="^confirm_"))
        
        conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_add_email, pattern="^admin_add_email$")],
            states={
                WAITING_EMAIL_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_email_data)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_add_email)],
        )
        app.add_handler(conv_handler)
        
        delete_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.start_delete_email, pattern="^admin_delete_email$")],
            states={
                WAITING_EMAIL_DELETE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.receive_email_delete)],
            },
            fallbacks=[CommandHandler("cancel", self.cancel_delete_email)],
        )
        app.add_handler(delete_conv)
        
        app.add_error_handler(self.error_handler)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        self.db.save_user(user.id, user.username, user.first_name, user.last_name)
        
        welcome_msg = (
            f"Hello {user.first_name}!\n\n"
            "Email Management Bot\n\n"
            "Commands:\n"
            "/list - View available emails\n"
            "/my - View your emails\n"
            "/help - Help\n"
            "/admin - Admin panel"
        )
        
        await update.message.reply_text(
            welcome_msg,
            reply_markup=get_main_menu_keyboard()
        )
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_msg = (
            "Help\n\n"
            "How it works:\n"
            "1. Use /list to see available emails\n"
            "2. Select an email you want\n"
            "3. Wait for admin approval\n"
            "4. You will receive the email details\n\n"
            "Commands:\n"
            "/start - Start the bot\n"
            "/list - View available emails\n"
            "/my - View your emails\n"
            "/help - This message\n\n"
            "Note: Emails are reserved for 5 minutes"
        )
        
        await update.message.reply_text(
            help_msg,
            reply_markup=get_back_keyboard("main_menu")
        )
    
    async def list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.show_available_emails(update, context)
    
    async def my_emails_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        emails = self.db.get_user_emails(user_id)
        
        if not emails:
            msg = "You haven't received any emails yet"
        else:
            msg = "Your emails:\n\n"
            for i, email in enumerate(emails, 1):
                msg += f"{i}. {email['email']}\n"
                msg += f"   Name: {email['first_name']} {email['last_name']}\n"
                msg += f"   Balance: {email['balance']}$\n"
                if email.get('sold_at'):
                    msg += f"   Date: {email['sold_at'][:10]}\n"
                msg += "\n"
        
        await update.message.reply_text(
            msg,
            reply_markup=get_back_keyboard("main_menu")
        )
    
    async def admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("Unauthorized.")
            return
        
        await update.message.reply_text(
            "Admin Panel\n\nSelect an action:",
            reply_markup=get_admin_menu_keyboard()
        )
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("Unauthorized.")
            return
        
        stats = self.db.get_stats()
        await update.message.reply_text(
            format_stats_message(stats),
            reply_markup=get_back_keyboard("admin")
        )
    
    async def show_available_emails(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        emails = self.db.get_available_emails()
        
        if not emails:
            msg = "No emails available."
            reply_markup = get_back_keyboard("main_menu")
        else:
            msg = f"Available emails ({len(emails)}):\n\nSelect an email:"
            reply_markup = get_email_list_keyboard(emails)
        
        if hasattr(update, 'message') and update.message:
            await update.message.reply_text(msg, reply_markup=reply_markup)
        elif hasattr(update, 'callback_query'):
            await update.callback_query.edit_message_text(msg, reply_markup=reply_markup)
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        user_id = query.from_user.id
        
        user = query.from_user
        self.db.save_user(user.id, user.username, user.first_name, user.last_name)
        
        if data == "main_menu":
            await query.edit_message_text(
                "Main Menu\n\nSelect an option:",
                reply_markup=get_main_menu_keyboard()
            )
            return
        
        if data == "list_emails" or data == "refresh_list":
            await self.show_available_emails(update, context)
            return
        
        if data == "my_emails":
            emails = self.db.get_user_emails(user_id)
            
            if not emails:
                msg = "You haven't received any emails yet"
            else:
                msg = "Your emails:\n\n"
                for i, email in enumerate(emails, 1):
                    msg += f"{i}. {email['email']}\n"
                    msg += f"   Name: {email['first_name']} {email['last_name']}\n"
                    msg += f"   Balance: {email['balance']}$\n"
                    if email.get('sold_at'):
                        msg += f"   Date: {email['sold_at'][:10]}\n"
                    msg += "\n"
            
            await query.edit_message_text(
                msg,
                reply_markup=get_back_keyboard("main_menu")
            )
            return
        
        if data == "help":
            await query.edit_message_text(
                "Help\n\nHow it works:\n1. Use /list to see available emails\n2. Select an email you want\n3. Wait for admin approval\n4. You will receive the email details\n\nCommands:\n/start - Start the bot\n/list - View available emails\n/my - View your emails\n/help - This message",
                reply_markup=get_back_keyboard("main_menu")
            )
            return
        
        if data.startswith("request_"):
            await self.handle_email_request(update, context)
            return
        
        if not is_admin(user_id):
            await query.edit_message_text("Unauthorized.")
            return
        
        if data == "admin_stats":
            stats = self.db.get_stats()
            await query.edit_message_text(
                format_stats_message(stats),
                reply_markup=get_back_keyboard("admin")
            )
            return
        
        if data == "admin_list_all":
            emails = self.db.get_all_emails()
            if not emails:
                msg = "No emails found"
            else:
                msg = "All emails:\n\n"
                for e in emails[:20]:
                    status_map = {'AVAILABLE': 'Available', 'PENDING': 'Pending', 'SOLD': 'Sold'}
                    msg += f"{e['email']} - {status_map.get(e['status'], e['status'])}\n"
                    msg += f"   Balance: {e['balance']}$ | Name: {e['first_name']} {e['last_name']}\n\n"
                if len(emails) > 20:
                    msg += f"\n... and {len(emails) - 20} more emails"
            
            await query.edit_message_text(
                msg,
                reply_markup=get_back_keyboard("admin")
            )
            return
        
        if data == "admin_pending":
            emails = self.db.get_pending_emails()
            if not emails:
                msg = "No pending requests"
            else:
                msg = "Pending requests:\n\n"
                for e in emails:
                    msg += f"{e['email']}\n"
                    msg += f"User: {e.get('user_first_name', 'Unknown')} (ID: {e['reserved_by']})\n"
                    msg += f"Balance: {e['balance']}$\n"
                    msg += f"Time: {e['reserved_at'][:16] if e.get('reserved_at') else ''}\n\n"
            
            await query.edit_message_text(
                msg,
                reply_markup=get_back_keyboard("admin")
            )
            return
        
        if data == "admin_clean":
            self.db._clean_expired_reservations(self.db.get_connection().__enter__())
            await query.edit_message_text(
                "Expired reservations cleaned successfully.",
                reply_markup=get_back_keyboard("admin")
            )
            return
        
        if data == "admin_exit":
            await query.edit_message_text(
                "Exited admin panel.",
                reply_markup=get_main_menu_keyboard()
            )
            return
        
        if data.startswith("admin_"):
            await self.handle_admin_action(update, context)
            return
        
        if data == "back" or data == "admin":
            await query.edit_message_text(
                "Admin Panel\n\nSelect an action:",
                reply_markup=get_admin_menu_keyboard()
            )
            return
        
        if data == "cancel":
            await query.edit_message_text("Cancelled.", reply_markup=get_main_menu_keyboard())
            return
    
    async def handle_email_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        email_id = int(query.data.split('_')[1])
        
        success, message = self.db.reserve_email(email_id, user_id)
        
        if not success:
            await query.edit_message_text(
                f"{message}\n\nTry refreshing the list.",
                reply_markup=get_back_keyboard("list_emails")
            )
            return
        
        email = self.db.get_email_by_id(email_id)
        if not email:
            await query.edit_message_text("Error, please try again.")
            return
        
        await query.edit_message_text(
            f"Request sent to admin.\n\nEmail: {email['email']}\nBalance: {email['balance']}$\n\nPlease wait for admin approval."
        )
        
        user_info = {
            'first_name': query.from_user.first_name,
            'last_name': query.from_user.last_name,
            'user_id': user_id,
            'username': query.from_user.username
        }
        
        admin_msg = (
            f"New email request\n\n"
            f"Email: {email['email']}\n"
            f"Name: {email['first_name']} {email['last_name']}\n"
            f"Balance: {email['balance']}$\n\n"
            f"Requester: {user_info['first_name']} {user_info['last_name']}\n"
            f"ID: {user_id}\n"
            f"Username: @{user_info['username'] or 'Not set'}"
        )
        
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_msg,
            reply_markup=get_admin_actions_keyboard(email_id, user_id, email)
        )
    
    async def handle_admin_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        parts = query.data.split('_')
        
        if len(parts) < 3:
            await query.edit_message_text("Invalid action.")
            return
        
        action = parts[1]
        email_id = int(parts[2])
        user_id = int(parts[3]) if len(parts) > 3 else None
        
        if action == 'approve':
            success, result = self.db.approve_email(email_id, user_id)
            
            if success:
                email_data = result
                user_msg = (
                    f"Your request has been approved!\n\n"
                    f"Email: {email_data['email']}\n"
                    f"Password: {email_data['password']}\n"
                    f"Name: {email_data['first_name']} {email_data['last_name']}\n"
                    f"Balance: {email_data['balance']}$\n\n"
                    "Please change the password immediately."
                )
                
                await context.bot.send_message(chat_id=user_id, text=user_msg)
                await query.edit_message_text("Request approved and sent to user.")
            else:
                await query.edit_message_text(f"Error: {result}")
        
        elif action == 'reject':
            success, result = self.db.reject_email(email_id)
            
            if success:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="Your request has been rejected."
                )
                await query.edit_message_text("Request rejected.")
            else:
                await query.edit_message_text(f"Error: {result}")
        
        elif action == 'details':
            email = self.db.get_email_by_id(email_id)
            if email:
                msg = (
                    f"Email details:\n\n"
                    f"Email: {email['email']}\n"
                    f"Name: {email['first_name']} {email['last_name']}\n"
                    f"Password: {email['password']}\n"
                    f"Balance: {email['balance']}$\n"
                    f"Status: {email['status']}"
                )
                await query.edit_message_text(msg, reply_markup=get_back_keyboard("admin"))
            else:
                await query.edit_message_text("Email not found.", reply_markup=get_back_keyboard("admin"))
        
        else:
            await query.edit_message_text("Unknown action.")
    
    async def handle_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        parts = query.data.split('_')
        action = parts[1]
        email_id = int(parts[2])
        
        if action == 'delete':
            success, message = self.db.delete_email(email_id)
            await query.edit_message_text(message, reply_markup=get_back_keyboard("admin"))
    
    async def start_add_email(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        await query.edit_message_text(
            "Send email data in this format:\n\n"
            "first_name | last_name | email | password | balance\n\n"
            "Example:\n"
            "Ahmed | Mohamed | ahmed@example.com | Pass123 | 50\n\n"
            "Type /cancel to cancel."
        )
        return WAITING_EMAIL_DATA
    
    async def receive_email_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        parts = [p.strip() for p in text.split('|')]
        
        if len(parts) != 5:
            await update.message.reply_text(
                "Invalid format. Please use:\n"
                "first_name | last_name | email | password | balance\n\n"
                "Example:\n"
                "Ahmed | Mohamed | ahmed@example.com | Pass123 | 50"
            )
            return WAITING_EMAIL_DATA
        
        first_name, last_name, email, password, balance = parts
        
        try:
            balance = float(balance)
        except ValueError:
            await update.message.reply_text("Invalid balance amount. Please enter a number.")
            return WAITING_EMAIL_DATA
        
        if not validate_email(email):
            await update.message.reply_text("Invalid email format. Please try again.")
            return WAITING_EMAIL_DATA
        
        success, message = self.db.add_email(first_name, last_name, email, password, balance)
        
        if success:
            await update.message.reply_text(
                f"Email added successfully!\n\nEmail: {email}",
                reply_markup=get_back_keyboard("admin")
            )
        else:
            await update.message.reply_text(
                f"Failed to add email: {message}",
                reply_markup=get_back_keyboard("admin")
            )
        
        return ConversationHandler.END
    
    async def cancel_add_email(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Cancelled.", reply_markup=get_back_keyboard("admin"))
        return ConversationHandler.END
    
    async def start_delete_email(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        emails = self.db.get_all_emails()
        available_emails = [e for e in emails if e['status'] != 'SOLD']
        
        if not available_emails:
            await query.edit_message_text(
                "No emails available to delete.",
                reply_markup=get_back_keyboard("admin")
            )
            return ConversationHandler.END
        
        msg = "Available emails to delete:\n\n"
        for e in available_emails[:10]:
            msg += f"ID: {e['id']} - {e['email']} ({e['status']})\n"
        
        msg += "\nEnter the email ID to delete:\nType /cancel to cancel."
        
        await query.edit_message_text(msg)
        return WAITING_EMAIL_DELETE
    
    async def receive_email_delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            email_id = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("Please enter a valid ID number.")
            return WAITING_EMAIL_DELETE
        
        success, message = self.db.delete_email(email_id)
        await update.message.reply_text(
            message,
            reply_markup=get_back_keyboard("admin")
        )
        return ConversationHandler.END
    
    async def cancel_delete_email(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Cancelled.", reply_markup=get_back_keyboard("admin"))
        return ConversationHandler.END
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Update {update} caused error {context.error}")
        try:
            if update and update.effective_message:
                await update.effective_message.reply_text("An error occurred. Please try again later.")
        except:
            pass

# ===================== تشغيل البوت =====================

if __name__ == "__main__":
    try:
        bot = EmailBot()
        bot.start()
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        sys.exit(1)
