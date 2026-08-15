# Sparks Coin Bot

Python + aiogram 3.30.0 + SQLite.

## Install
python3 -m pip install -r requirements.txt

## Configure
Set BOT_TOKEN as an environment variable.

IMPORTANT:
- Replace/add the real numeric Telegram ID of the second admin in `ADMIN_IDS`.
- Add the bot as an administrator to all 5 required channels so membership checks can work reliably.
- This code uses Telegram User IDs for admin authorization, not usernames.

## Run
python3 bot.py

## Main user commands
/start
/daily
/deposit
/withdrawal
/transfer
/dash
/history
/referral
/promo
/help
/admin

## Admin
/admin
/tax
/settax deposit 5
/settax withdrawal 5
/settax transfer 5
/createpromo CODE COINS LIMIT YYYY-MM-DD
/promos
/setbonus start 100
/setbonus reset 50
/user TELEGRAM_ID
/addcoins TELEGRAM_ID COINS
/removecoins TELEGRAM_ID COINS

## Notes
- ₹1 = 100 Sparks Coin.
- Default deposit/withdrawal/transfer tax = 5%.
- Deposit = ₹1–₹1000.
- P2P minimum = 100 coins.
- Withdrawal minimum = 10,000 coins.
- Withdrawal amount is held immediately; decline refunds it.
- Deposit credits only after admin approval.
- Daily bonus requires a completed deposit or transfer that day.
- Daily cycle is 7 days; after day 7 the next cycle starts at 50 coins.
- History shows the latest 5 transactions.
