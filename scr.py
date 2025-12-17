import re
import os
import asyncio
import logging
import aiofiles
import time
from urllib.parse import urlparse
from html import escape
from datetime import datetime

# Import from your main bot
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# Configuration from your main bot
from config import API_ID, API_HASH, ADMIN_LIMIT, ADMIN_IDS, DEFAULT_LIMIT

# Pyrogram setup for scraper
from pyrogram import Client as PyroClient
from pyrogram.errors import PeerIdInvalid, ChannelInvalid, ChannelPrivate, UsernameNotOccupied, UsernameInvalid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Pyrogram client for scraping
pyro_user = None

# Cooldown tracking
user_last_command = {}

async def initialize_scraper():
    """Initialize Pyrogram client for scraping with FIX for PeerIdInvalid error"""
    global pyro_user
    try:
        # Check if credentials are available
        if not API_ID or not API_HASH:
            logger.warning("API_ID or API_HASH not found in config. Scraper disabled.")
            return False
            
        # FIRST: Clear old session files to prevent cache issues
        try:
            session_file = "scraper_session.session"
            if os.path.exists(session_file):
                os.remove(session_file)
                logger.info("Cleared old Pyrogram session file to fix cache issues")
        except:
            pass
            
        # Create client with NO_UPDATES to prevent automatic cache loading
        pyro_user = PyroClient(
            name="scraper_session",
            api_id=API_ID,
            api_hash=API_HASH,
            workers=1000,
            no_updates=True,  # CRITICAL: Disable automatic updates
            sleep_threshold=0,
            max_concurrent_transmissions=1
        )
        
        # Start the client WITHOUT loading dialogs
        await pyro_user.start()
        me = await pyro_user.get_me()
        logger.info(f"✅ Scraper client started successfully as: {me.first_name}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to start scraper client: {e}")
        return False

# ===== COOLDOWN SYSTEM =====
async def enforce_cooldown(user_id: int, update: Update, cooldown_seconds: int = 10) -> bool:
    """Enforce cooldown between commands"""
    current_time = time.time()
    last_command_time = user_last_command.get(user_id, 0)
    
    if current_time - last_command_time < cooldown_seconds:
        remaining = cooldown_seconds - (current_time - last_command_time)
        await update.message.reply_text(
            f"⏳ Please wait {int(remaining)} seconds before using this command again.",
            parse_mode=ParseMode.HTML
        )
        return False
    
    user_last_command[user_id] = current_time
    return True

# ===== DATABASE FUNCTIONS =====
async def get_user(user_id):
    """Get user data from database"""
    try:
        from db import get_user as db_get_user
        return await db_get_user(user_id)
    except Exception as e:
        logger.error(f"Error getting user data: {e}")
        return None

async def update_user(user_id, **kwargs):
    """Update user data in database"""
    try:
        from db import update_user as db_update_user
        await db_update_user(user_id, **kwargs)
    except Exception as e:
        logger.error(f"Error updating user data: {e}")

async def consume_credit(user_id: int) -> bool:
    """Consume 1 credit from user"""
    user_data = await get_user(user_id)
    if user_data and user_data.get('credits', 0) > 0:
        new_credits = user_data['credits'] - 1
        await update_user(user_id, credits=new_credits)
        return True
    return False

