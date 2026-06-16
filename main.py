import logging
import os
import sys
import time
from zoneinfo import ZoneInfo
import asyncio

from telethon import TelegramClient, events
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import constants as c
import resources.Environment as Env
from src.chat.manage_message import (
    manage_regular as manage_regular_message,
    manage_callback as manage_callback_message,
)
from src.service.message_service import full_message_send
from src.service.timer_service import set_timers


async def chat_id(event) -> None:
    """
    Send chat id of current chat
    :param event: Telethon event
    :return: None
    """
    client = event.client
    await full_message_send(client, str(event.chat_id), event)


def pre_init():
    """
    Pre init checks
    :return: None, raises Exception if something is wrong
    """

    # Check that all the required environment variables are set
    for env in Env.Environment.instances:
        env.get()


async def post_init(client: TelegramClient, scheduler: AsyncIOScheduler) -> None:
    """
    Post init
    :param client: the telethon client
    :param scheduler: the APScheduler instance
    :return: None
    """
    await set_timers(client, scheduler)


async def async_main() -> None:
    # Set timezone: Only on linux
    os.environ["TZ"] = Env.TZ.get()
    try:
        time.tzset()
    except AttributeError:
        pass

    # Pre init checks
    pre_init()

    if Env.DB_LOG_QUERIES.get_bool():
        # Set Peewee logger
        logger = logging.getLogger("peewee")
        logger.addHandler(logging.StreamHandler())
        logger.setLevel(logging.DEBUG)

    # Disable httpx logging info
    logging.getLogger("httpx").setLevel(logging.WARNING)

    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.getLevelName(Env.LOG_LEVEL.get()),
        stream=sys.stdout,
    )

    # Sentry
    if Env.SENTRY_ENABLED.get_bool():
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=Env.SENTRY_DSN.get(),
            environment=Env.SENTRY_ENVIRONMENT.get(),
            enable_tracing=Env.SENTRY_ENABLE_TRACING.get_bool(),
            traces_sample_rate=Env.SENTRY_TRACES_SAMPLE_RATE.get_float(),
            profiles_sample_rate=Env.SENTRY_PROFILES_SAMPLE_RATE.get_float(),
            integrations=[
                LoggingIntegration(
                    level=logging.getLevelName(Env.SENTRY_LOG_LEVEL.get()),
                    event_level=logging.getLevelName(Env.SENTRY_LOG_EVENT_LEVEL.get()),
                ),
            ],
        )

    api_id = Env.API_ID.get_int() if hasattr(Env, 'API_ID') else 12345
    api_hash = Env.API_HASH.get() if hasattr(Env, 'API_HASH') else '0123456789abcdef0123456789abcdef'
    bot_token = Env.BOT_TOKEN.get()

    client = TelegramClient('bot_session', api_id, api_hash)

    # ====================================================
    # PTB COMPATIBILITY PATCH FOR MESSAGE HANDLERS
    # ====================================================
    class MockApplication:
        def create_task(self, coro):
            return asyncio.create_task(coro)

    client.application = MockApplication()
    client.bot = client
    # ====================================================

    # Activate timers logging configuration
    logging.getLogger("apscheduler.executors.default").propagate = False

    # Initialize and start APScheduler
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(Env.TZ.get()))
    scheduler.start()

    await client.start(bot_token=bot_token)
    await post_init(client, scheduler)

    # Chat id handler
    @client.on(events.NewMessage(pattern='/chatid'))
    async def chat_id_handler(event):
        await chat_id(event)

    # Regular message handler
    @client.on(events.NewMessage())
    async def new_message_handler(event):
        if event.text == '/chatid':
            return
        await manage_regular_message(event, client)

    # Callback query handler
    @client.on(events.CallbackQuery())
    async def callback_query_handler(event):
        await manage_callback_message(event, client)

    # Inline query handler
    @client.on(events.InlineQuery())
    async def inline_query_handler(event):
        await manage_regular_message(event, client) 

    await client.run_until_disconnected()


def main() -> None:
    """
    Main function. Starts the bot
    :return: None
    """
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
