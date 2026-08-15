import asyncio
import logging
import math
import os
import re
import sqlite3
from datetime import datetime, date, timezone
from io import BytesIO
from decimal import Decimal, ROUND_DOWN

import qrcode
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

BOT_TOKEN = os.getenv("BOT_TOKEN", "8883287876:AAEtuHY9qoR8PrrsZR87E6mSQQnDTmq9JtU")

# Put the REAL numeric Telegram IDs of both admins here.
# Usernames are shown to users, but numeric IDs should be used for authorization.
ADMIN_IDS = {
    8420732280,          # first admin ID from your earlier specification
    # ADD_SECOND_ADMIN_NUMERIC_ID_HERE
}
ADMIN_USERNAMES = ["@kritika_pridictions", "@sparsh_zii"]

REQUIRED_CHANNELS = [
    ("public_sg_updated", "Public SG Updated"),
    ("public_sg_community", "Public SG Community"),
    ("godfather_pridiction", "Godfather Prediction"),
    ("team_tiranga", "Team Tiranga"),
    ("Sparks_Corporation", "Sparks Corporation"),
]

UPI_ID = "pablo.rudransh@fam"
COIN_PER_INR = 100
DEPOSIT_MIN = 1
DEPOSIT_MAX = 1000
P2P_MIN = 100
WITHDRAW_MIN = 10000

DB_PATH = os.getenv("DB_PATH", "sparks.db")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sparks")

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA foreign_keys=ON")

def now():
    return datetime.now(timezone.utc).isoformat()

def init_db():
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        language TEXT DEFAULT 'hinglish',
        balance INTEGER DEFAULT 0,
        total_deposited INTEGER DEFAULT 0,
        total_withdrawn INTEGER DEFAULT 0,
        total_sent INTEGER DEFAULT 0,
        total_received INTEGER DEFAULT 0,
        referral_count INTEGER DEFAULT 0,
        streak INTEGER DEFAULT 0,
        last_daily_date TEXT,
        daily_bonus INTEGER DEFAULT 100,
        referred_by INTEGER,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS transactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        amount_coins INTEGER NOT NULL,
        tax_coins INTEGER DEFAULT 0,
        net_coins INTEGER DEFAULT 0,
        status TEXT NOT NULL,
        note TEXT,
        reference TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS deposits(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount_inr INTEGER NOT NULL,
        gross_coins INTEGER NOT NULL,
        tax_percent REAL NOT NULL,
        tax_coins INTEGER NOT NULL,
        net_coins INTEGER NOT NULL,
        upi_id TEXT,
        upi_name TEXT,
        utr TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL,
        processed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS withdrawals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        coins INTEGER NOT NULL,
        tax_percent REAL NOT NULL,
        tax_coins INTEGER NOT NULL,
        payout_coins INTEGER NOT NULL,
        upi_id TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL,
        processed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS transfers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER NOT NULL,
        receiver_id INTEGER NOT NULL,
        coins INTEGER NOT NULL,
        tax_percent REAL NOT NULL,
        tax_coins INTEGER NOT NULL,
        receiver_coins INTEGER NOT NULL,
        status TEXT DEFAULT 'completed',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS promos(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        coins INTEGER NOT NULL,
        usage_limit INTEGER DEFAULT 0,
        used_count INTEGER DEFAULT 0,
        expires_at TEXT,
        active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS promo_uses(
        promo_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(promo_id,user_id)
    );

    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS support_messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        admin_id INTEGER,
        direction TEXT NOT NULL,
        text TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """)
    defaults = {
        "deposit_tax": "5",
        "withdraw_tax": "5",
        "transfer_tax": "5",
        "daily_start": "100",
        "daily_reset_start": "50",
        "referrer_bonus": "500",
        "new_user_bonus": "100",
    }
    for k, v in defaults.items():
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    db.commit()

def setting(key, default=0.0):
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    try:
        return float(row["value"]) if row else float(default)
    except ValueError:
        return float(default)

def set_setting(key, value):
    db.execute("INSERT INTO settings(key,value) VALUES(?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    db.commit()

def get_user(user_id):
    return db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

def ensure_user(tg_user, referred_by=None):
    row = get_user(tg_user.id)
    if row:
        db.execute("UPDATE users SET username=?, first_name=? WHERE id=?",
                   (tg_user.username, tg_user.first_name, tg_user.id))
        db.commit()
        return
    if referred_by == tg_user.id:
        referred_by = None
    db.execute("""INSERT INTO users(id,username,first_name,referred_by,created_at)
                  VALUES(?,?,?,?,?)""",
               (tg_user.id, tg_user.username, tg_user.first_name, referred_by, now()))
    db.commit()

def is_admin(user_id):
    return user_id in ADMIN_IDS

def money_round_down(x):
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_DOWN))

def tax_parts(coins, percent):
    tax = money_round_down(Decimal(coins) * Decimal(str(percent)) / Decimal(100))
    return tax, coins - tax

def add_transaction(user_id, typ, amount, tax, net, status, note="", reference=""):
    db.execute("""INSERT INTO transactions
        (user_id,type,amount_coins,tax_coins,net_coins,status,note,reference,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        (user_id, typ, amount, tax, net, status, note, reference, now()))
    db.commit()

def change_balance(user_id, delta):
    db.execute("UPDATE users SET balance=balance+? WHERE id=?", (delta, user_id))
    db.commit()

def qr_bytes(amount):
    uri = (
        f"upi://pay?pa={UPI_ID}"
        f"&pn=Sparks%20Corporation"
        f"&am={amount:.2f}&cu=INR"
    )
    img = qrcode.make(uri)
    bio = BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Deposit", callback_data="deposit"),
         InlineKeyboardButton(text="💸 Withdrawal", callback_data="withdraw")],
        [InlineKeyboardButton(text="🎁 Daily Bonus", callback_data="daily"),
         InlineKeyboardButton(text="📊 Dashboard", callback_data="dash")],
        [InlineKeyboardButton(text="🔄 Transfer", callback_data="transfer"),
         InlineKeyboardButton(text="🎟 Promo Code", callback_data="promo")],
        [InlineKeyboardButton(text="📜 History", callback_data="history"),
         InlineKeyboardButton(text="🆘 Help", callback_data="help")],
    ])

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Deposits", callback_data="adm_deposits"),
         InlineKeyboardButton(text="📤 Withdrawals", callback_data="adm_withdrawals")],
        [InlineKeyboardButton(text="📊 Dashboard", callback_data="adm_dash"),
         InlineKeyboardButton(text="💸 Tax", callback_data="adm_tax")],
        [InlineKeyboardButton(text="👥 Users", callback_data="adm_users"),
         InlineKeyboardButton(text="🎟 Promo", callback_data="adm_promo")],
        [InlineKeyboardButton(text="🎁 Bonus", callback_data="adm_bonus"),
         InlineKeyboardButton(text="📢 Broadcast", callback_data="adm_broadcast")],
    ])

