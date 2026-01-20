import os
import re
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Пути к файлам данных
DATA_DIR = Path("bot_data")
DATA_DIR.mkdir(exist_ok=True)

BASE_FILE = DATA_DIR / "base.json"
REPORT_FILE = DATA_DIR / "current_report.json"
EXCHANGE_RATE_FILE = DATA_DIR / "exchange_rate.json"

# Инициализация файлов данных
def init_data_files():
    """Инициализирует файлы данных если они не существуют"""
    if not BASE_FILE.exists():
        save_json(BASE_FILE, {})
    if not EXCHANGE_RATE_FILE.exists():
        save_json(EXCHANGE_RATE_FILE, {"rate": 88.0})

def save_json(filepath: Path, data: dict):
    """Сохраняет данные в JSON файл"""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_json(filepath: Path) -> dict:
    """Загружает данные из JSON файла"""
    if not filepath.exists():
        return {}
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def parse_entry(text: str) -> Optional[Tuple[float, int, str]]:
    """
    Парсит запись в формате: выручка [количество] модель#
    Возвращает (выручка, количество, модель) или None если не удалось распарсить
    """
    text = text.strip()
    if not text:
        return None
    
    # Регулярное выражение для парсинга
    pattern = r'^([\d.]+)\s+(?:(\d+)\s+)?(.+#)$'
    match = re.match(pattern, text)
    
    if not match:
        return None
    
    try:
        revenue = float(match.group(1))
        quantity = int(match.group(2)) if match.group(2) else 1
        model = match.group(3).strip()
        
        return (revenue, quantity, model)
    except (ValueError, AttributeError):
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start - справка"""
    help_text = """🤖 **Telegram-бот для расчета прибыли**

📋 **Доступные команды:**
• `/base` - показать базу товаров или добавить новые
• `/rate [число]` - показать/установить курс обмена
• `/start_report` - начать отчет
• `/end_report` - завершить отчет и получить результаты

📝 **Формат добавления товара:**
`модель#:цена`

📊 **Формат записи о продаже:**
`выручка [количество] модель#`

**Пример:**
```
/start_report
800 2 махрп#
550 врн#
/end_report
```"""
    
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    logger.info(f"User {update.effective_user.id} started bot")

async def cmd_base(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /base - управление базой товаров"""
    base = load_json(BASE_FILE)
    
    if not base:
        await update.message.reply_text("📦 База товаров пуста.\n\nОтправьте товары в формате:\n`модель#:цена`")
        return
    
    # Показываем текущую базу
    base_text = "📦 **Текущая база товаров:**\n\n"
    for model, price in sorted(base.items()):
        base_text += f"`{model}` - ${price}\n"
    
    base_text += "\n💡 Чтобы добавить новые товары, отправьте в формате:\n`модель#:цена`"
    
    await update.message.reply_text(base_text, parse_mode=ParseMode.MARKDOWN)
    logger.info(f"User {update.effective_user.id} viewed base")

async def cmd_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /rate - управление курсом обмена"""
    rate_data = load_json(EXCHANGE_RATE_FILE)
    current_rate = rate_data.get("rate", 88.0)
    
    if context.args:
        try:
            new_rate = float(context.args[0])
            save_json(EXCHANGE_RATE_FILE, {"rate": new_rate})
            await update.message.reply_text(f"✅ Курс обновлен: 1$ = {new_rate} сом")
            logger.info(f"User {update.effective_user.id} set rate to {new_rate}")
        except ValueError:
            await update.message.reply_text("❌ Ошибка: укажите число")
    else:
        await update.message.reply_text(f"💱 Текущий курс: 1$ = {current_rate} сом\n\nЧтобы изменить, отправьте:\n`/rate 88.5`", parse_mode=ParseMode.MARKDOWN)

async def cmd_start_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start_report - начало отчета"""
    context.user_data['report_active'] = True
    context.user_data['entries'] = []
    
    await update.message.reply_text("✅ Отчет начат!\n\nОтправляйте записи в формате:\n`выручка [количество] модель#`\n\nКогда закончите, отправьте `/end_report`", parse_mode=ParseMode.MARKDOWN)
    logger.info(f"User {update.effective_user.id} started report")

