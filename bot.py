import os
import logging
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatType
from aiogram.filters import Command
from aiogram.utils.chat_action import ChatActionSender
from aiogram.types import TelegramObject
import httpx
import asyncio

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация переменных окружения
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://127.0.0.1:8080")

if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не задан в переменных окружения!")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

class LoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data):
        print(f"RAW UPDATE PRINT: {event.model_dump_json(exclude_none=True)}", flush=True)
        return await handler(event, data)

dp.message.outer_middleware(LoggingMiddleware())
dp.inline_query.outer_middleware(LoggingMiddleware())
dp.chosen_inline_result.outer_middleware(LoggingMiddleware())

def get_status_text(query: str, step: int, search_queries: list = None) -> str:
    display_query = query[:50] + "..." if len(query) > 50 else query
    if step == 1:
        return (
            f"🔍 <b>Запрос:</b> <code>{display_query}</code>\n\n"
            f"🧠 <b>Статус:</b> Планирование поиска...\n"
            f"📊 <code>[■■□□□□□□□□] 20%</code>"
        )
    elif step == 2:
        queries_str = ""
        if search_queries:
            queries_str = "\n\n<i>Модель решила искать по:</i>"
            for q in search_queries:
                queries_str += f"\n🔍 <code>{q}</code>"
        return (
            f"🔍 <b>Запрос:</b> <code>{display_query}</code>\n\n"
            f"🛰 <b>Статус:</b> Сбор информации SearxNG...\n"
            f"📊 <code>[■■■■■□□□□□] 50%</code>{queries_str}"
        )
    elif step == 3:
        return (
            f"🔍 <b>Запрос:</b> <code>{display_query}</code>\n\n"
            f"🧠 <b>Статус:</b> Анализ и ответ GLM...\n"
            f"📊 <code>[■■■■■■■■□□] 80%</code>"
        )

def clean_model_answer(answer: str) -> str:
    text = answer.strip()
    prefixes = [
        "ответ на запрос:",
        "ответ на вопрос:",
        "ответ пользователя:",
        "ответ:",
        "запрос:",
        "вывод:"
    ]
    changed = True
    while changed:
        changed = False
        lower_text = text.lower()
        for prefix in prefixes:
            if lower_text.startswith(prefix):
                text = text[len(prefix):].strip()
                changed = True
                break
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    elif text.startswith("'") and text.endswith("'"):
        text = text[1:-1].strip()
    return text

async def search_searxng_raw(query: str) -> list:
    url = f"{SEARXNG_URL.rstrip('/')}/search"
    params = {
        "q": query,
        "format": "json",
        "pageno": 1
    }
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except Exception as e:
            logger.error(f"Ошибка при запросе к SearxNG по запросу '{query}': {e}")
            return []

async def ask_ollama(prompt: str, system_prompt: str) -> str:
    url = f"{OLLAMA_BASE_URL.rstrip('/')}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json"
    }
    if OLLAMA_API_KEY:
        headers["Authorization"] = f"Bearer {OLLAMA_API_KEY}"
        
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3
    }
    
    async with httpx.AsyncClient(timeout=45.0) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"Ошибка при запросе к Ollama API: {e}")
            return f"Ошибка генерации ответа от Ollama: {e}"

async def generate_search_queries(query: str) -> list[str]:
    system_prompt = (
        "Ты — эксперт по поиску информации. Проанализируй вопрос пользователя и сгенерируй от 3 до 10 различных поисковых запросов для поисковой системы, "
        "чтобы получить максимально полную, разностороннюю и актуальную информацию из разных источников. "
        "Запросы должны быть лаконичными и уникальными, без использования логических операторов (AND, OR, site: и т.д.). "
        "Выведи только сами поисковые запросы, каждый на новой строке. "
        "Не пиши никаких пояснений, введений, кавычек или номеров списков."
    )
    
    try:
        response = await ask_ollama(prompt=query, system_prompt=system_prompt)
        queries = []
        for line in response.strip().split("\n"):
            line = line.strip().strip('"').strip("'").strip("-").strip("*").strip()
            # Убираем нумерацию вроде "1. ", "2) "
            if line and (line[0].isdigit() or line.startswith("•")):
                while line and (line[0].isdigit() or line[0] in [".", ")", "-", " ", "*", "•"]):
                    line = line[1:].strip()
            if line:
                queries.append(line)
        if not queries:
            queries = [query]
        return queries[:10]
    except Exception as e:
        logger.error(f"Ошибка при генерации поисковых запросов: {e}")
        return [query]

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer(
        "👋 Привет! Я инлайн-агент для поиска информации в интернете.\n\n"
        "Для работы со мной начните писать в любом чате: <code>@имя_этого_бота ваш запрос</code> "
        "и выберите предложенный вариант. Сообщение будет отправлено и динамически обновлено на основе результатов поиска и анализа ИИ."
    )