async def membership_status(bot, user_id, username):
    remaining = []
    for channel, title in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(f"@{channel}", user_id)
            if member.status in {
                ChatMemberStatus.CREATOR,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.MEMBER,
            }:
                continue
            # Telegram may return LEFT/KICKED for non-members.
            remaining.append((channel, title))
        except Exception as e:
            log.warning("Membership check failed for @%s: %s", channel, e)
            # If bot cannot check, keep the channel visible rather than falsely approving.
            remaining.append((channel, title))
    return remaining

def sub_keyboard(remaining):
    rows = []
    for channel, title in remaining:
        rows.append([InlineKeyboardButton(
            text=f"↗ Join {title}",
            url=f"https://t.me/{channel}"
        )])
    rows.append([InlineKeyboardButton(text="✅ Verify", callback_data="verify_subs")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def send_gate(message, bot):
    remaining = await membership_status(bot, message.from_user.id, message.from_user.username)
    if not remaining:
        await message.answer(
            "⚡ <b>Welcome to Sparks!</b>\n\n"
            "Your channels are verified. Choose an option below.",
            reply_markup=main_menu()
        )
        return True
    await message.answer(
        "🔐 <b>Channel Verification</b>\n\n"
        "Please join the remaining required channels.\n"
        "Already joined channels are automatically hidden.\n\n"
        "After joining, press <b>Verify</b>.",
        reply_markup=sub_keyboard(remaining)
    )
    return False

class DepositState(StatesGroup):
    amount = State()
    upi_id = State()
    upi_name = State()
    utr = State()

class WithdrawState(StatesGroup):
    coins = State()
    upi_id = State()

class TransferState(StatesGroup):
    receiver = State()
    coins = State()

class SupportState(StatesGroup):
    message = State()

class PromoState(StatesGroup):
    code = State()

class AdminTaxState(StatesGroup):
    kind = State()
    value = State()

class AdminPromoState(StatesGroup):
    code = State()
    coins = State()
    limit = State()
    expiry = State()

class BroadcastState(StatesGroup):
    text = State()

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    referred_by = None
    parts = message.text.split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("ref_"):
        try:
            referred_by = int(parts[1][4:])
        except ValueError:
            pass
    ensure_user(message.from_user, referred_by)
    await send_gate(message, bot)

@dp.callback_query(F.data == "verify_subs")
async def verify_subs(call: CallbackQuery):
    remaining = await membership_status(bot, call.from_user.id, call.from_user.username)
    if remaining:
        await call.answer("❌ Kuch channels abhi bhi pending hain.", show_alert=True)
        try:
            await call.message.edit_reply_markup(reply_markup=sub_keyboard(remaining))
        except Exception:
            pass
        return
    await call.answer("✅ Verified!")
    await call.message.edit_text(
        "🎉 <b>Verification successful!</b>\n\nWelcome to Sparks!",
        reply_markup=main_menu()
    )
    # Referral rewards only after successful verification.
    user = get_user(call.from_user.id)
    if user and user["referred_by"]:
        ref = get_user(user["referred_by"])
        if ref:
            # Prevent duplicate reward by using a transaction note marker.
            exists = db.execute(
                "SELECT 1 FROM transactions WHERE user_id=? AND type='referral_new' AND note=?",
                (user["referred_by"], f"user:{user['id']}")
            ).fetchone()
            if not exists:
                ref_bonus = int(setting("referrer_bonus", 500))
                new_bonus = int(setting("new_user_bonus", 100))
                change_balance(ref["id"], ref_bonus)
                change_balance(user["id"], new_bonus)
                add_transaction(ref["id"], "referral_new", ref_bonus, 0, ref_bonus,
                                "completed", f"user:{user['id']}")
                add_transaction(user["id"], "referral_join", new_bonus, 0, new_bonus,
                                "completed", f"referrer:{ref['id']}")
                db.execute("UPDATE users SET referral_count=referral_count+1 WHERE id=?",
                           (ref["id"],))
                db.commit()
                try:
                    await bot.send_message(ref["id"],
                        f"🎉 Referral reward!\n+<b>{ref_bonus}</b> Sparks Coin")
                except Exception:
                    pass

@dp.callback_query(F.data == "deposit")
async def cb_deposit(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(DepositState.amount)
    await call.message.answer(
        f"💰 <b>Deposit</b>\n\nEnter amount in INR ({DEPOSIT_MIN}-{DEPOSIT_MAX}):"
    )

@dp.message(Command("deposit"))
async def cmd_deposit(message: Message, state: FSMContext):
    if not await send_gate(message, bot):
        return
    await state.set_state(DepositState.amount)
    await message.answer(f"💰 Enter deposit amount ₹{DEPOSIT_MIN}-₹{DEPOSIT_MAX}:")

@dp.message(DepositState.amount)
async def dep_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
    except Exception:
        await message.answer("❌ Enter a whole INR amount.")
        return
    if not (DEPOSIT_MIN <= amount <= DEPOSIT_MAX):
        await message.answer(f"❌ Amount must be ₹{DEPOSIT_MIN}-₹{DEPOSIT_MAX}.")
        return
    await state.update_data(amount=amount)
    await state.set_state(DepositState.upi_id)
    await message.answer("Enter the <b>UPI ID</b> from which you will pay:")

@dp.message(DepositState.upi_id)
async def dep_upi(message: Message, state: FSMContext):
    await state.update_data(upi_id=message.text.strip()[:100])
    await state.set_state(DepositState.upi_name)
    await message.answer("Enter the <b>UPI Name</b>:")

@dp.message(DepositState.upi_name)
async def dep_name(message: Message, state: FSMContext):
    data = await state.get_data()
    amount = data["amount"]
    tax_pct = setting("deposit_tax", 5)
    gross = amount * COIN_PER_INR
    tax, net = tax_parts(gross, tax_pct)
    await state.update_data(upi_name=message.text.strip()[:100])
    qr = BufferedInputFile(qr_bytes(amount), filename="sparks_deposit.png")
    await message.answer_photo(
        qr,
        caption=(
            f"💳 <b>Deposit ₹{amount}</b>\n\n"
            f"UPI: <code>{UPI_ID}</code>\n"
            f"Tax: {tax_pct:g}% ({tax} coins)\n"
            f"Net credit: <b>{net} coins</b>\n\n"
            "Pay the exact INR amount using the QR/UPI above, then send your UTR."
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ I've Paid", callback_data="dep_paid")]
        ])
    )
    await state.set_state(DepositState.utr)

@dp.callback_query(F.data == "dep_paid")
async def dep_paid(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.answer("Send your <b>UTR / Transaction ID</b>:")
    await state.set_state(DepositState.utr)

@dp.message(DepositState.utr)
async def dep_utr(message: Message, state: FSMContext):
    data = await state.get_data()
    amount = data["amount"]
    tax_pct = setting("deposit_tax", 5)
    gross = amount * COIN_PER_INR
    tax, net = tax_parts(gross, tax_pct)
    cur = db.execute("""INSERT INTO deposits
        (user_id,amount_inr,gross_coins,tax_percent,tax_coins,net_coins,upi_id,upi_name,utr,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (message.from_user.id, amount, gross, tax_pct, tax, net,
         data["upi_id"], data["upi_name"], message.text.strip()[:100], now()))
    db.commit()
    dep_id = cur.lastrowid
    add_transaction(message.from_user.id, "deposit", gross, tax, net,
                     "pending", f"deposit:{dep_id}", message.text.strip()[:100])
    await state.clear()
    await message.answer(
        f"⏳ Deposit request <b>#{dep_id}</b> submitted.\n"
        f"Amount: ₹{amount}\nNet coins after tax: <b>{net}</b>\n"
        "Please wait for admin approval."
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📥 <b>New Deposit #{dep_id}</b>\n"
                f"User: <code>{message.from_user.id}</code>\n"
                f"Amount: ₹{amount}\n"
                f"UPI: <code>{data['upi_id']}</code>\n"
                f"Name: {data['upi_name']}\n"
                f"UTR: <code>{message.text.strip()}</code>\n"
                f"Net: {net} coins",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Approve", callback_data=f"dep_ok:{dep_id}"),
                    InlineKeyboardButton(text="❌ Decline", callback_data=f"dep_no:{dep_id}")
                ]])
            )
        except Exception as e:
            log.warning("Admin notify: %s", e)

@dp.callback_query(F.data.startswith("dep_ok:"))
async def dep_approve(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Not authorized.", show_alert=True); return
    dep_id = int(call.data.split(":")[1])
    dep = db.execute("SELECT * FROM deposits WHERE id=?", (dep_id,)).fetchone()
    if not dep or dep["status"] != "pending":
        await call.answer("Already processed.", show_alert=True); return
    db.execute("UPDATE deposits SET status='approved',processed_at=? WHERE id=?", (now(), dep_id))
    db.execute("UPDATE users SET balance=balance+?,total_deposited=total_deposited+? WHERE id=?",
               (dep["net_coins"], dep["net_coins"], dep["user_id"]))
    db.commit()
    add_transaction(dep["user_id"], "deposit", dep["gross_coins"], dep["tax_coins"],
                     dep["net_coins"], "completed", f"deposit:{dep_id}", dep["utr"])
    await call.answer("Approved.")
    await call.message.edit_reply_markup(reply_markup=None)
    await bot.send_message(dep["user_id"],
        f"✅ Deposit approved!\n+<b>{dep['net_coins']} Sparks Coin</b>\n"
        f"New balance: <b>{get_user(dep['user_id'])['balance']}</b>")

@dp.callback_query(F.data.startswith("dep_no:"))
async def dep_decline(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Not authorized.", show_alert=True); return
    dep_id = int(call.data.split(":")[1])
    dep = db.execute("SELECT * FROM deposits WHERE id=?", (dep_id,)).fetchone()
    if not dep or dep["status"] != "pending":
        await call.answer("Already processed.", show_alert=True); return
    db.execute("UPDATE deposits SET status='declined',processed_at=? WHERE id=?", (now(), dep_id))
    db.commit()
    add_transaction(dep["user_id"], "deposit", dep["gross_coins"], dep["tax_coins"],
                     dep["net_coins"], "declined", f"deposit:{dep_id}", dep["utr"])
    await call.answer("Declined.")
    await call.message.edit_reply_markup(reply_markup=None)
    await bot.send_message(dep["user_id"],
        f"❌ Deposit #{dep_id} declined.\nNo coins were credited.")

@dp.callback_query(F.data == "withdraw")
async def cb_withdraw(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(WithdrawState.coins)
    await call.message.answer(f"💸 Enter withdrawal amount in coins (minimum {WITHDRAW_MIN}):")

@dp.message(Command("withdrawal"))
async def cmd_withdraw(message: Message, state: FSMContext):
    if not await send_gate(message, bot): return
    await state.set_state(WithdrawState.coins)
    await message.answer(f"💸 Enter withdrawal amount in coins (minimum {WITHDRAW_MIN}):")

@dp.message(WithdrawState.coins)
async def wd_coins(message: Message, state: FSMContext):
    try:
        coins = int(message.text.strip())
    except Exception:
        await message.answer("❌ Enter a whole coin amount."); return
    user = get_user(message.from_user.id)
    if coins < WITHDRAW_MIN:
        await message.answer(f"❌ Minimum withdrawal is {WITHDRAW_MIN} coins."); return
    if coins > user["balance"]:
        await message.answer("❌ Insufficient available balance."); return
    await state.update_data(coins=coins)
    await state.set_state(WithdrawState.upi_id)
    await message.answer("Enter your <b>UPI ID</b> for payout:")

@dp.message(WithdrawState.upi_id)
async def wd_upi(message: Message, state: FSMContext):
    data = await state.get_data()
    coins = data["coins"]
    tax_pct = setting("withdraw_tax", 5)
    tax, payout = tax_parts(coins, tax_pct)

    # Hold/deduct immediately; declined request releases the exact held amount.
    db.execute("UPDATE users SET balance=balance-? WHERE id=?", (coins, message.from_user.id))
    cur = db.execute("""INSERT INTO withdrawals
        (user_id,coins,tax_percent,tax_coins,payout_coins,upi_id,created_at)
        VALUES(?,?,?,?,?,?,?)""",
        (message.from_user.id, coins, tax_pct, tax, payout, message.text.strip()[:100], now()))
    db.commit()
    wid = cur.lastrowid
    add_transaction(message.from_user.id, "withdrawal", coins, tax, payout,
                     "pending", f"withdrawal:{wid}")
    await state.clear()
    await message.answer(
        f"⏳ Withdrawal <b>#{wid}</b> submitted.\n"
        f"Requested: {coins} coins\n"
        f"Tax: {tax_pct:g}% ({tax} coins)\n"
        f"Admin payout: <b>{payout} coins</b>\n"
        "Your requested coins are held until admin approves or declines."
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📤 <b>New Withdrawal #{wid}</b>\n"
                f"User: <code>{message.from_user.id}</code>\n"
                f"Coins: {coins}\nTax: {tax} coins\n"
                f"Payout: {payout} coins\nUPI: <code>{message.text.strip()}</code>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Approve", callback_data=f"wd_ok:{wid}"),
                    InlineKeyboardButton(text="❌ Decline", callback_data=f"wd_no:{wid}")
                ]])
            )
        except Exception as e:
            log.warning("Admin notify: %s", e)

@dp.callback_query(F.data.startswith("wd_ok:"))
async def wd_approve(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Not authorized.", show_alert=True); return
    wid = int(call.data.split(":")[1])
    wd = db.execute("SELECT * FROM withdrawals WHERE id=?", (wid,)).fetchone()
    if not wd or wd["status"] != "pending":
        await call.answer("Already processed.", show_alert=True); return
    db.execute("UPDATE withdrawals SET status='approved',processed_at=? WHERE id=?", (now(), wid))
    db.execute("UPDATE users SET total_withdrawn=total_withdrawn+? WHERE id=?",
               (wd["payout_coins"], wd["user_id"]))
    db.commit()
    add_transaction(wd["user_id"], "withdrawal", wd["coins"], wd["tax_coins"],
                    wd["payout_coins"], "completed", f"withdrawal:{wid}")
    await call.answer("Approved. Send the payout manually, then finish your admin record.")
    await call.message.edit_reply_markup(reply_markup=None)
    await bot.send_message(wd["user_id"],
        f"✅ Withdrawal #{wid} approved.\n"
        f"Payout: <b>{wd['payout_coins']} coins</b> to <code>{wd['upi_id']}</code>.")

@dp.callback_query(F.data.startswith("wd_no:"))
async def wd_decline(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Not authorized.", show_alert=True); return
    wid = int(call.data.split(":")[1])
    wd = db.execute("SELECT * FROM withdrawals WHERE id=?", (wid,)).fetchone()
    if not wd or wd["status"] != "pending":
        await call.answer("Already processed.", show_alert=True); return
    db.execute("UPDATE withdrawals SET status='declined',processed_at=? WHERE id=?", (now(), wid))
    db.execute("UPDATE users SET balance=balance+? WHERE id=?", (wd["coins"], wd["user_id"]))
    db.commit()
    add_transaction(wd["user_id"], "withdrawal", wd["coins"], wd["tax_coins"],
                    wd["payout_coins"], "declined", f"withdrawal:{wid}")
    await call.answer("Declined and refunded.")
    await call.message.edit_reply_markup(reply_markup=None)
    await bot.send_message(wd["user_id"],
        f"❌ Withdrawal #{wid} declined.\n"
        f"<b>{wd['coins']} coins</b> have been returned to your balance.")

@dp.callback_query(F.data == "transfer")
async def cb_transfer(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(TransferState.receiver)
    await call.message.answer("🔄 Enter receiver's Telegram numeric User ID:")

@dp.message(Command("transfer"))
async def cmd_transfer(message: Message, state: FSMContext):
    if not await send_gate(message, bot): return
    await state.set_state(TransferState.receiver)
    await message.answer("🔄 Enter receiver's Telegram numeric User ID:")

@dp.message(TransferState.receiver)
async def tr_receiver(message: Message, state: FSMContext):
    try:
        rid = int(message.text.strip())
    except Exception:
        await message.answer("❌ Enter a numeric Telegram User ID."); return
    if rid == message.from_user.id or not get_user(rid):
        await message.answer("❌ Receiver must be another registered Sparks user."); return
    await state.update_data(receiver=rid)
    await state.set_state(TransferState.coins)
    await message.answer(f"Enter amount (minimum {P2P_MIN} coins):")

@dp.message(TransferState.coins)
async def tr_coins(message: Message, state: FSMContext):
    try:
        coins = int(message.text.strip())
    except Exception:
        await message.answer("❌ Enter a whole coin amount."); return
    if coins < P2P_MIN:
        await message.answer(f"❌ Minimum transfer is {P2P_MIN} coins."); return
    sender = get_user(message.from_user.id)
    if coins > sender["balance"]:
        await message.answer("❌ Insufficient balance."); return
    data = await state.get_data()
    rid = data["receiver"]
    tax_pct = setting("transfer_tax", 5)
    tax, receiver_coins = tax_parts(coins, tax_pct)

    # Atomic transaction.
    try:
        db.execute("BEGIN IMMEDIATE")
        cur = db.execute("SELECT balance FROM users WHERE id=?", (message.from_user.id,))
        bal = cur.fetchone()["balance"]
        if bal < coins:
            db.execute("ROLLBACK")
            await message.answer("❌ Insufficient balance."); return
        db.execute("UPDATE users SET balance=balance-?,total_sent=total_sent+? WHERE id=?",
                   (coins, coins, message.from_user.id))
        db.execute("UPDATE users SET balance=balance+?,total_received=total_received+? WHERE id=?",
                   (receiver_coins, receiver_coins, rid))
        cur = db.execute("""INSERT INTO transfers
            (sender_id,receiver_id,coins,tax_percent,tax_coins,receiver_coins,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            (message.from_user.id,rid,coins,tax_pct,tax,receiver_coins,now()))
        tid = cur.lastrowid
        db.commit()
    except Exception:
        db.execute("ROLLBACK")
        await message.answer("❌ Transfer failed. Please try again."); return

    add_transaction(message.from_user.id, "transfer_sent", coins, tax, receiver_coins,
                    "completed", f"transfer:{tid}")
    add_transaction(rid, "transfer_received", receiver_coins, 0, receiver_coins,
                    "completed", f"transfer:{tid}")
    await state.clear()
    await message.answer(
        f"✅ Transfer successful!\n"
        f"Sent: {coins} coins\nTax: {tax} coins\n"
        f"Receiver gets: <b>{receiver_coins} coins</b>"
    )
    try:
        await bot.send_message(rid,
            f"💰 You received <b>{receiver_coins} Sparks Coin</b> from "
            f"<code>{message.from_user.id}</code>.")
    except Exception:
        pass

def daily_amount_for_day(day_number):
    if day_number <= 0:
        return 100
    if day_number == 1:
        return 100
    # Integer coins; each day increases by 25%, rounded down.
    x = 100
    for _ in range(2, day_number + 1):
        x = int(x * 1.25)
    return x

def has_qualifying_transaction_today(user_id):
    today = date.today().isoformat()
    row = db.execute("""SELECT 1 FROM transactions
        WHERE user_id=? AND status='completed'
        AND type IN ('deposit','transfer_sent','transfer_received')
        AND substr(created_at,1,10)=?
        LIMIT 1""", (user_id, today)).fetchone()
    return bool(row)

@dp.callback_query(F.data == "daily")
async def cb_daily(call: CallbackQuery):
    await call.answer()
    await claim_daily(call.message, call.from_user.id)

@dp.message(Command("daily"))
async def cmd_daily(message: Message):
    if not await send_gate(message, bot): return
    await claim_daily(message, message.from_user.id)

async def claim_daily(message, user_id):
    u = get_user(user_id)
    today = date.today().isoformat()
    if u["last_daily_date"] == today:
        await message.answer("🎁 Aaj ka daily bonus already claimed hai.")
        return
    if not has_qualifying_transaction_today(user_id):
        await message.answer(
            "🎁 <b>Daily Bonus</b>\n\n"
            "Aaj bonus claim karne se pehle kam se kam 1 completed transaction karo "
            "(Deposit ya Transfer)."
        )
        return

    if not u["last_daily_date"]:
        streak = 1
        bonus = int(setting("daily_start", 100))
    else:
        # If last claim was yesterday, continue. Otherwise reset cycle.
        last = date.fromisoformat(u["last_daily_date"])
        diff = (date.today() - last).days
        if diff == 1:
            if u["streak"] >= 7:
                streak = 1
                bonus = int(setting("daily_reset_start", 50))
            else:
                streak = u["streak"] + 1
                bonus = daily_amount_for_day(streak)
        else:
            streak = 1
            bonus = int(setting("daily_start", 100))

    change_balance(user_id, bonus)
    db.execute("UPDATE users SET streak=?,last_daily_date=?,daily_bonus=? WHERE id=?",
               (streak, today, bonus, user_id))
    db.commit()
    add_transaction(user_id, "daily_bonus", bonus, 0, bonus, "completed",
                    f"streak:{streak}")
    await message.answer(
        f"🎁 <b>Daily Bonus Claimed!</b>\n"
        f"Day: {streak}/7\n"
        f"+<b>{bonus} Sparks Coin</b>\n"
        f"Balance: <b>{get_user(user_id)['balance']}</b>"
    )

@dp.callback_query(F.data == "dash")
async def cb_dash(call: CallbackQuery):
    await call.answer()
    await show_dash(call.message, call.from_user.id)

@dp.message(Command("dash"))
async def cmd_dash(message: Message):
    if not await send_gate(message, bot): return
    await show_dash(message, message.from_user.id)

async def show_dash(message, uid):
    u = get_user(uid)
    await message.answer(
        f"📊 <b>Your Dashboard</b>\n\n"
        f"🆔 User ID: <code>{u['id']}</code>\n"
        f"💰 Balance: <b>{u['balance']}</b> Sparks Coin\n"
        f"📥 Deposited: <b>{u['total_deposited']}</b>\n"
        f"📤 Withdrawn: <b>{u['total_withdrawn']}</b>\n"
        f"↗️ Sent: <b>{u['total_sent']}</b>\n"
        f"↙️ Received: <b>{u['total_received']}</b>\n"
        f"👥 Referrals: <b>{u['referral_count']}</b>\n"
        f"🔥 Streak: <b>{u['streak']}/7</b>"
    )

@dp.callback_query(F.data == "history")
async def cb_history(call: CallbackQuery):
    await call.answer()
    await show_history(call.message, call.from_user.id)

@dp.message(Command("history"))
async def cmd_history(message: Message):
    if not await send_gate(message, bot): return
    await show_history(message, message.from_user.id)

async def show_history(message, uid):
    rows = db.execute("""SELECT * FROM transactions WHERE user_id=?
                         ORDER BY id DESC LIMIT 5""", (uid,)).fetchall()
    if not rows:
        await message.answer("📜 No transactions yet."); return
    lines = ["📜 <b>Last 5 Transactions</b>\n"]
    for r in rows:
        lines.append(
            f"#{r['id']} • {r['type']}\n"
            f"Amount: {r['amount_coins']} | Tax: {r['tax_coins']} | "
            f"Net: {r['net_coins']}\nStatus: {r['status']}\n"
        )
    await message.answer("\n".join(lines))

@dp.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(SupportState.message)
    await call.message.answer(
        "🆘 <b>Support</b>\n\nType your message. It will be sent to the admin team."
    )

@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    if not await send_gate(message, bot): return
    await state.set_state(SupportState.message)
    await message.answer("🆘 Type your support message:")

@dp.message(SupportState.message)
async def support_msg(message: Message, state: FSMContext):
    text = message.text or message.caption or "[non-text message]"
    db.execute("""INSERT INTO support_messages(user_id,direction,text,created_at)
                  VALUES(?,?,?,?)""", (message.from_user.id, "to_admin", text[:4000], now()))
    db.commit()
    await state.clear()
    await message.answer("✅ Message sent to admin. You will receive the reply here.")
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(
                aid,
                f"🆘 <b>Support message</b>\nUser: <code>{message.from_user.id}</code>\n\n{text}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="💬 Reply", callback_data=f"reply:{message.from_user.id}")
                ]])
            )
        except Exception:
            pass

# Admin reply: after clicking Reply, admin simply sends text. This small in-memory map
# is adequate for a single process; the message is also stored in the database.
admin_reply_target = {}

@dp.callback_query(F.data.startswith("reply:"))
async def reply_start(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Not authorized.", show_alert=True); return
    target = int(call.data.split(":")[1])
    admin_reply_target[call.from_user.id] = target
    await call.answer("Reply mode enabled.")
    await call.message.answer(f"💬 Send the reply for user <code>{target}</code>.")

@dp.message()
async def admin_reply_catchall(message: Message):
    if not is_admin(message.from_user.id):
        return
    target = admin_reply_target.pop(message.from_user.id, None)
    if target is not None and message.text:
        db.execute("""INSERT INTO support_messages(user_id,admin_id,direction,text,created_at)
                      VALUES(?,?,?,?,?)""",
                   (target, message.from_user.id, "from_admin", message.text[:4000], now()))
        db.commit()
        try:
            await bot.send_message(target, f"🆘 <b>Admin reply:</b>\n\n{message.text}")
            await message.answer("✅ Reply sent.")
        except Exception:
            await message.answer("❌ Could not send reply to that user.")

@dp.callback_query(F.data == "promo")
async def cb_promo(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(PromoState.code)
    await call.message.answer("🎟 Enter promo code:")

@dp.message(Command("promo"))
async def cmd_promo(message: Message, state: FSMContext):
    if not await send_gate(message, bot): return
    await state.set_state(PromoState.code)
    await message.answer("🎟 Enter promo code:")

@dp.message(PromoState.code)
async def promo_redeem(message: Message, state: FSMContext):
    code = message.text.strip().upper()
    row = db.execute("SELECT * FROM promos WHERE code=?", (code,)).fetchone()
    if not row or not row["active"]:
        await message.answer("❌ Invalid or disabled promo.")
        return
    if row["expires_at"] and row["expires_at"] < now():
        await message.answer("❌ Promo expired."); return
    if row["usage_limit"] > 0 and row["used_count"] >= row["usage_limit"]:
        await message.answer("❌ Promo usage limit reached."); return
    used = db.execute("SELECT 1 FROM promo_uses WHERE promo_id=? AND user_id=?",
                      (row["id"], message.from_user.id)).fetchone()
    if used:
        await message.answer("❌ You already used this promo."); return
    db.execute("INSERT INTO promo_uses(promo_id,user_id,created_at) VALUES(?,?,?)",
               (row["id"], message.from_user.id, now()))
    db.execute("UPDATE promos SET used_count=used_count+1 WHERE id=?", (row["id"],))
    db.commit()
    change_balance(message.from_user.id, row["coins"])
    add_transaction(message.from_user.id, "promo", row["coins"], 0, row["coins"],
                    "completed", code)
    await state.clear()
    await message.answer(f"🎉 Promo redeemed!\n+<b>{row['coins']} Sparks Coin</b>")

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Admin access only."); return
    await message.answer(
        "👑 <b>Sparks Admin Panel</b>\n\n"
        f"Admins: {', '.join(ADMIN_USERNAMES)}",
        reply_markup=admin_menu()
    )

@dp.callback_query(F.data == "adm_deposits")
async def adm_deposits(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    rows = db.execute("SELECT * FROM deposits WHERE status='pending' ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:
        await call.answer("No pending deposits.", show_alert=True); return
    await call.answer()
    for d in rows:
        await call.message.answer(
            f"📥 <b>Deposit #{d['id']}</b>\nUser: <code>{d['user_id']}</code>\n"
            f"₹{d['amount_inr']} | Net {d['net_coins']} coins\n"
            f"UPI: <code>{d['upi_id']}</code>\nName: {d['upi_name']}\nUTR: <code>{d['utr']}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Approve", callback_data=f"dep_ok:{d['id']}"),
                InlineKeyboardButton(text="❌ Decline", callback_data=f"dep_no:{d['id']}")
            ]])
        )

@dp.message(Command("withdrawal", "withdraw"))
async def cmd_withdraw_alias(message: Message, state: FSMContext):
    await cmd_withdraw(message, state)

@dp.callback_query(F.data == "adm_withdrawals")
async def adm_withdrawals(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    rows = db.execute("SELECT * FROM withdrawals WHERE status='pending' ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:
        await call.answer("No pending withdrawals.", show_alert=True); return
    await call.answer()
    for w in rows:
        await call.message.answer(
            f"📤 <b>Withdrawal #{w['id']}</b>\nUser: <code>{w['user_id']}</code>\n"
            f"Requested: {w['coins']} | Tax: {w['tax_coins']} | Payout: {w['payout_coins']}\n"
            f"UPI: <code>{w['upi_id']}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Approve", callback_data=f"wd_ok:{w['id']}"),
                InlineKeyboardButton(text="❌ Decline", callback_data=f"wd_no:{w['id']}")
            ]])
        )

