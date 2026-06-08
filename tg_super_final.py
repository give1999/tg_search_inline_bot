import asyncio
from telethon import TelegramClient, errors
import qrcode
import os

API_ID = 33472658
API_HASH = '16bb023064ba37e7ee2f8b79fe9eb5ef'
SESSION_NAME = 'gemini_session'

async def main():
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()
    
    print("Генерация QR-кода...")
    qr_login = await client.qr_login()
    qrcode.make(qr_login.url).save("qr.png")
    os.startfile("qr.png")
    print("\n" + "!"*40)
    print("1. СКАНИРУЙТЕ QR-КОД НА ЭКРАНЕ")
    print("2. ГАДАЙТЕ ПАРОЛЬ ЗДЕСЬ, ПОКА НЕ ВСПОМНИТЕ")
    print("!"*40 + "\n")
    
    try:
        await qr_login.wait()
    except errors.SessionPasswordNeededError:
        print("\nQR ПРИНЯТ! Теперь вводи пароли.")
        
        while True:
            pwd = input("\nВВЕДИ ПАРОЛЬ (или 'exit' для отмены): ")
            if pwd.lower() == 'exit':
                break
            
            try:
                await client.sign_in(password=pwd)
                print("\n" + "="*30)
                me = await client.get_me()
                print(f"ЕСТЬ КОНТАКТ! Вошли как: {me.first_name}")
                print("="*30)
                break
            except errors.PasswordHashInvalidError:
                print("❌ НЕВЕРНЫЙ ПАРОЛЬ. Попробуй еще раз...")
            except Exception as e:
                print(f"Произошла какая-то ошибка: {e}")
                break

    if await client.is_user_authorized():
        print("\nАвторизация завершена. Сессия сохранена.")
    
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