async def clear_cmd(message: types.Message):
    current_id = message.message_id
    deleted_count = 0
    status = await message.answer("⏳ Очищаю историю сообщений (до 100 недавних)...")
    
    for msg_id in range(current_id, current_id - 100, -1):
        if msg_id == status.message_id:
            continue
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
            deleted_count += 1
        except Exception:
            continue
            
    try:
        await status.edit_text(f"✅ Чат очищен! Удалено сообщений: {deleted_count}")
        await asyncio.sleep(4.0)
        await bot.delete_message(chat_id=message.chat.id, message_id=status.message_id)
    except Exception:
        pass

@dp.inline_query()
async def inline_query_handler(inline_query: types.InlineQuery):
    query = inline_query.query.strip()
    if not query:
        return
    
    result_id = f"search_{inline_query.id}"
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[
            types.InlineKeyboardButton(text="⏳ Идет поиск...", callback_data="loading_dummy")
        ]]
    )
    
    results = [
        types.InlineQueryResultArticle(
            id=result_id,
            title=f"Найти: {query[:45]}",
            description="Запустить глубокий поиск через SearxNG и Ollama...",
            input_message_content=types.InputTextMessageContent(
                message_text=f"🔍 <b>Запрос:</b> {query}\n\n⏳ <i>Инициализация поиска...</i>",
                parse_mode=ParseMode.HTML
            ),
            reply_markup=keyboard
        )
    ]
    
    try:
        await inline_query.answer(results, cache_time=0, is_personal=True)
    except Exception as e:
        logger.error(f"Ошибка отправки инлайн ответа: {e}")

@dp.chosen_inline_result()
async def chosen_inline_result_handler(chosen_inline_result: types.ChosenInlineResult):
    query = chosen_inline_result.query.strip()
    inline_message_id = chosen_inline_result.inline_message_id
    if not inline_message_id:
        logger.warning("inline_message_id отсутствует в ChosenInlineResult")
        return
    
    logger.info(f"Пользователь выбрал результат: '{query}'. ID сообщения: {inline_message_id}")
    asyncio.create_task(process_inline_search(inline_message_id, query))

