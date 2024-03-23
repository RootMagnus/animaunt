import asyncio
import os

import aiohttp
import motor.motor_asyncio
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import FSInputFile
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver import FirefoxOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

BOT_TOKEN = "" # Токен бота

API_SERVER_IP = "127.0.0.1:8081" # ip адрес API сервера telegram

CHAT_ID = 0 # id чата для отправки серий

mongo_url = "" # Ссылка на подключение к mongodb
claster_name = "" # имя кластера, к которому подключаетесь
db_name = "" # название базы данных, в которую будут записываться аниме

"""Нужен, чтобы загружать файлы больших размеров в телеграм. Можно убрать"""
session = AiohttpSession(
    api=TelegramAPIServer.from_base(API_SERVER_IP)
)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML", session=session)

database = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)["db_name"]
animaunt_db = database[db_name]

async def animaunt_series(bot):
    session = aiohttp.ClientSession()
    opts = FirefoxOptions()
    opts.add_argument("--headless")
    driver = webdriver.Firefox(options=opts)
    async with session.get("https://animaunt.org/anime/") as response:
        soup = BeautifulSoup(await response.text(), "lxml")
        for element in soup.findAll("a", class_="th-img img-resp-v with-mask")[:10]:
            anime_name = element.img.get("alt")

            if not await animaunt_db.find_one({"name": anime_name}):
                await animaunt_db.insert_one({"name": anime_name, "series": {}})

            driver.get(element.get("href"))

            try:
                WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.CLASS_NAME, "extra-series-item")))
            except Exception as e:
                print(e)
                continue

            series_list = driver.find_elements(By.CLASS_NAME, "extra-series-item")

            temp_list = []

            finded = await animaunt_db.find_one({"anime_name": anime_name})

            if finded["series"]:
                last_db_series = list(finded["series"].keys())[-1]
            else:
                last_db_series = series_list[-1].text

            for butt_name in series_list:
                temp_list.append(butt_name.text)

            index_temp = temp_list.index(last_db_series)
            temp_list = temp_list[index_temp:]
            for butt_name in series_list:
                if butt_name.text not in temp_list:
                    continue
                butt_name.click()
                series_num_text = butt_name.text

                if series_num_text in finded["series"]:
                    continue

                driver.switch_to.frame(0)
                el_video = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.TAG_NAME, "video")))
                url = el_video.get_attribute("src")

                try:
                    response_video = await session.head(url)
                except Exception as e:
                    print(e)
                    continue
                """Скачиваем видео в 4 потока потоки. Просто так было в моем боте, который @animevost_org_bot"""
                num_threads = 4
                headers = response_video.headers
                file_size = int(headers['Content-Length'])
                part_size = file_size // num_threads
                file_name = "video.mp4"
                file = open(file_name, 'wb')
                tasks = []
                for i in range(num_threads):
                    start = i * part_size
                    end = start + part_size - 1

                    if i == num_threads - 1:
                        end = file_size - 1

                    task = asyncio.create_task(download_range(session, url, start, end, file))
                    tasks.append(task)

                await asyncio.gather(*tasks)

                file.close()

                msg = await bot.send_video(CHAT_ID,
                                           video=FSInputFile(file_name),
                                           caption=f"{anime_name} ({series_num_text})",
                                           width=1280,
                                           height=720,
                                           supports_streaming=True,
                                           duration=video_duration(file_name),
                                           request_timeout=600
                                           )

                await animaunt_db.update_one({"name": anime_name},
                                             {"$set": {f"series.{series_num_text}": msg.video.file_id}})
                os.remove(file_name)

    driver.close()


asyncio.run(animaunt_series(bot))


async def download_range(session, url, start, end, file):
    """
    :param session: Сессия aiohttp
    :param url: Ссылка на видео
    :param start: Начальная точка скачивания
    :param end: Конечная точка скачивания
    :param file: В какой файл записывать
    """
    headers = {'Range': f'bytes={start}-{end}'}
    async with session.get(url, headers=headers) as response:
        data = await response.read()
        file.seek(start)
        file.write(data)


def video_duration(filename):
    """
    Можно убрать, но пропадет отображение продолжительности серии. Также нужно убрать в отправке сообщения
    :param filename: Имя файла
    :return: Продолжительность видео
    """
    import cv2
    video = cv2.VideoCapture(filename)
    frame_count = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = video.get(cv2.CAP_PROP_FPS)
    duration = frame_count / fps
    video.release()
    try:
        return int(duration)
    except Exception as e:
        print(e)
        return 0
