import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode

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
    # Формат: число [число] текст#
    pattern = r'^(\d+(?:\.\d+)?)\s+(?:(\d+(?:\.\d+)?)\s+)?(.+?#)$'
    match = re.match(pattern, text)
    
    if not match:
        return None
    
    revenue = float(match.group(1))
    quantity = int(float(match.group(2))) if match.group(2) else 1
    model = match.group(3).strip()
    
    return revenue, quantity, model

def parse_report(text: str) -> List[Tuple[float, int, str]]:
    """Парсит отчет с несколькими записями"""
    entries = []
    lines = text.strip().split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        parsed = parse_entry(line)
        if parsed:
            entries.append(parsed)
    
    return entries

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /start"""
    await update.message.reply_text(
        "Добро пожаловать! Я помогу вам рассчитать прибыль.\n\n"
        "Доступные команды:\n"
        "/base - показать базу товаров\n"
        "/rate - установить курс обмена\n"
        "/start_report - начать новый отчет\n"
        "/end_report - завершить отчет и получить результаты\n\n"
        "Формат записи: выручка [количество] модель#\n"
        "Пример: 800 2 махрп#"
    )

async def cmd_base(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /base - показать или обновить базу товаров"""
    base = load_json(BASE_FILE)
    
    if not base:
        await update.message.reply_text(
            "База товаров пуста.\n\n"
            "Добавьте товары в формате: модель#:себестоимость\n"
            "Пример: махрп#:5.50"
        )
        context.user_data['awaiting_base_input'] = True
        return
    
    # Показываем текущую базу
    base_text = "📦 Текущая база товаров:\n\n"
    for model, cost in sorted(base.items()):
        base_text += f"{model} - ${cost}\n"
    
    base_text += "\n\nДля добавления новых товаров отправьте:\nмодель#:себестоимость"
    await update.message.reply_text(base_text)
    context.user_data['awaiting_base_input'] = True

async def cmd_exchange_rate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /rate - установить курс обмена"""
    if not context.args:
        rate_data = load_json(EXCHANGE_RATE_FILE)
        current_rate = rate_data.get('rate', 88.0)
        await update.message.reply_text(
        f"💱 Текущий курс: 1$ = {current_rate} сом\n\n"
        "Для изменения отправьте: /rate <число>"
        )
        return
    
    try:
        rate = float(context.args[0])
        save_json(EXCHANGE_RATE_FILE, {"rate": rate})
        await update.message.reply_text(f"✅ Курс обновлен: 1$ = {rate} сом")
    except ValueError:
        await update.message.reply_text("❌ Ошибка: введите число")

async def cmd_start_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /start_report - начать новый отчет"""
    context.user_data['report_entries'] = []
    context.user_data['in_report'] = True
    await update.message.reply_text(
        "✅ Отчет начат!\n\n"
        "Отправляйте записи в формате: выручка [количество] модель#\n"
        "Или одним сообщением несколько записей\n\n"
        "Когда закончите, используйте /end_report"
    )