# ===== IMPROVED CHAT RESOLUTION - GROUPS & CHANNELS =====
async def resolve_chat_safe(client, chat_identifier):
    """Safely resolve chat for BOTH groups and channels"""
    try:
        # Clean the identifier
        original_identifier = chat_identifier
        
        # Remove URL prefixes
        if chat_identifier.startswith("https://t.me/"):
            chat_identifier = chat_identifier[13:]
        elif chat_identifier.startswith("t.me/"):
            chat_identifier = chat_identifier[5:]
        
        # Handle different types of identifiers
        
        # 1. Numeric ID (Group or Channel)
        if chat_identifier.lstrip("-").isdigit():
            chat_id = int(chat_identifier)
            try:
                chat = await client.get_chat(chat_id)
                return chat, None
            except (PeerIdInvalid, ChannelInvalid, ChannelPrivate):
                return None, "Bot is not a member of this chat. Add bot to group/channel first."
        
        # 2. Username (@username)
        elif chat_identifier.startswith("@"):
            try:
                chat = await client.get_chat(chat_identifier)
                return chat, None
            except UsernameNotOccupied:
                return None, f"Username {chat_identifier} does not exist."
            except (ChannelPrivate, ChannelInvalid):
                return None, f"Chat {chat_identifier} is private or bot not a member."
        
        # 3. Group invite link (t.me/+xxxxxxxxx)
        elif chat_identifier.startswith("+") or "joinchat" in chat_identifier:
            try:
                # Join the group/channel
                await client.join_chat(original_identifier)
                chat = await client.get_chat(original_identifier)
                return chat, None
            except Exception as e:
                error_msg = str(e)
                if "already" in error_msg.lower():
                    try:
                        chat = await client.get_chat(original_identifier)
                        return chat, None
                    except:
                        return None, "Bot is already a member but cannot access chat."
                elif "invite" in error_msg.lower() and "expired" in error_msg.lower():
                    return None, "Invite link has expired."
                else:
                    return None, f"Cannot join chat: {error_msg}"
        
        # 4. Regular username without @
        else:
            # Try with @ prefix
            try:
                chat = await client.get_chat(f"@{chat_identifier}")
                return chat, None
            except UsernameNotOccupied:
                # Try as invite link
                try:
                    await client.join_chat(f"https://t.me/{chat_identifier}")
                    chat = await client.get_chat(chat_identifier)
                    return chat, None
                except:
                    return None, f"Chat '{chat_identifier}' not found. Use @username or full invite link."
            except (ChannelPrivate, ChannelInvalid):
                return None, f"Chat '{chat_identifier}' is private. Bot needs to be added as member."
                
    except Exception as e:
        logger.error(f"Error resolving chat {chat_identifier}: {e}")
        return None, f"Error: {str(e)}"

# ===== SCRAPING FUNCTION FOR BOTH GROUPS & CHANNELS =====
async def scrape_messages(client, chat_id, limit, start_number=None, bank_name=None):
    """Scrape credit cards from Telegram chat (group or channel)"""
    messages = []
    count = 0
    pattern = r'\d{16}\D*\d{2}\D*\d{2,4}\D*\d{3,4}'

    logger.info(f"Starting to scrape messages from chat {chat_id} with limit {limit}")

    try:
        # Use search_messages for both groups and channels
        async for message in client.search_messages(chat_id=chat_id, limit=limit):
            if count >= limit:
                break
                
            text = message.text or message.caption
            if text:
                # Bank name filter
                if bank_name and bank_name.lower() not in text.lower():
                    continue
                
                matched_messages = re.findall(pattern, text)
                if matched_messages:
                    formatted_messages = []
                    for matched_message in matched_messages:
                        extracted_values = re.findall(r'\d+', matched_message)
                        if len(extracted_values) == 4:
                            card_number, mo, year, cvv = extracted_values
                            year = year[-2:]  # Convert to 2-digit year
                            
                            # BIN filter
                            if start_number:
                                if card_number.startswith(start_number[:6]):
                                    formatted_messages.append(f"{card_number}|{mo}|{year}|{cvv}")
                            else:
                                formatted_messages.append(f"{card_number}|{mo}|{year}|{cvv}")
                    
                    messages.extend(formatted_messages)
                    count += len(formatted_messages)
                    
    except Exception as e:
        logger.error(f"Error scraping from {chat_id}: {e}")
    
    logger.info(f"Scraped {len(messages)} messages from chat {chat_id}")
    return messages[:limit]

def remove_duplicates(messages):
    """Remove duplicate cards from the list"""
    unique_messages = list(set(messages))
    duplicates_removed = len(messages) - len(unique_messages)
    logger.info(f"Removed {duplicates_removed} duplicates")
    return unique_messages, duplicates_removed

