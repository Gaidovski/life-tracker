import os
from dotenv import load_dotenv
load_dotenv()
import logging
import uuid
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials

# === КОНФИГ ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
CREDENTIALS_FILE = "/home/ubuntu/life-tracker/google_credentials.json"

# === СТЕЙТЫ ДИАЛОГА ===
CHOOSE_BIG_CATEGORY, CHOOSE_CATEGORY, CHOOSE_DATE, CHOOSE_CUSTOM_DATE, \
CHOOSE_TIME, ENTER_START, ENTER_END, ENTER_DURATION, ENTER_NOTE = range(9)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_sheets_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    return gspread.authorize(creds)

def get_categories():
    client = get_sheets_client()
    sheet = client.open_by_key(SPREADSHEET_ID)
    ws = sheet.worksheet("categories_dict")
    rows = ws.get_all_records()
    result = {}
    for row in rows:
        big = row.get("big_category", "").strip()
        cat = row.get("category", "").strip()
        if big and cat:
            result.setdefault(big, []).append(cat)
    return result

def write_event(data: dict):
    client = get_sheets_client()
    sheet = client.open_by_key(SPREADSHEET_ID)
    ws = sheet.worksheet("events")
    if ws.row_count == 0 or ws.acell("A1").value is None:
        ws.append_row([
            "event_id", "created_at", "dt", "big_category",
            "category", "amount", "started_at", "ended_at",
            "duration_min", "note"
        ])
    ws.append_row([
        data.get("event_id", ""),
        data.get("created_at", ""),
        data.get("dt", ""),
        data.get("big_category", ""),
        data.get("category", ""),
        data.get("amount", 1),
        data.get("started_at", ""),
        data.get("ended_at", ""),
        data.get("duration_min", ""),
        data.get("note", ""),
    ])