async def cmd_end_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /end_report - завершить отчет и показать результаты"""
    if not context.user_data.get('in_report'):
        await update.message.reply_text("❌ Отчет не начат. Используйте /start_report")
        return
    
    entries = context.user_data.get('report_entries', [])
    
    if not entries:
        await update.message.reply_text("❌ В отчете нет записей")
        return
    
    base = load_json(BASE_FILE)
    rate_data = load_json(EXCHANGE_RATE_FILE)
    exchange_rate = rate_data.get('rate', 88.0)
    
    # Проверяем наличие всех моделей в базе
    missing_models = set()
    for revenue, quantity, model in entries:
        if model not in base:
            missing_models.add(model)
    
    if missing_models:
        await update.message.reply_text(
            f"❌ Ошибка: модели не найдены в базе:\n" +
            "\n".join(sorted(missing_models)) +
            "\n\nДобавьте их через /base"
        )
        return
    
    # Рассчитываем результаты
    details = []  # Детали продаж
    summary = defaultdict(lambda: {"quantity": 0, "revenue": 0, "cost_usd": 0})  # Сводка по моделям
    
    total_quantity = 0
    total_revenue = 0
    total_cost_som = 0
    
    for revenue, quantity, model in entries:
        cost_usd = base[model]
        cost_som = cost_usd * exchange_rate
        total_cost_som_item = cost_som * quantity
        profit_som = revenue - total_cost_som_item
        margin = (profit_som / revenue * 100) if revenue > 0 else 0
        
        details.append({
            "model": model,
            "quantity": quantity,
            "revenue": revenue,
            "cost_usd": cost_usd,
            "cost_som": cost_som,
            "total_cost_som": total_cost_som_item,
            "profit_som": profit_som,
            "margin": margin
        })
        
        # Обновляем сводку
        summary[model]["quantity"] += quantity
        summary[model]["revenue"] += revenue
        summary[model]["cost_usd"] += cost_usd * quantity
        
        total_quantity += quantity
        total_revenue += revenue
        total_cost_som += total_cost_som_item
    
    total_profit = total_revenue - total_cost_som
    total_margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0
    
    # Формируем таблицу "Детали продаж"
    details_text = "📊 ДЕТАЛИ ПРОДАЖ\n"
    details_text += "=" * 80 + "\n"
    details_text += f"{'Модель':<15} {'Кол-во':<8} {'Выручка':<12} {'Себест. ($)':<12} {'Себест. (сом)':<15} {'Прибыль':<12} {'Маржа %':<8}\n"
    details_text += "-" * 80 + "\n"
    
    for item in details:
        details_text += f"{item['model']:<15} {item['quantity']:<8} {item['revenue']:<12.0f} {item['cost_usd']:<12.2f} {item['cost_som']:<15.2f} {item['profit_som']:<12.2f} {item['margin']:<8.1f}\n"
    
    details_text += "-" * 80 + "\n"
    details_text += f"{'ИТОГО':<15} {total_quantity:<8} {total_revenue:<12.0f} {'':<12} {total_cost_som:<15.2f} {total_profit:<12.2f} {total_margin:<8.1f}\n"
    
    # Формируем таблицу "Сводка по моделям"
    summary_text = "\n\n📈 СВОДКА ПО МОДЕЛЯМ\n"
    summary_text += "=" * 80 + "\n"
    summary_text += f"{'Модель':<15} {'Кол-во':<8} {'Выручка':<12} {'Себест. (сом)':<15} {'Прибыль':<12} {'Маржа %':<8}\n"
    summary_text += "-" * 80 + "\n"
    
    for model in sorted(summary.keys()):
        data = summary[model]
        cost_som_total = data["cost_usd"] * exchange_rate
        profit = data["revenue"] - cost_som_total
        margin = (profit / data["revenue"] * 100) if data["revenue"] > 0 else 0
        
        summary_text += f"{model:<15} {data['quantity']:<8} {data['revenue']:<12.0f} {cost_som_total:<15.2f} {profit:<12.2f} {margin:<8.1f}\n"
    
    summary_text += "-" * 80 + "\n"
    summary_text += f"{'ИТОГО':<15} {total_quantity:<8} {total_revenue:<12.0f} {total_cost_som:<15.2f} {total_profit:<12.2f} {total_margin:<8.1f}\n"
    
    # Отправляем результаты
    await update.message.reply_text(details_text, parse_mode=ParseMode.MONOSPACE)
    await update.message.reply_text(summary_text, parse_mode=ParseMode.MONOSPACE)
    
    # Сохраняем отчет
    report_data = {
        "timestamp": datetime.now().isoformat(),
        "exchange_rate": exchange_rate,
        "details": details,
        "summary": {k: dict(v) for k, v in summary.items()},
        "totals": {
            "quantity": total_quantity,
            "revenue": total_revenue,
            "cost_som": total_cost_som,
            "profit": total_profit,
            "margin": total_margin
        }
    }
    save_json(REPORT_FILE, report_data)
    
    # Завершаем отчет
    context.user_data['in_report'] = False
    context.user_data['report_entries'] = []
    await update.message.reply_text("✅ Отчет завершен и сохранен!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка обычных сообщений"""
    text = update.message.text
    
    # Если ожидаем ввод базы товаров
    if context.user_data.get('awaiting_base_input'):
        base = load_json(BASE_FILE)
        
        # Парсим новые товары
        lines = text.strip().split('\n')
        added = []
        errors = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Формат: модель#:себестоимость
            match = re.match(r'^(.+?#):(\d+(?:\.\d+)?)$', line)
            if match:
                model = match.group(1)
                cost = float(match.group(2))
                base[model] = cost
                added.append(f"{model} - ${cost}")
            else:
                errors.append(line)
        
        if added:
            save_json(BASE_FILE, base)
            response = "✅ Добавлено:\n" + "\n".join(added)
            if errors:
                response += "\n\n❌ Ошибки при парсинге:\n" + "\n".join(errors)
            await update.message.reply_text(response)
        elif errors:
            await update.message.reply_text(
                "❌ Не удалось распарсить записи.\n"
                "Формат: модель#:себестоимость\n"
                "Пример: махрп#:5.50"
            )
        
        context.user_data['awaiting_base_input'] = False
        return
    
    # Если в режиме отчета
    if context.user_data.get('in_report'):
        entries = parse_report(text)
        
        if entries:
            context.user_data['report_entries'].extend(entries)
            await update.message.reply_text(f"✅ Добавлено {len(entries)} записей")
        else:
            await update.message.reply_text(
                "❌ Не удалось распарсить записи.\n"
                "Формат: выручка [количество] модель#\n"
                "Пример: 800 2 махрп#"
            )

def main():
    """Запуск бота"""
    init_data_files()
    
    # Получаем токен из переменной окружения
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не установлен")
    
    # Создаем приложение
    app = Application.builder().token(token).build()
    
    # Регистрируем обработчики команд
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("base", cmd_base))
    app.add_handler(CommandHandler("rate", cmd_exchange_rate))
    app.add_handler(CommandHandler("start_report", cmd_start_report))
    app.add_handler(CommandHandler("end_report", cmd_end_report))
    
    # Регистрируем обработчик сообщений
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запускаем бота
    print("🤖 Бот запущен...")
    app.run_polling()

if __name__ == '__main__':
    main()