# ===== SEND RESULTS FUNCTION =====
async def send_scraped_results(update: Update, unique_messages, duplicates_removed, source_name, bin_filter=None, bank_filter=None):
    """Send scraped results as a file"""
    if unique_messages:
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"BlackXCard_Scraper_x{len(unique_messages)}_{source_name.replace(' ', '_')}_{timestamp}.txt"
            
            async with aiofiles.open(file_name, mode='w') as f:
                await f.write("\n".join(unique_messages))

            user = update.effective_user
            user_link = f'<a href="tg://user?id={user.id}">{escape(user.first_name or "User")}</a>'
            
            # Build caption
            caption = (
                f"💎 𝘽𝙡𝙖𝙘𝙠 𝙓 𝘾𝙖𝙧𝙙 𝘾𝙘 𝙎𝙘𝙧𝙚𝙥𝙚𝙧 💸\n\n"
                f"☑️ <b>𝙎𝙘𝙧𝙖𝙥𝙥𝙚𝙙 𝙎𝙐𝘾𝘾𝙀𝙎𝙎𝙁𝙐𝙇𝙇𝙔</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━  \n"
                f"🧠 <b>𝙎𝙤𝙪𝙧𝙘𝙚 ↭</b> <code>{escape(source_name)}</code>\n"
                f"💀 <b> 𝙏𝙤𝙩𝙖𝙡 𝘾𝙘 ↭</b> <code>{len(unique_messages)}</code>\n"
                f"☠️ <b> 𝘿𝙪𝙥𝙡𝙞𝙘𝙖𝙩𝙚 𝙍𝙚𝙢𝙤𝙫𝙚𝙙 ↭</b> <code>{duplicates_removed}</code>\n"
            )
            
            # Add filters info if applied
            if bin_filter:
                caption += f"🔍 <b>𝐁𝐈𝐍 𝐅𝐈𝐋𝐓𝐄𝐑:</b> <code>{bin_filter}</code>\n"
            if bank_filter:
                caption += f"🏦 <b>𝐁𝐀𝐍𝐊 𝐅𝐈𝐋𝐓𝐄𝐑:</b> <code>{escape(bank_filter)}</code>\n"
            
            caption += (
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💎 <b>𝙎𝙘𝙧𝙖𝙥𝙥𝙚𝙙 𝘽𝙮 ⇾</b> {user_link}\n"
            )

            await update.effective_message.reply_document(
                document=open(file_name, 'rb'),
                filename=file_name,
                caption=caption,
                parse_mode=ParseMode.HTML
            )

            logger.info(f"Results sent successfully for {source_name}")
            
        except Exception as e:
            logger.error(f"Error sending results: {e}")
            await update.effective_message.reply_text(
                f"<b>❌ 𝐄𝐑𝐑𝐎𝐑 𝐒𝐄𝐍𝐃𝐈𝐍𝐆 𝐅𝐈𝐋𝐄:</b> <code>{escape(str(e))}</code>",
                parse_mode=ParseMode.HTML
            )
        finally:
            # Cleanup file
            try:
                if os.path.exists(file_name):
                    os.remove(file_name)
            except Exception as e:
                logger.error(f"Error removing file: {e}")
    else:
        await update.effective_message.reply_text(
            "<b>❌ 𝙉𝙤 𝘾𝙘 𝙁𝙤𝙪𝙣𝙙</b>\n\n"
            "No credit cards found in this chat with the specified filters.",
            parse_mode=ParseMode.HTML
        )