async def process_inline_search(inline_message_id: str, query: str):
    try:
        # Шаг 1: Планирование поиска (генерация запросов)
        await bot.edit_message_text(
            text=get_status_text(query, 1),
            inline_message_id=inline_message_id,
            parse_mode=ParseMode.HTML
        )
        search_queries = await generate_search_queries(query)
        
        # Шаг 2: Сбор информации в SearxNG
        await bot.edit_message_text(
            text=get_status_text(query, 2, search_queries),
            inline_message_id=inline_message_id,
            parse_mode=ParseMode.HTML
        )
        
        # Запускаем параллельные запросы к SearxNG
        search_tasks = [search_searxng_raw(q) for q in search_queries]
        search_results_list = await asyncio.gather(*search_tasks)
        
        # Объединяем результаты и удаляем дубликаты по URL
        unique_results = {}
        for results in search_results_list:
            for res in results:
                url = res.get("url")
                if url and url not in unique_results:
                    unique_results[url] = res
                    
        # Форматируем контекст для модели (берем до 12 источников)
        formatted_results = []
        for i, res in enumerate(list(unique_results.values())[:12], 1):
            title = res.get("title", "Без названия")
            link = res.get("url", "")
            content = res.get("content", "") or res.get("snippet", "")
            formatted_results.append(
                f"{i}. Источник: {link}\n"
                f"Заголовок: {title}\n"
                f"Текст: {content}\n"
            )
        search_context = "\n".join(formatted_results)
        
        if not search_context.strip():
            search_context = "Результаты поиска отсутствуют."
        
        # Шаг 3: Генерация ответа модели
        await bot.edit_message_text(
            text=get_status_text(query, 3),
            inline_message_id=inline_message_id,
            parse_mode=ParseMode.HTML
        )
        
        system_prompt = (
            "Ты — полезный ИИ-помощник, который отвечает на вопрос пользователя на основе предоставленных данных поиска.\n"
            "Твой ответ должен быть кратким, лаконичным и емким (как обычное короткое сообщение в мессенджере Telegram, максимум 1-3 небольших абзаца).\n"
            "Пиши исключительно на русском языке. Абсолютно НЕ используй никакое форматирование текста (без Markdown, без HTML, без жирного шрифта, курсива, списков и т.д.).\n"
            "Абсолютно НЕ указывай никакие ссылки на источники, URL-адреса или сайты. Пиши только чистый текст ответа.\n"
            "КРИТИЧЕСКИ ВАЖНО: Твой ответ должен быть ТОЛЬКО на русском языке, даже если данные поиска и вопрос на английском или другом языке. Также в ответе должен быть только сам ответ — никогда не пиши вводные слова вроде 'Ответ:', 'Запрос:', 'Ответ на ваш запрос:', не дублируй вопрос пользователя и не используй никакие префиксы. Сразу переходи к сути."
        )
        
        prompt = (
            f"Найденная информация из интернета (используй ее для ответа):\n{search_context}\n\n"
            f"Вопрос пользователя: {query}\n"
        )
        
        answer = await ask_ollama(prompt, system_prompt)
        answer = clean_model_answer(answer)
        
        # Финал
        await bot.edit_message_text(
            text=answer,
            inline_message_id=inline_message_id,
            parse_mode=None,
            reply_markup=None,
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке инлайн-поиска: {e}")
        try:
            await bot.edit_message_text(
                text=f"❌ Произошла ошибка при обработке запроса: <i>{query}</i>\n\nДетали ошибки: {e}",
                inline_message_id=inline_message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=None
            )
        except Exception as edit_err:
            logger.error(f"Не удалось обновить статус ошибки в сообщении: {edit_err}")

@dp.message()
async def private_message_handler(message: types.Message):
    print(f"DEBUG LOG: Получено сообщение! ID: {message.message_id}, Текст: {message.text}, Тип чата: {message.chat.type}", flush=True)
    
    if message.text and message.text.startswith("/"):
        if message.text.startswith("/start"):
            await start_cmd(message)
        elif message.text.startswith("/clear"):
            await clear_cmd(message)
        return
        
    query = message.text.strip() if message.text else ""
    if not query:
        print("DEBUG LOG: Пустой запрос, игнорируем.", flush=True)
        return
    
    status_msg = await message.answer(
        text=get_status_text(query, 1),
        parse_mode=ParseMode.HTML
    )
    
    try:
        async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
            # Шаг 1: Генерация запросов
            search_queries = await generate_search_queries(query)
            
            # Шаг 2: Сбор информации
            await status_msg.edit_text(
                text=get_status_text(query, 2, search_queries),
                parse_mode=ParseMode.HTML
            )
            
            search_tasks = [search_searxng_raw(q) for q in search_queries]
            search_results_list = await asyncio.gather(*search_tasks)
            
            unique_results = {}
            for results in search_results_list:
                for res in results:
                    url = res.get("url")
                    if url and url not in unique_results:
                        unique_results[url] = res
                        
            formatted_results = []
            for i, res in enumerate(list(unique_results.values())[:12], 1):
                title = res.get("title", "Без названия")
                link = res.get("url", "")
                content = res.get("content", "") or res.get("snippet", "")
                formatted_results.append(
                    f"{i}. Источник: {link}\n"
                    f"Заголовок: {title}\n"
                    f"Текст: {content}\n"
                )
            search_context = "\n".join(formatted_results)
            
            if not search_context.strip():
                search_context = "Результаты поиска отсутствуют."
            
            # Шаг 3: Анализ и ответ
            await status_msg.edit_text(
                text=get_status_text(query, 3),
                parse_mode=ParseMode.HTML
            )
            
            system_prompt = (
                "Ты — полезный ИИ-помощник, который отвечает на вопрос пользователя на основе предоставленных данных поиска.\n"
                "Твой ответ должен быть кратким, лаконичным и емким (как обычное короткое сообщение в мессенджере Telegram, максимум 1-3 небольших абзаца).\n"
                "Пиши исключительно на русском языке. Абсолютно НЕ используй никакое форматирование текста (без Markdown, без HTML, без жирного шрифта, курсива, списков и т.д.).\n"
                "Абсолютно НЕ указывай никакие ссылки на источники, URL-адреса или сайты. Пиши только чистый текст ответа.\n"
                "КРИТИЧЕСКИ ВАЖНО: Твой ответ должен быть ТОЛЬКО на русском языке, даже если данные поиска и вопрос на английском или другом языке. Также в ответе должен быть только сам ответ — никогда не пиши вводные слова вроде 'Ответ:', 'Запрос:', 'Ответ на ваш запрос:', не дублируй вопрос пользователя и не используй никакие префиксы. Сразу переходи к сути."
            )
            
            prompt = (
                f"Найденная информация из интернета (используй ее для ответа):\n{search_context}\n\n"
                f"Вопрос пользователя: {query}\n"
            )
            
            answer = await ask_ollama(prompt, system_prompt)
            answer = clean_model_answer(answer)
            
            await status_msg.edit_text(
                text=answer,
                parse_mode=None,
                disable_web_page_preview=True
            )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения в личке: {e}")
        try:
            await status_msg.edit_text(
                text=f"❌ Произошла ошибка при обработке запроса: <i>{query}</i>\n\nДетали ошибки: {e}",
                parse_mode=ParseMode.HTML
            )
        except Exception as edit_err:
            logger.error(f"Не удалось отправить сообщение об ошибке в личке: {edit_err}")

@dp.callback_query()
async def callback_query_handler(callback_query: types.CallbackQuery):
    await callback_query.answer(text="Идет выполнение поиска...", show_alert=False)

async def main():
    logger.info("Регистрация команд в Telegram...")
    await bot.set_my_commands([
        types.BotCommand(command="start", description="Запустить бота"),
        types.BotCommand(command="clear", description="Очистить историю сообщений")
    ])
    logger.info("Запуск Telegram-бота...")
    await dp.start_polling(bot, allowed_updates=["message", "inline_query", "chosen_inline_result", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
