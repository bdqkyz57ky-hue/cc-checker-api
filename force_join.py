from functools import wraps
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest, Forbidden

# --- Configuration ---
GROUP_ID = -1003173403820    # numeric group ID (required)
GROUP_USERNAME = "BlinkXChat"     # for join button (@username only)

CHANNEL_ID = -1003159765896    # numeric channel ID (required)
CHANNEL_USERNAME = "BlackXCards"  # for join button (no '+' sign)

# ✅ Updated permanent image link from ImgBB
FORCE_JOIN_IMAGE = "https://i.ibb.co/93nHh5Xj/IMG-20251104-185218-529.jpg"

logger = logging.getLogger("force_join")
logger.setLevel(logging.INFO)


# --- Helper: Safe membership check ---
async def safe_get_member(bot, chat_id, user_id: int):
    """Safely check if a user is in a group/channel, handles API errors."""
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        logger.info(f"[DEBUG] User {user_id} in {chat_id}: {member.status}")
        return member.status
    except BadRequest as e:
        if "user not found" in str(e).lower() or "user not participant" in str(e).lower():
            logger.info(f"[DEBUG] User {user_id} NOT in {chat_id}")
            return "not_member"
        else:
            logger.warning(f"[SAFE CHECK] Failed to get member {user_id} in {chat_id}: {e}")
            return None
    except Forbidden:
        logger.warning(f"[SAFE CHECK] Bot not admin in chat {chat_id} or chat inaccessible")
        return None
    except Exception as e:
        logger.warning(f"[SAFE CHECK] Error getting member {user_id} in {chat_id}: {e}")
        return None


async def is_user_joined(bot, user_id: int) -> bool:
    """Check if user has joined BOTH group and channel."""
    # ✅ "restricted" status ko bhi include karo kyunki restricted users bhi group ke members hote hain
    valid_statuses = ["member", "administrator", "creator", "restricted"]

    # --- Check group ---
    group_status = await safe_get_member(bot, GROUP_ID, user_id)
    if group_status not in valid_statuses:
        logger.warning(f"User {user_id} NOT in group ({group_status})")
        return False

    # --- Check channel ---
    channel_status = await safe_get_member(bot, CHANNEL_ID, user_id)
    if channel_status not in valid_statuses:
        logger.warning(f"User {user_id} NOT in channel ({channel_status})")
        return False

    logger.info(f"User {user_id} is in group & channel ✅")
    return True


# --- Force Join Decorator ---
def force_join(func):
    """Decorator to enforce group + channel join before using a command."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id

        # Always allow /start
        if update.message and update.message.text and update.message.text.startswith("/start"):
            return await func(update, context, *args, **kwargs)

        # Check membership
        joined = await is_user_joined(context.bot, user_id)
        if not joined:
            keyboard = [
                [InlineKeyboardButton("📢 Join Group", url=f"https://t.me/{GROUP_USERNAME}")],
                [InlineKeyboardButton("📡 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")],
                [InlineKeyboardButton("✅ I have joined", callback_data="check_joined")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            caption_text = "👀 𝙈𝙖𝙠𝙚 𝙎𝙪𝙧𝙚 𝙔𝙤𝙪 𝙅𝙤𝙞𝙣 𝙊𝙪𝙧 𝘾𝙝𝙖𝙣𝙣𝙚𝙡 𝘼𝙣𝙙 𝙂𝙧𝙤𝙪𝙥 🔥"

            if update.message:
                await update.message.reply_photo(
                    photo=FORCE_JOIN_IMAGE,
                    caption=caption_text,
                    reply_markup=reply_markup
                )
            elif update.callback_query:
                await update.callback_query.message.reply_photo(
                    photo=FORCE_JOIN_IMAGE,
                    caption=caption_text,
                    reply_markup=reply_markup
                )
            return  # Stop execution

        # User already joined → proceed
        return await func(update, context, *args, **kwargs)

    return wrapper


# --- Callback for "✅ I have joined" button ---
async def check_joined_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-check membership when user clicks 'I have joined'."""
    query = update.callback_query
    await query.answer()  # Important: answer callback first
    user_id = query.from_user.id

    logger.info(f"Callback triggered by user {user_id}")

    joined = await is_user_joined(context.bot, user_id)

    if joined:
        await query.answer("✅ 𝗬𝗼𝘂 𝗵𝗮𝘃𝗲 𝗷𝗼𝗶𝗻𝗲𝗱, 𝗮𝗰𝗰𝗲𝘀𝘀 𝗴𝗿𝗮𝗻𝘁𝗲𝗱! 𝗡𝗼𝘄 𝘆𝗼𝘂 𝗰𝗮𝗻 𝘂𝘀𝗲 𝘁𝗵𝗲 𝗯𝗼𝘁 ✅", show_alert=True)
        try:
            await query.edit_message_caption("💎 𝙏𝙝𝙖𝙣𝙠𝙨 𝙁𝙤𝙧 𝙅𝙤𝙞𝙣𝙞𝙣𝙜 𝘽𝙤𝙩 𝘾𝙤𝙢𝙢𝙖𝙣𝙙 𝘼𝙫𝙞𝙡𝙖𝙗𝙡𝙚 𝙀𝙣𝙟𝙤𝙮 𝙔𝙤𝙪 𝘾𝙖𝙣 𝙐𝙨𝙚 𝘽𝙤𝙩 𝙄𝙣 𝙋𝙧𝙞𝙫𝙚𝙩 𝘾𝙝𝙖𝙩 𝘼𝙡𝙨𝙤 🔥")
        except Exception as e:
            logger.error(f"Failed to edit message: {e}")
    else:
        await query.answer("❌ 𝗔𝗰𝗰𝗲𝘀𝘀 𝗱𝗲𝗻𝗶𝗲𝗱 – 𝘆𝗼𝘂 𝘀𝘁𝗶𝗹𝗹 𝗻𝗲𝗲𝗱 𝘁𝗼 𝗷𝗼𝗶𝗻!", show_alert=True)
        logger.info(f"User {user_id} clicked 'I have joined' but is still missing membership.")