async def cmd_end_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /end_report - завершение отчета"""
    if not context.user_data.get('report_active'):
        await update.message.reply_text("❌ Отчет не был начат. Используйте `/start_report`", parse_mode=ParseMode.MARKDOWN)
        return
    
    entries = context.user_data.get('entries', [])
    if not entries:
        await update.message.reply_text("❌ Нет записей в отчете")
        return
    
    base = load_json(BASE_FILE)
    rate_data = load_json(EXCHANGE_RATE_FILE)
    rate = rate_data.get("rate", 88.0)
    
    # Проверяем все модели
    missing_models = set()
    for revenue, quantity, model in entries:
        if model not in base:
            missing_models.add(model)
    
    if missing_models:
        await update.message.reply_text(f"❌ Модели не найдены в базе:\n{', '.join(missing_models)}")
        return
    
    # Расчеты
    details = []
    model_summary = defaultdict(lambda: {'quantity': 0, 'revenue': 0, 'cost_usd': 0})
    
    total_revenue = 0
    total_cost_som = 0
    total_profit = 0
    
    for revenue, quantity, model in entries:
        cost_usd = base[model]
        cost_som = cost_usd * rate * quantity
        profit = revenue - cost_som
        margin = (profit / revenue * 100) if revenue > 0 else 0
        
        details.append({
            'model': model,
            'quantity': quantity,
            'revenue': revenue,
            'cost_usd': cost_usd,
            'cost_som': cost_som,
            'profit': profit,
            'margin': margin
        })
        
        model_summary[model]['quantity'] += quantity
        model_summary[model]['revenue'] += revenue
        model_summary[model]['cost_usd'] += cost_usd * quantity
        
        total_revenue += revenue
        total_cost_som += cost_som
        total_profit += profit
    
    # Таблица 1: Детали продаж
    table1 = "📊 **Таблица 1: Детали продаж**\n\n"
    table1 += "```\n"
    table1 += f"{'Модель':<15} {'Кол-во':>7} {'Выручка':>10} {'Себест. ($)':>12} {'Себест. (сом)':>14} {'Прибыль':>10} {'Маржа %':>8}\n"
    table1 += "-" * 86 + "\n"
    
    for detail in details:
        table1 += f"{detail['model']:<15} {detail['quantity']:>7} {detail['revenue']:>10.0f} {detail['cost_usd']:>12.2f} {detail['cost_som']:>14.2f} {detail['profit']:>10.2f} {detail['margin']:>7.1f}%\n"
    
    total_margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0
    table1 += "-" * 86 + "\n"
    table1 += f"{'ИТОГО':<15} {sum(e[1] for e in entries):>7} {total_revenue:>10.0f} {'':<12} {total_cost_som:>14.2f} {total_profit:>10.2f} {total_margin:>7.1f}%\n"
    table1 += "```\n"
    
    # Таблица 2: Сводка по моделям
    table2 = "\n📊 **Таблица 2: Сводка по моделям**\n\n"
    table2 += "```\n"
    table2 += f"{'Модель':<15} {'Кол-во':>7} {'Выручка':>10} {'Себест. (сом)':>14} {'Прибыль':>10} {'Маржа %':>8}\n"
    table2 += "-" * 74 + "\n"
    
    for model in sorted(model_summary.keys()):
        summary = model_summary[model]
        cost_som = summary['cost_usd'] * rate
        profit = summary['revenue'] - cost_som
        margin = (profit / summary['revenue'] * 100) if summary['revenue'] > 0 else 0
        
        table2 += f"{model:<15} {summary['quantity']:>7} {summary['revenue']:>10.0f} {cost_som:>14.2f} {profit:>10.2f} {margin:>7.1f}%\n"
    
    table2 += "-" * 74 + "\n"
    total_margin_summary = (total_profit / total_revenue * 100) if total_revenue > 0 else 0
    table2 += f"{'ИТОГО':<15} {sum(e[1] for e in entries):>7} {total_revenue:>10.0f} {total_cost_som:>14.2f} {total_profit:>10.2f} {total_margin_summary:>7.1f}%\n"
    table2 += "```\n"
    
    # Отправляем результаты
    result_text = table1 + table2
    
    # Разбиваем на части если слишком большое сообщение
    if len(result_text) > 4000:
        await update.message.reply_text(table1, parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text(table2, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(result_text, parse_mode=ParseMode.MARKDOWN)
    
    # Сохраняем отчет
    report_data = {
        'timestamp': datetime.now().isoformat(),
        'entries': entries,
        'details': details,
        'summary': dict(model_summary),
        'totals': {
            'revenue': total_revenue,
            'cost_som': total_cost_som,
            'profit': total_profit,
            'margin': total_margin
        }
    }
    save_json(REPORT_FILE, report_data)
    
    # Завершаем отчет
    context.user_data['report_active'] = False
    context.user_data['entries'] = []
    
    await update.message.reply_text("✅ Отчет завершен и сохранен!")
    logger.info(f"User {update.effective_user.id} completed report with {len(entries)} entries")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка обычных сообщений"""
    if not context.user_data.get('report_active'):
        await update.message.reply_text("❌ Отчет не активен. Используйте `/start_report` чтобы начать", parse_mode=ParseMode.MARKDOWN)
        return
    
    text = update.message.text
    
    # Проверяем формат добавления товара
    if ':' in text and text.endswith('#'):
        # Это добавление товара
        parts = text.split(':')
        if len(parts) == 2:
            model = parts[0].strip()
            try:
                price = float(parts[1].strip())
                base = load_json(BASE_FILE)
                base[model] = price
                save_json(BASE_FILE, base)
                await update.message.reply_text(f"✅ Товар добавлен: {model} - ${price}")
                logger.info(f"User {update.effective_user.id} added product {model}")
                return
            except ValueError:
                pass
    
    # Парсим запись о продаже
    parsed = parse_entry(text)
    
    if not parsed:
        await update.message.reply_text("❌ Ошибка парсинга. Формат: `выручка [количество] модель#`", parse_mode=ParseMode.MARKDOWN)
        return
    
    revenue, quantity, model = parsed
    
    # Проверяем модель в базе
    base = load_json(BASE_FILE)
    if model not in base:
        await update.message.reply_text(f"❌ Модель `{model}` не найдена в базе", parse_mode=ParseMode.MARKDOWN)
        return
    
    # Добавляем запись
    context.user_data['entries'].append((revenue, quantity, model))
    
    cost_usd = base[model]
    rate_data = load_json(EXCHANGE_RATE_FILE)
    rate = rate_data.get("rate", 88.0)
    cost_som = cost_usd * rate * quantity
    profit = revenue - cost_som
    margin = (profit / revenue * 100) if revenue > 0 else 0
    
    await update.message.reply_text(
        f"✅ Запись добавлена:\n"
        f"Модель: `{model}`\n"
        f"Кол-во: {quantity}\n"
        f"Выручка: {revenue} сом\n"
        f"Себестоимость: {cost_som:.2f} сом\n"
        f"Прибыль: {profit:.2f} сом\n"
        f"Маржа: {margin:.1f}%",
        parse_mode=ParseMode.MARKDOWN
    )
    logger.info(f"User {update.effective_user.id} added entry: {revenue} {quantity} {model}")

async def main():
    """Главная функция"""
    init_data_files()
    
    # Получаем токен из переменной окружения
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN не установлен!")
        return
    
    # Создаем приложение
    app = Application.builder().token(token).build()
    
    # Добавляем обработчики команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("base", cmd_base))
    app.add_handler(CommandHandler("rate", cmd_rate))
    app.add_handler(CommandHandler("start_report", cmd_start_report))
    app.add_handler(CommandHandler("end_report", cmd_end_report))
    
    # Обработчик обычных сообщений
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("🤖 Бот запущен!")
    
    # Запускаем бота
    await app.run_polling()

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
