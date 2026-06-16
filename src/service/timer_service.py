import logging
from datetime import datetime
import asyncio
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telethon import TelegramClient

import src.model.enums.Timer as Timer
from src.chat.manage_message import init, end
from src.model.DailyReward import DailyReward
from src.service.bounty_loan_service import set_expired_bounty_loans
from src.service.bounty_poster_service import reset_bounty_poster_limit
from src.service.devil_fruit_service import schedule_devil_fruit_release, respawn_devil_fruit
from src.service.fight_plunder_service import decrease_scout_count
from src.service.game_service import end_inactive_games
from src.service.generic_service import run_minute_tasks
from src.service.group_service import deactivate_inactive_group_chats
from src.service.leaderboard_service import send_leaderboard
from src.service.location_service import reset_can_change_region
from src.service.prediction_service import (
    send_scheduled_predictions,
    close_scheduled_predictions,
    send_prediction_status_change_message_or_refresh_dispatch,
)
from src.service.reddit_service import manage as send_reddit_post
from src.utils.download_utils import cleanup_temp_dir


# ==========================================
# TELETHON COMPATIBILITY LAYER
# ==========================================
class MockJob:
    def __init__(self, timer: Timer.Timer):
        self.data = timer
        self.name = timer.name

class MockApplication:
    def create_task(self, coro):
        return asyncio.create_task(coro)

class TelethonContext:
    """
    A lightweight wrapper that mimics the python-telegram-bot context 
    so existing downstream services don't crash.
    """
    def __init__(self, client: TelegramClient, timer: Timer.Timer):
        self.client = client
        self.bot = client  # Routes calls expecting context.bot straight to Telethon
        self.job = MockJob(timer)
        self.application = MockApplication()  # Added to prevent application attribute errors!


# ==========================================
# CORE TIMER SERVICES
# ==========================================
def add_to_queue(client: TelegramClient, scheduler: AsyncIOScheduler, timer: Timer.Timer):
    """
    Add a job to the APScheduler instance
    :param client: The Telethon client
    :param scheduler: The AsyncIOScheduler instance
    :param timer: The timer configuration enum
    :return: The scheduled job
    """
    cron_expression_len = len(timer.cron_expression.split())

    if cron_expression_len not in [1, 5]:
        raise ValueError(
            f"Invalid cron expression for timer {timer.name}: {timer.cron_expression}"
        )

    if cron_expression_len == 1:  # Every X seconds
        job = scheduler.add_job(
            run,
            trigger=IntervalTrigger(seconds=int(timer.cron_expression)),
            id=timer.name,
            name=timer.name,
            args=[client, timer],
            replace_existing=True
        )
    else:  # Standard Crontab format
        job = scheduler.add_job(
            run,
            trigger=CronTrigger.from_crontab(timer.cron_expression),
            id=timer.name,
            name=timer.name,
            args=[client, timer],
            replace_existing=True
        )

    logging.info(f'Next run of "{timer.name}" is scheduled for {job.next_run_time}')
    return job


async def set_timers(client: TelegramClient, scheduler: AsyncIOScheduler) -> None:
    """
    Set the timers
    :param client: The Telethon client
    :param scheduler: The AsyncIOScheduler instance
    :return: None
    """
    for timer in Timer.TIMERS:
        if not timer.is_enabled:
            logging.info(f"Timer {timer.name} is disabled")
            continue

        add_to_queue(client, scheduler, timer)
        
        if timer.should_run_on_startup:
            logging.info(f"Timer {timer.name} is running on startup...")
            # Fire it in the background immediately so it doesn't block startup
            asyncio.create_task(run(client, timer))


async def run(client: TelegramClient, timer: Timer.Timer) -> None:
    """
    Run the timers
    :param client: The Telethon client
    :param timer: The specific Timer enum to run
    :return: None
    """
    # Create