@dp.callback_query(F.data == "adm_dash")
async def adm_dash(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    users = db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    balance = db.execute("SELECT COALESCE(SUM(balance),0) s FROM users").fetchone()["s"]
    dep = db.execute("SELECT COALESCE(SUM(net_coins),0) s FROM deposits WHERE status='approved'").fetchone()["s"]
    wd = db.execute("SELECT COALESCE(SUM(payout_coins),0) s FROM withdrawals WHERE status='approved'").fetchone()["s"]
    tax = (
        db.execute("SELECT COALESCE(SUM(tax_coins),0) s FROM deposits WHERE status='approved'").fetchone()["s"]
        + db.execute("SELECT COALESCE(SUM(tax_coins),0) s FROM withdrawals WHERE status='approved'").fetchone()["s"]
        + db.execute("SELECT COALESCE(SUM(tax_coins),0) s FROM transfers WHERE status='completed'").fetchone()["s"]
    )
    await call.answer()
    await call.message.answer(
        f"📊 <b>Admin Dashboard</b>\n\n"
        f"👥 Users: <b>{users}</b>\n"
        f"💰 Coins in user balances: <b>{balance}</b>\n"
        f"📥 Approved deposits: <b>{dep}</b>\n"
        f"📤 Approved withdrawals: <b>{wd}</b>\n"
        f"💸 Tax collected: <b>{tax}</b>"
    )
    rows = db.execute("SELECT id,username,balance FROM users ORDER BY balance DESC LIMIT 50").fetchall()
    text = "👥 <b>Top user balances</b>\n\n" + "\n".join(
        f"<code>{r['id']}</code> @{r['username'] or '-'} — {r['balance']} coins" for r in rows
    )
    await call.message.answer(text[:4000])

@dp.callback_query(F.data == "adm_tax")
async def adm_tax(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    await call.answer()
    await call.message.answer(
        f"💸 <b>Tax Settings</b>\n\n"
        f"Deposit: {setting('deposit_tax',5):g}%\n"
        f"Withdrawal: {setting('withdraw_tax',5):g}%\n"
        f"Transfer: {setting('transfer_tax',5):g}%\n\n"
        "Use commands:\n"
        "/settax deposit 5\n"
        "/settax withdrawal 5\n"
        "/settax transfer 5"
    )

@dp.message(Command("tax"))
async def cmd_tax(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Admin only."); return
    await message.answer(
        f"💸 Deposit: {setting('deposit_tax',5):g}%\n"
        f"💸 Withdrawal: {setting('withdraw_tax',5):g}%\n"
        f"💸 Transfer: {setting('transfer_tax',5):g}%\n\n"
        "Change with: /settax <deposit|withdrawal|transfer> <0-100>"
    )

@dp.message(Command("settax"))
async def cmd_settax(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Admin only."); return
    p = message.text.split()
    if len(p) != 3 or p[1] not in {"deposit","withdrawal","transfer"}:
        await message.answer("Usage: /settax deposit 5"); return
    try:
        val = float(p[2])
    except ValueError:
        await message.answer("Invalid percentage."); return
    if not 0 <= val <= 100:
        await message.answer("Tax must be between 0 and 100."); return
    set_setting(f"{p[1]}_tax", val)
    await message.answer(f"✅ {p[1].title()} tax set to {val:g}%.")

@dp.callback_query(F.data == "adm_users")
async def adm_users(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    await call.answer()
    await call.message.answer(
        "👥 Use /user <telegram_id> to inspect a user.\n"
        "Use /addcoins <telegram_id> <coins> to add coins.\n"
        "Use /removecoins <telegram_id> <coins> to remove coins."
    )

@dp.message(Command("user"))
async def cmd_user(message: Message):
    if not is_admin(message.from_user.id): return
    p = message.text.split()
    if len(p) != 2:
        await message.answer("Usage: /user <telegram_id>"); return
    try: uid = int(p[1])
    except: await message.answer("Invalid ID."); return
    u = get_user(uid)
    if not u:
        await message.answer("User not found."); return
    await message.answer(
        f"👤 <b>User</b>\nID: <code>{u['id']}</code>\n"
        f"Username: @{u['username'] or '-'}\nBalance: <b>{u['balance']}</b>\n"
        f"Deposited: {u['total_deposited']}\nWithdrawn: {u['total_withdrawn']}\n"
        f"Sent: {u['total_sent']}\nReceived: {u['total_received']}\n"
        f"Referrals: {u['referral_count']}"
    )

@dp.message(Command("addcoins"))
async def cmd_addcoins(message: Message):
    if not is_admin(message.from_user.id): return
    p = message.text.split()
    if len(p) != 3: await message.answer("Usage: /addcoins <id> <coins>"); return
    uid, coins = int(p[1]), int(p[2])
    if coins <= 0: await message.answer("Coins must be positive."); return
    if not get_user(uid): await message.answer("User not found."); return
    change_balance(uid, coins)
    add_transaction(uid, "admin_add", coins, 0, coins, "completed", f"admin:{message.from_user.id}")
    await message.answer("✅ Coins added.")

@dp.message(Command("removecoins"))
async def cmd_removecoins(message: Message):
    if not is_admin(message.from_user.id): return
    p = message.text.split()
    if len(p) != 3: await message.answer("Usage: /removecoins <id> <coins>"); return
    uid, coins = int(p[1]), int(p[2])
    if coins <= 0: await message.answer("Coins must be positive."); return
    u = get_user(uid)
    if not u or u["balance"] < coins: await message.answer("User not found or insufficient balance."); return
    change_balance(uid, -coins)
    add_transaction(uid, "admin_remove", coins, 0, -coins, "completed", f"admin:{message.from_user.id}")
    await message.answer("✅ Coins removed.")

@dp.callback_query(F.data == "adm_promo")
async def adm_promo(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    await call.answer()
    await call.message.answer(
        "🎟 Promo:\n/createpromo CODE COINS LIMIT YYYY-MM-DD\n"
        "Example: /createpromo WELCOME500 500 100 2026-12-31\n"
        "LIMIT 0 = unlimited.\nUse /promos to list active codes."
    )

@dp.message(Command("createpromo"))
async def createpromo(message: Message):
    if not is_admin(message.from_user.id): return
    p = message.text.split()
    if len(p) not in (4,5):
        await message.answer("Usage: /createpromo CODE COINS LIMIT [YYYY-MM-DD]"); return
    code = p[1].upper()
    try:
        coins, limit = int(p[2]), int(p[3])
    except:
        await message.answer("Coins/limit must be numbers."); return
    expiry = None
    if len(p) == 5:
        try:
            expiry = datetime.fromisoformat(p[4] + "T23:59:59+00:00").isoformat()
        except:
            await message.answer("Invalid expiry date."); return
    if coins <= 0 or limit < 0:
        await message.answer("Invalid promo values."); return
    try:
        db.execute("""INSERT INTO promos(code,coins,usage_limit,expires_at,created_at)
                      VALUES(?,?,?,?,?)""", (code,coins,limit,expiry,now()))
        db.commit()
    except sqlite3.IntegrityError:
        await message.answer("Promo already exists."); return
    await message.answer(f"✅ Promo {code} created for {coins} coins.")

@dp.message(Command("promos"))
async def promos(message: Message):
    if not is_admin(message.from_user.id): return
    rows = db.execute("SELECT * FROM promos ORDER BY id DESC LIMIT 50").fetchall()
    if not rows: await message.answer("No promos."); return
    await message.answer("\n".join(
        f"{r['code']} — {r['coins']} coins — used {r['used_count']}/{r['usage_limit'] or '∞'} — "
        f"{'ON' if r['active'] else 'OFF'}" for r in rows
    ))

@dp.callback_query(F.data == "adm_bonus")
async def adm_bonus(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    await call.answer()
    await call.message.answer(
        f"🎁 Daily start: {setting('daily_start',100):g}\n"
        f"7-day reset start: {setting('daily_reset_start',50):g}\n"
        "Use /setbonus start 100\n/setbonus reset 50"
    )

@dp.message(Command("setbonus"))
async def setbonus(message: Message):
    if not is_admin(message.from_user.id): return
    p = message.text.split()
    if len(p)!=3 or p[1] not in {"start","reset"}:
        await message.answer("Usage: /setbonus start 100"); return
    try: val=int(p[2])
    except: await message.answer("Invalid amount."); return
    if val < 0: await message.answer("Invalid amount."); return
    set_setting("daily_start" if p[1]=="start" else "daily_reset_start", val)
    await message.answer("✅ Bonus setting updated.")

@dp.callback_query(F.data == "adm_broadcast")
async def adm_broadcast(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    await call.answer()
    await state.set_state(BroadcastState.text)
    await call.message.answer("📢 Send broadcast text:")

@dp.message(BroadcastState.text)
async def do_broadcast(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    users = db.execute("SELECT id FROM users").fetchall()
    ok = bad = 0
    for r in users:
        try:
            await bot.send_message(r["id"], message.text)
            ok += 1
        except:
            bad += 1
    await state.clear()
    await message.answer(f"📢 Broadcast done.\nSent: {ok}\nFailed: {bad}")

@dp.message(Command("referral"))
async def referral(message: Message):
    if not await send_gate(message, bot): return
    link = f"https://t.me/{(await bot.get_me()).username}?start=ref_{message.from_user.id}"
    await message.answer(
        f"🤝 <b>Your Referral Link</b>\n\n<code>{link}</code>\n\n"
        f"Referrer reward: {int(setting('referrer_bonus',500))} coins\n"
        f"New user reward: {int(setting('new_user_bonus',100))} coins"
    )
async def main():
    init_db()
    if BOT_TOKEN == "PUT_BOT_TOKEN_HERE":
        raise RuntimeError("Set BOT_TOKEN environment variable.")
    me = await bot.get_me()
    log.info("Starting @%s", me.username)
    await dp.start_polling(bot)

if __name__ == "__main__":
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(admin_actions, pattern="^(app|rej)_"))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    print("Sparks Bot successfully start ho gaya hai...")
    
    # Webhook hata kar polling start karne ke liye
    application.run_polling(drop_pending_updates=True)
    