# ===== MAIN COMMANDS =====
async def scr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /scr command - Single chat scraping (Group or Channel)"""
    
    # Check if scraper is available
    if pyro_user is None:
        await update.message.reply_text(
            "<b>❌ 𝐒𝐂𝐑𝐀𝐏𝐄𝐑 𝐍𝐎𝐓 𝐀𝐕𝐀𝐈𝐋𝐀𝐁𝐋𝐄</b>\n\n"
            "Scraper is currently disabled.\n"
            "Please contact admin to fix configuration.",
            parse_mode=ParseMode.HTML
        )
        return
    
    user = update.effective_user
    
    # Cooldown check
    if not await enforce_cooldown(user.id, update):
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "💎 𝘽𝙡𝙖𝙘𝙠 𝙓 𝘾𝙖𝙧𝙙 𝘾𝙘 𝙎𝙘𝙧𝙚𝙥𝙚𝙧 💸\n\n"
            "🧠 <b>𝐔𝐒𝐀𝐆𝐄:</b>\n"
            "<code>/scr 𝙇𝙞𝙣𝙠 𝘼𝙢𝙤𝙪𝙣𝙩</code>\n\n"
            "🔧 <b>𝐄𝐗𝐀𝐌𝐏𝐋𝐄𝐒:</b>\n"
            "<b>Channel:</b>\n"
            "<code>/scr @blinkisop 100</code>\n"
            "<code>/scr https://t.me/BlackXCarding 100</code>\n"
            "<code>/scr -100xxxxxxx 100</code>\n\n"
            "<b>Group:</b>\n"
            "<code>/scr @BlinkXChat 100</code>\n"
            "<code>/scr https://t.me/BlinkXChat 100</code>\n"
            "<code>/scr t.me/+invitecode 100</code>\n\n"
            "🎯 <b>𝐅𝐈𝐋𝐓𝐄𝐑𝐒:</b>\n"
            "• BIN (first 6 digits)\n"
            "• Bank Name",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
        return

    chat_identifier = args[0]
    
    try:
        limit = int(args[1])
    except ValueError:
        await update.message.reply_text(
            "<b>⚠️ 𝐈𝐧𝐯𝐚𝐥𝐢𝐝 𝐥𝐢𝐦𝐢𝐭 𝐯𝐚𝐥𝐮𝐞. 𝐏𝐥𝐞𝐚𝐬𝐞 𝐩𝐫𝐨𝐯𝐢𝐝𝐞 𝐚 𝐯𝐚𝐥𝐢𝐝 𝐧𝐮𝐦𝐛𝐞𝐫 ❌</b>",
            parse_mode=ParseMode.HTML
        )
        return

    start_number = None
    bank_name = None
    bin_filter = None
    
    if len(args) > 2:
        if args[2].isdigit():
            start_number = args[2]
            bin_filter = args[2][:6]
            logger.info(f"BIN filter applied: {bin_filter}")
        else:
            bank_name = " ".join(args[2:])
            logger.info(f"Bank filter applied: {bank_name}")

    max_lim = ADMIN_LIMIT if user.id in ADMIN_IDS else DEFAULT_LIMIT
    if limit > max_lim:
        await update.message.reply_text(
            f"<b>🚫 𝐋𝐢𝐦𝐢𝐭 𝐄𝐱𝐜𝐞𝐞𝐝𝐞𝐝!</b>\n\n"
            f"<b>Your max limit:</b> {max_lim}\n"
            f"<b>Requested:</b> {limit}\n\n"
            f"<i>Contact admin for higher limits</i>",
            parse_mode=ParseMode.HTML
        )
        return

    processing_msg = await update.message.reply_text(
        f"<b>🧠 𝙎𝙘𝙧𝙖𝙥𝙞𝙣𝙜 𝙄𝙣 𝙋𝙧𝙤𝙜𝙧𝙚𝙨𝙨 </b>\n\n"
        f"<b>𝒀𝒐𝒖𝒓 𝑳𝒊𝒏𝒌 ↭ </b> <code>{escape(chat_identifier)}</code>\n"
        f"<b>𝘼𝙢𝙤𝙪𝙣𝙩 ↭ </b> <code>{limit}</code>\n"
        f"<b>𝘽𝙤𝙩 𝘽𝙮 ↭ </b> @BlinkisOP\n"  
        f"<b>𝙎𝙩𝙖𝙩𝙪𝙨 ↭ </b> 𝑩𝒍𝒊𝒏𝒌 𝑺𝒄𝒓𝒆𝒑𝒆𝒓 𝑾𝒐𝒓𝒌𝒊𝒏𝒈 𝑾𝒂𝒊𝒕 𝑭𝒆𝒘 𝑺𝒆𝒄..",
        parse_mode=ParseMode.HTML
    )

    try:
        # Resolve chat (Group or Channel)
        chat, error_msg = await resolve_chat_safe(pyro_user, chat_identifier)
        
        if error_msg:
            await processing_msg.edit_text(
                f"<b>❌ 𝐂𝐇𝐀𝐓 𝐄𝐑𝐑𝐎𝐑:</b>\n"
                f"<code>{escape(error_msg)}</code>\n\n"
                f"<b>💡 𝐒𝐎𝐋𝐔𝐓𝐈𝐎𝐍𝐒:</b>\n"
                f"1. Add bot to the group/channel as member\n"
                f"2. Use correct username/link\n"
                f"3. Ensure chat exists and is accessible",
                parse_mode=ParseMode.HTML
            )
            return

        chat_name = chat.title or chat_identifier
        chat_type = "Group" if chat.type in ["group", "supergroup"] else "Channel"

        # Update processing message
        await processing_msg.edit_text(
            f"<b>☠️ 𝙎𝙘𝙧𝙖𝙥𝙥𝙞𝙣𝙜 𝙄𝙣 𝙋𝙧𝙤𝙜𝙧𝙚𝙨𝙨</b>\n\n"
            f"<b>𝒀𝒐𝒖𝒓 𝑳𝒊𝒏𝒌 ↭ </b> <code>{escape(chat_name)}</code>\n"
            f"<b>𝙏𝙮𝙥𝙚 ↭ </b> <code>{chat_type}</code>\n"
            f"<b>𝘼𝙢𝙤𝙪𝙣𝙩 ↭ </b> <code>{limit}</code>\n"
            f"<b>𝘽𝙤𝙩 𝘽𝙮 ↭ </b> @blinkisop\n"  
            f"<b>𝙎𝙩𝙖𝙩𝙪𝙨 ↭ </b> 𝑩𝒍𝒊𝒏𝒌 𝑺𝒄𝒓𝒆𝒑𝒆𝒓 𝑾𝒐𝒓𝒌𝒊𝒏𝒈 𝑾𝒂𝒊𝒕 𝑭𝒆𝒘 𝑺𝒆𝒄..",
            parse_mode=ParseMode.HTML
        )

        # Scrape messages
        scrapped_results = await scrape_messages(pyro_user, chat.id, limit, start_number=start_number, bank_name=bank_name)
        unique_messages, duplicates_removed = remove_duplicates(scrapped_results)

        await processing_msg.delete()
        
        if unique_messages:
            # Send results
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = "".join(c for c in chat_name if c.isalnum() or c in (' ', '_')).strip()
            file_name = f"BlackXCard_Scraper_x{len(unique_messages)}_{safe_name}_{timestamp}.txt"
            
            async with aiofiles.open(file_name, mode='w') as f:
                await f.write("\n".join(unique_messages))

            user_link = f'<a href="tg://user?id={user.id}">{escape(user.first_name or "User")}</a>'
            
            caption = (
                f"💎 𝘽𝙡𝙖𝙘𝙠 𝙓 𝘾𝙖𝙧𝙙 𝘾𝙘 𝙎𝙘𝙧𝙚𝙥𝙚𝙧 💸\n\n"
                f"☑️ <b>𝙎𝙘𝙧𝙖𝙥𝙥𝙚𝙙 𝙎𝙐𝘾𝘾𝙀𝙎𝙎𝙁𝙐𝙇𝙇𝙔</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━  \n"
                f"🧠 <b>𝙎𝙤𝙪𝙧𝙘𝙚 ↭</b> <code>{escape(chat_name)}</code>\n"
                f"📁 <b>𝙏𝙮𝙥𝙚 ↭</b> <code>{chat_type}</code>\n"
                f"💀 <b> 𝙏𝙤𝙩𝙖𝙡 𝘾𝙘 ↭</b> <code>{len(unique_messages)}</code>\n"
                f"☠️ <b> 𝘿𝙪𝙥𝙡𝙞𝙘𝙖𝙩𝙚 𝙍𝙚𝙢𝙤𝙫𝙚𝙙 ↭</b> <code>{duplicates_removed}</code>\n"
            )
            
            if bin_filter:
                caption += f"🔍 <b>𝐁𝐈𝐍 𝐅𝐈𝐋𝐓𝐄𝐑:</b> <code>{bin_filter}</code>\n"
            if bank_name:
                caption += f"🏦 <b>𝐁𝐀𝐍𝐊 𝐅𝐈𝐋𝐓𝐄𝐑:</b> <code>{escape(bank_name)}</code>\n"
            
            caption += f"━━━━━━━━━━━━━━━━━━━━━━\n💎 <b>𝙎𝙘𝙧𝙖𝙥𝙥𝙚𝙙 𝘽𝙮 ⇾</b> {user_link}\n"

            await update.effective_message.reply_document(
                document=open(file_name, 'rb'),
                filename=file_name,
                caption=caption,
                parse_mode=ParseMode.HTML
            )
            
            # Cleanup
            if os.path.exists(file_name):
                os.remove(file_name)
                
        else:
            await update.message.reply_text(
                f"<b>❌ 𝙉𝙤 𝘾𝙘 𝙁𝙤𝙪𝙣𝙙</b>\n\n"
                f"No credit cards found in this {chat_type.lower()} with the specified filters.",
                parse_mode=ParseMode.HTML
            )

    except Exception as e:
        logger.error(f"Error in scr_command: {e}")
        await processing_msg.edit_text(
            f"<b>❌ 𝐄𝐑𝐑𝐎𝐑:</b>\n<code>{escape(str(e))}</code>\n\n"
            f"<b>𝐂𝐨𝐦𝐦𝐨𝐧 𝐟𝐢𝐱𝐞𝐬:</b>\n"
            f"• Add bot to the group/channel\n"
            f"• Use correct invite link\n"
            f"• Ensure chat is not deleted",
            parse_mode=ParseMode.HTML
        )

# ===== REST OF THE CODE (clean_command and mc_command) REMAINS SAME =====
# ... (clean_command और mc_command का code वही रहेगा जो पहले था)

# ===== FILE CLEANING COMMAND =====
def normalize_card(text):
    """Normalize card data from various formats"""
    text = re.sub(r'[\n\r\|\/]', ' ', text)
    numbers = re.findall(r'\d+', text)

    cc = mm = yy = cvv = ''

    for num in numbers:
        if len(num) >= 13 and len(num) <= 19 and not cc:
            cc = num
        elif len(num) == 4 and num.startswith('20') and not yy:
            yy = num
        elif len(num) == 2 and int(num) <= 12 and not mm:
            mm = num
        elif len(num) == 2 and not yy:
            yy = '20' + num
        elif (len(num) == 3 or len(num) == 4) and not cvv:
            cvv = num

    if cc and mm and yy and cvv:
        if len(yy) == 2:
            yy = '20' + yy
        return f"{cc}|{mm}|{yy}|{cvv}"

    return None

async def clean_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clean command - Clean and format card data from file"""
    user = update.effective_user
    
    # Cooldown check
    if not await enforce_cooldown(user.id, update):
        return

    if not update.message.reply_to_message or not update.message.reply_to_message.document:
        await update.message.reply_text(
            "『🧹 𝐅𝐈𝐋𝐄 𝐂𝐋𝐄𝐀𝐍𝐄𝐑 💎』\n\n"
            "🧠 <b>𝐔𝐒𝐀𝐆𝐄:</b>\n"
            "Reply to a .txt file with card data\n\n"
            "🔧 <b>𝐄𝐗𝐀𝐌𝐏𝐋𝐄:</b>\n"
            "<code>/clean</code> (reply to file)\n\n"
            "⚡ <b>Cleans and formats card data from files</b>",
            parse_mode=ParseMode.HTML
        )
        return

    document = update.message.reply_to_message.document

    if not document.file_name.endswith('.txt'):
        await update.message.reply_text("<b>⚠️ 𝐏𝐥𝐞𝐚𝐬𝐞 𝐩𝐫𝐨𝐯𝐢𝐝𝐞 𝐚 𝐭𝐞𝐱𝐭 (.𝐭𝐱𝐭) 𝐟𝐢𝐥𝐞</b>")
        return

    status_msg = await update.message.reply_text("<b>🧹 𝐏𝐫𝐨𝐜𝐞𝐬𝐬𝐢𝐧𝐠 𝐟𝐢𝐥𝐞...</b>")

    try:
        # Download file
        file = await context.bot.get_file(document.file_id)
        file_path = f"temp_{document.file_id}.txt"
        await file.download_to_drive(file_path)

        # Read and process file
        async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = await f.read()

        lines = content.strip().split('\n')
        cleaned_cards = []

        for line in lines:
            if line.strip():
                normalized = normalize_card(line)
                if normalized:
                    cleaned_cards.append(normalized)

        unique_cards, duplicates_removed = remove_duplicates(cleaned_cards)

        if unique_cards:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"cleaned_x{len(unique_cards)}_{timestamp}.txt"
            async with aiofiles.open(output_filename, 'w') as f:
                await f.write('\n'.join(unique_cards))

            user_link = f'<a href="tg://user?id={user.id}">{escape(user.first_name or "User")}</a>'
            caption = (
                f"『🧹 𝐅𝐈𝐋𝐄 𝐂𝐋𝐄𝐀𝐍𝐄𝐑 💎』\n\n"
                f"✅ <b>𝐂𝐋𝐄𝐀𝐍𝐄𝐃 𝐒𝐔𝐂𝐂𝐄𝐒𝐒𝐅𝐔𝐋𝐋𝐘</b>\n"
                f"▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                f"🌐 <b>𝐒𝐎𝐔𝐑𝐂𝐄:</b> <code>File Cleaning 🧹</code>\n"
                f"📝 <b>𝐀𝐌𝐎𝐔𝐍𝐓:</b> <code>{len(unique_cards)}</code>\n"
                f"🗑️ <b>𝐃𝐔𝐏𝐋𝐈𝐂𝐀𝐓𝐄𝐒 𝐑𝐄𝐌𝐎𝐕𝐄𝐃:</b> <code>{duplicates_removed}</code>\n"
                f"▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                f"👤 <b>𝐂𝐋𝐄𝐀𝐍𝐄𝐃 𝐁𝐘:</b> {user_link}\n"
                f"🕒 <b>𝐓𝐈𝐌𝐄:</b> <code>{datetime.now().strftime('%d-%m-%Y %H:%M:%S')}</code>\n"
            )

            await status_msg.delete()
            await update.message.reply_document(
                document=open(output_filename, 'rb'),
                filename=output_filename,
                caption=caption,
                parse_mode=ParseMode.HTML
            )

            # Cleanup
            os.remove(file_path)
            os.remove(output_filename)

        else:
            await status_msg.delete()
            await update.message.reply_text(
                "<b>❌ 𝐍𝐎 𝐕𝐀𝐋𝐈𝐃 𝐂𝐑𝐄𝐃𝐈𝐓 𝐂𝐀𝐑𝐃𝐒 𝐅𝐎𝐔𝐍𝐃</b>\n\n"
                "The file doesn't contain valid credit card data in recognizable formats.",
                parse_mode=ParseMode.HTML
            )
            os.remove(file_path)

    except Exception as e:
        logger.error(f"Error in clean command: {e}")
        await status_msg.delete()
        await update.message.reply_text(
            f"<b>❌ 𝐄𝐑𝐑𝐎𝐑 𝐏𝐑𝐎𝐂𝐄𝐒𝐒𝐈𝐍𝐆 𝐅𝐈𝐋𝐄:</b>\n<code>{escape(str(e))}</code>",
            parse_mode=ParseMode.HTML
        )