def fmt_summary(data: dict) -> str:
    lines = [
        f"📌 *Категория:* {data.get('big_category')} / {data.get('category')}",
        f"📅 *Дата:* {data.get('dt')}",
    ]
    if data.get("started_at"):
        lines.append(f"🕐 *Начало:* {data['started_at']}")
    if data.get("ended_at"):
        lines.append(f"🕑 *Конец:* {data['ended_at']}")
    if data.get("duration_min"):
        lines.append(f"⏱ *Длительность:* {data['duration_min']} мин")
    if data.get("note"):
        lines.append(f"💬 *Заметка:* {data['note']}")
    return "\n".join(lines)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    try:
        categories = get_categories()
    except Exception as e:
        await update.message.reply_text(f"Ошибка загрузки категорий: {e}")
        return ConversationHandler.END
    context.user_data["categories"] = categories
    big_cats = list(categories.keys())
    keyboard = [[InlineKeyboardButton(bc, callback_data=f"big|{bc}")] for bc in big_cats]
    await update.message.reply_text(
        "Привет! Что логируем?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSE_BIG_CATEGORY

async def choose_big_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    big_cat = query.data.split("|", 1)[1]
    context.user_data["big_category"] = big_cat
    cats = context.user_data["categories"].get(big_cat, [])
    keyboard = [[InlineKeyboardButton(c, callback_data=f"cat|{c}")] for c in cats]
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back|big")])
    await query.edit_message_text(
        f"*{big_cat}* — выбери категорию:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CHOOSE_CATEGORY

async def choose_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "back|big":
        categories = context.user_data["categories"]
        big_cats = list(categories.keys())
        keyboard = [[InlineKeyboardButton(bc, callback_data=f"big|{bc}")] for bc in big_cats]
        await query.edit_message_text("Что логируем?", reply_markup=InlineKeyboardMarkup(keyboard))
        return CHOOSE_BIG_CATEGORY
    cat = query.data.split("|", 1)[1]
    context.user_data["category"] = cat
    keyboard = [
        [InlineKeyboardButton("Сегодня", callback_data="date|today")],
        [InlineKeyboardButton("Вчера", callback_data="date|yesterday")],
        [InlineKeyboardButton("Другая дата", callback_data="date|custom")],
    ]
    await query.edit_message_text(
        f"Выбрано: *{context.user_data['big_category']} / {cat}*\n\nКогда было событие?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CHOOSE_DATE

async def choose_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.split("|", 1)[1]
    if choice == "today":
        context.user_data["dt"] = datetime.now().strftime("%Y-%m-%d")
        return await ask_time(query, context)
    elif choice == "yesterday":
        context.user_data["dt"] = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        return await ask_time(query, context)
    elif choice == "custom":
        await query.edit_message_text("Введи дату в формате ДД.ММ.ГГГГ, например 01.03.2026:")
        return CHOOSE_CUSTOM_DATE

async def choose_custom_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        dt_obj = datetime.strptime(text, "%d.%m.%Y")
        context.user_data["dt"] = dt_obj.strftime("%Y-%m-%d")
    except ValueError:
        await update.message.reply_text("Неверный формат. Введи дату как ДД.ММ.ГГГГ:")
        return CHOOSE_CUSTOM_DATE
    keyboard = [
        [InlineKeyboardButton("Указать время", callback_data="time|yes")],
        [InlineKeyboardButton("Пропустить", callback_data="time|no")],
    ]
    await update.message.reply_text(
        "Добавить время начала и конца?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSE_TIME

async def ask_time(query, context):
    keyboard = [
        [InlineKeyboardButton("Указать время", callback_data="time|yes")],
        [InlineKeyboardButton("Пропустить", callback_data="time|no")],
    ]
    await query.edit_message_text(
        f"Дата: *{context.user_data['dt']}*\n\nДобавить время начала и конца?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CHOOSE_TIME

async def choose_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.split("|", 1)[1]
    if choice == "no":
        await query.edit_message_text("Добавь заметку (или напиши /skip чтобы пропустить):")
        return ENTER_NOTE
    else:
        await query.edit_message_text("Введи время начала в формате ЧЧ:ММ, например 18:30:")
        return ENTER_START

async def enter_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        datetime.strptime(text, "%H:%M")
        context.user_data["started_at"] = text
    except ValueError:
        await update.message.reply_text("Неверный формат. Введи как ЧЧ:ММ:")
        return ENTER_START
    await update.message.reply_text("Введи время конца в формате ЧЧ:ММ (или /skip):")
    return ENTER_END

async def enter_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() == "/skip":
        await update.message.reply_text("Добавь заметку (или /skip):")
        return ENTER_NOTE
    text = update.message.text.strip()
    try:
        t_start = datetime.strptime(context.user_data["started_at"], "%H:%M")
        t_end = datetime.strptime(text, "%H:%M")
        duration = int((t_end - t_start).total_seconds() / 60)
        context.user_data["ended_at"] = text
        context.user_data["duration_min"] = duration if duration > 0 else ""
    except ValueError:
        await update.message.reply_text("Неверный формат. Введи как ЧЧ:ММ:")
        return ENTER_END
    await update.message.reply_text("Добавь заметку (или /skip):")
    return ENTER_NOTE

async def enter_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text != "/skip":
        context.user_data["note"] = text
    return await save_event(update, context)

async def save_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = {
        "event_id": str(uuid.uuid4()),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dt": context.user_data.get("dt", ""),
        "big_category": context.user_data.get("big_category", ""),
        "category": context.user_data.get("category", ""),
        "amount": 1,
        "started_at": context.user_data.get("started_at", ""),
        "ended_at": context.user_data.get("ended_at", ""),
        "duration_min": context.user_data.get("duration_min", ""),
        "note": context.user_data.get("note", ""),
    }
    try:
        write_event(data)
        summary = fmt_summary(data)
        await update.message.reply_text(
            f"✅ *Записано!*\n\n{summary}\n\nНажми /start чтобы добавить ещё.",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка записи: {e}")
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено. Нажми /start чтобы начать заново.")
    return ConversationHandler.END

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSE_BIG_CATEGORY: [CallbackQueryHandler(choose_big_category, pattern="^big\\|")],
            CHOOSE_CATEGORY: [CallbackQueryHandler(choose_category)],
            CHOOSE_DATE: [CallbackQueryHandler(choose_date, pattern="^date\\|")],
            CHOOSE_CUSTOM_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_custom_date)],
            CHOOSE_TIME: [CallbackQueryHandler(choose_time, pattern="^time\\|")],
            ENTER_START: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_start)],
            ENTER_END: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_end),
                CommandHandler("skip", enter_end),
            ],
            ENTER_NOTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_note),
                CommandHandler("skip", enter_note),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)
    logger.info("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
ENDOFFILE