# ===== MULTIPLE CHANNELS COMMAND =====
async def mc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /mc command - Multiple chat scraping (Groups & Channels)"""
    
    # Check if scraper is available
    if pyro_user is None:
        await update.message.reply_text(
            "<b>❌ 𝐒𝐂𝐑𝐀𝐏𝐄𝐑 𝐍𝐎𝐓 𝐀𝐕𝐀𝐈𝐋𝐀𝐁𝐋𝐄</b>\n\n"
            "Scraper is currently disabled.\n"
            "Please contact admin to fix configuration.",
            parse_mode=ParseMode.HTML
        )
        return
    
    user = update.effective_user
    
    # Cooldown check
    if not await enforce_cooldown(user.id, update):
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "『💎 𝐌𝐔𝐋𝐓𝐈𝐏𝐋𝐄 𝐂𝐇𝐀𝐓 𝐒𝐂𝐑𝐀𝐏𝐄𝐑 💸』\n\n"
            "🧠 <b>𝐔𝐒𝐀𝐆𝐄:</b>\n"
            "<code>/mc chat1 chat2 ... amount</code>\n\n"
            "🔧 <b>𝐄𝐗𝐀𝐌𝐏𝐋𝐄:</b>\n"
            "<code>/mc @group1 @channel2 https://t.me/+invite 100</code>\n\n"
            "⚡ <b>Scrapes from multiple groups/channels simultaneously</b>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
        return

    chat_identifiers = args[:-1]
    
    try:
        limit = int(args[-1])
    except ValueError:
        await update.message.reply_text(
            "<b>⚠️ 𝐈𝐧𝐯𝐚𝐥𝐢𝐝 𝐥𝐢𝐦𝐢𝐭 𝐯𝐚𝐥𝐮𝐞</b>",
            parse_mode=ParseMode.HTML
        )
        return

    max_lim = ADMIN_LIMIT if user.id in ADMIN_IDS else DEFAULT_LIMIT
    if limit > max_lim:
        await update.message.reply_text(
            f"<b>🚫 𝐋𝐢𝐦𝐢𝐭 𝐄𝐱𝐜𝐞𝐝𝐞𝐝!</b>\n\n"
            f"<b>Your max limit:</b> {max_lim}\n"
            f"<b>Requested:</b> {limit}",
            parse_mode=ParseMode.HTML
        )
        return

    processing_msg = await update.message.reply_text(
        f"<b>🔍 𝐌𝐔𝐋𝐓𝐈𝐏𝐋𝐄 𝐂𝐇𝐀𝐓 𝐒𝐂𝐑𝐀𝐏𝐈𝐍𝐆...</b>\n\n"
        f"<b>Chats:</b> {len(chat_identifiers)}\n"
        f"<b>Limit:</b> <code>{limit}</code>\n"
        f"<b>Status:</b> Starting...",
        parse_mode=ParseMode.HTML
    )

    all_messages = []
    successful_chats = []
    failed_chats = []
    
    for chat_identifier in chat_identifiers:
        try:
            chat, error_msg = await resolve_chat_safe(pyro_user, chat_identifier)
            
            if error_msg:
                failed_chats.append(f"{chat_identifier}: {error_msg}")
                continue
                
            results = await scrape_messages(pyro_user, chat.id, limit)
            all_messages.extend(results)
            successful_chats.append(chat.title or chat_identifier)
            
        except Exception as e:
            logger.error(f"Failed to scrape from {chat_identifier}: {e}")
            failed_chats.append(f"{chat_identifier}: {str(e)}")

    unique_messages, duplicates_removed = remove_duplicates(all_messages)
    unique_messages = unique_messages[:limit]

    await processing_msg.delete()
    
    if unique_messages:
        # Send results
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"BlackXCard_Multi_x{len(unique_messages)}_{timestamp}.txt"
        
        async with aiofiles.open(file_name, mode='w') as f:
            await f.write("\n".join(unique_messages))

        user = update.effective_user
        user_link = f'<a href="tg://user?id={user.id}">{escape(user.first_name or "User")}</a>'
        
        caption = (
            f"💎 𝘽𝙡𝙖𝙘𝙠 𝙓 𝘾𝙖𝙧𝙙 𝙈𝙪𝙡𝙩𝙞 𝘾𝙘 𝙎𝙘𝙧𝙚𝙥𝙚𝙧 💸\n\n"
            f"✅ <b>𝙎𝙘𝙧𝙖𝙥𝙥𝙚𝙙 𝙎𝙪𝙘𝙘𝙚𝙨𝙨𝙛𝙪𝙡𝙡𝙮</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━  \n"
            f"🧠 <b>𝙎𝙤𝙪𝙧𝙘𝙚 ↭</b> <code>{len(successful_chats)}/{len(chat_identifiers)} Chats</code>\n"
            f"💀 <b> 𝙏𝙤𝙩𝙖𝙡 𝘾𝙘 ↭</b> <code>{len(unique_messages)}</code>\n"
            f"☠️ <b> 𝘿𝙪𝙥𝙡𝙞𝙘𝙖𝙩𝙚 𝙍𝙚𝙢𝙤𝙫𝙚𝙙 ↭</b> <code>{duplicates_removed}</code>\n"
            f"✅ <b> 𝙎𝙪𝙘𝙘𝙚𝙨𝙨𝙛𝙪𝙡 ↭</b> <code>{len(successful_chats)}</code>\n"
            f"❌ <b> 𝙁𝙖𝙞𝙡𝙚𝙙 ↭</b> <code>{len(failed_chats)}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💎 <b>𝙎𝙘𝙧𝙖𝙥𝙥𝙚𝙙 𝘽𝙮 ⇾</b> {user_link}\n"
        )

        await update.effective_message.reply_document(
            document=open(file_name, 'rb'),
            filename=file_name,
            caption=caption,
            parse_mode=ParseMode.HTML
        )
        
        # Cleanup
        if os.path.exists(file_name):
            os.remove(file_name)
            
        # Send failed chats info if any
        if failed_chats:
            failed_info = "\n".join(failed_chats[:3])  # Show first 3 failures
            await update.message.reply_text(
                f"<b>⚠️ 𝐅𝐚𝐢𝐥𝐞𝐝 𝐂𝐡𝐚𝐭𝐬 ({len(failed_chats)}):</b>\n"
                f"<code>{escape(failed_info)}</code>\n"
                f"<i>... and {len(failed_chats)-3} more</i>" if len(failed_chats) > 3 else "",
                parse_mode=ParseMode.HTML
            )
    else:
        await update.message.reply_text(
            "<b>❌ 𝐍𝐎 𝐂𝐑𝐄𝐃𝐈𝐓 𝐂𝐀𝐑𝐃𝐒 𝐅𝐎𝐔𝐍𝐃</b>\n\n"
            "No cards found in any of the specified chats.",
            parse_mode=ParseMode.HTML
        )