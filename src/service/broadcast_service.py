import asyncio
import logging

from telegram import Message
from telegram.error import Forbidden, TelegramError, RetryAfter
from telegram.ext import ContextTypes

from resources import phrases
from src.model.Group import Group
from src.model.GroupChat import GroupChat
from src.model.User import User
from src.service.group_service import save_group_chat_error
from src.service.message_service import full_message_send


async def broadcast_to_groups_dispatch(
    context: ContextTypes.DEFAULT_TYPE,
    owner: User,
    from_chat_id: int | str,
    message_id: int,
    should_pin: bool,
    progress_message_id: int,
) -> None:
    """
    Dispatches the group broadcast (gcast) to a background task
    :param context: The context
    :param owner: The owner, used to send the final report
    :param from_chat_id: The chat id of the message to broadcast
    :param message_id: The id of the message to broadcast
    :param should_pin: True if the broadcast message should be pinned
    :param progress_message_id: The id of the progress message to edit with the final report
    :return: None
    """

    context.application.create_task(
        broadcast_to_groups(
            context, owner, from_chat_id, message_id, should_pin, progress_message_id
        )
    )


async def broadcast_to_groups(
    context: ContextTypes.DEFAULT_TYPE,
    owner: User,
    from_chat_id: int | str,
    message_id: int,
    should_pin: bool,
    progress_message_id: int,
) -> None:
    """
    Broadcasts a message to every group/topic the Bot is an active member of
    :param context: The context
    :param owner: The owner, used to send the final report
    :param from_chat_id: The chat id of the message to broadcast
    :param message_id: The id of the message to broadcast
    :param should_pin: True if the broadcast message should be pinned
    :param progress_message_id: The id of the progress message to edit with the final report
    :return: None
    """

    total = 0
    successful = 0
    failed = 0
    pinned = 0
    pin_failed = 0

    groups: list[Group] = Group.select()
    for group in groups.iterator():
        group_chats: list[GroupChat] = list(group.group_chats)

        for group_chat in group_chats:
            total += 1

            copied_message = await _copy_message(
                context, group.tg_group_id, from_chat_id, message_id, group_chat.tg_topic_id
            )
            if copied_message is None:
                failed += 1
                save_group_chat_error(group_chat, "Failed to broadcast message (gcast)")
                continue

            successful += 1

            if not should_pin:
                continue

            if await _pin_message(context, group.tg_group_id, copied_message.message_id):
                pinned += 1
            else:
                pin_failed += 1
                save_group_chat_error(group_chat, "Failed to pin broadcast message (gcast)")

    await _send_final_report(
        context,
        owner,
        progress_message_id,
        phrases.BROADCAST_GROUPS_COMPLETED,
        phrases.BROADCAST_GROUPS_STATISTICS.format(total, successful, failed),
        should_pin,
        pinned,
        pin_failed,
    )


async def broadcast_to_players_dispatch(
    context: ContextTypes.DEFAULT_TYPE,
    owner: User,
    from_chat_id: int | str,
    message_id: int,
    should_pin: bool,
    progress_message_id: int,
) -> None:
    """
    Dispatches the player broadcast (pcast) to a background task
    :param context: The context
    :param owner: The owner, used to send the final report
    :param from_chat_id: The chat id of the message to broadcast
    :param message_id: The id of the message to broadcast
    :param should_pin: True if the broadcast message should be pinned
    :param progress_message_id: The id of the progress message to edit with the final report
    :return: None
    """

    context.application.create_task(
        broadcast_to_players(
            context, owner, from_chat_id, message_id, should_pin, progress_message_id
        )
    )


async def broadcast_to_players(
    context: ContextTypes.DEFAULT_TYPE,
    owner: User,
    from_chat_id: int | str,
    message_id: int,
    should_pin: bool,
    progress_message_id: int,
) -> None:
    """
    Broadcasts a message to every registered player's private chat
    :param context: The context
    :param owner: The owner, used to send the final report
    :param from_chat_id: The chat id of the message to broadcast
    :param message_id: The id of the message to broadcast
    :param should_pin: True if the broadcast message should be pinned
    :param progress_message_id: The id of the progress message to edit with the final report
    :return: None
    """

    total = 0
    successful = 0
    failed = 0
    pinned = 0
    pin_failed = 0

    players: list[User] = User.select()
    for player in players.iterator():
        total += 1

        copied_message = await _copy_message(
            context, player.tg_user_id, from_chat_id, message_id, None
        )
        if copied_message is None:
            failed += 1
            continue

        successful += 1

        if not should_pin:
            continue

        if await _pin_message(context, player.tg_user_id, copied_message.message_id):
            pinned += 1
        else:
            pin_failed += 1

    await _send_final_report(
        context,
        owner,
        progress_message_id,
        phrases.BROADCAST_PLAYERS_COMPLETED,
        phrases.BROADCAST_PLAYERS_STATISTICS.format(total, successful, failed),
        should_pin,
        pinned,
        pin_failed,
    )


async def _copy_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int | str,
    from_chat_id: int | str,
    message_id: int,
    message_thread_id: int | None,
) -> Message | None:
    """
    Copies a message to a chat, using Telegram's native copyMessage so the broadcast doesn't
    show a "Forwarded from" tag and preserves formatting/media/keyboards as-is.
    Retries once if rate limited, since a failure to broadcast to one destination must not stop
    the rest of the broadcast.
    :param context: The context
    :param chat_id: The destination chat id
    :param from_chat_id: The chat id of the message to copy
    :param message_id: The id of the message to copy
    :param message_thread_id: The forum topic id, if any
    :return: The copied message, or None if it could not be sent
    """

    for attempt in range(2):
        try:
            return await context.bot.copy_message(
                chat_id=chat_id,
                from_chat_id=from_chat_id,
                message_id=message_id,
                message_thread_id=message_thread_id,
                disable_notification=True,
            )
        except RetryAfter as ra:
            if attempt == 0:
                logging.warning(f"Rate limited while broadcasting to {chat_id}, retrying")
                await asyncio.sleep(ra.retry_after)
                continue
            logging.warning(f"Failed to broadcast to {chat_id} after retrying: {ra}")
            return None
        except Forbidden:
            # Bot was kicked from the group, or the player blocked/never started the Bot
            return None
        except TelegramError as te:
            logging.warning(f"Failed to broadcast to {chat_id}: {te}")
            return None

    return None


async def _pin_message(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int | str, message_id: int
) -> bool:
    """
    Pins a message in a chat. Retries once if rate limited.
    A failure to pin must not interrupt the broadcast.
    :param context: The context
    :param chat_id: The chat id
    :param message_id: The id of the message to pin
    :return: True if the message was pinned
    """

    for attempt in range(2):
        try:
            await context.bot.pin_chat_message(
                chat_id=chat_id, message_id=message_id, disable_notification=True
            )
            return True
        except RetryAfter as ra:
            if attempt == 0:
                await asyncio.sleep(ra.retry_after)
                continue
            logging.warning(f"Failed to pin broadcast message in {chat_id} after retrying: {ra}")
            return False
        except TelegramError as te:
            logging.warning(f"Failed to pin broadcast message in {chat_id}: {te}")
            return False

    return False


async def _send_final_report(
    context: ContextTypes.DEFAULT_TYPE,
    owner: User,
    progress_message_id: int,
    completed_phrase: str,
    statistics_text: str,
    should_pin: bool,
    pinned: int,
    pin_failed: int,
) -> None:
    """
    Edits the progress message with the final broadcast report
    :param context: The context
    :param owner: The owner
    :param progress_message_id: The id of the progress message to edit
    :param completed_phrase: The completed phrase template, expecting statistics and pin stats
    :param statistics_text: The broadcast statistics text
    :param should_pin: True if pin statistics should be included
    :param pinned: The number of successfully pinned messages
    :param pin_failed: The number of messages that failed to pin
    :return: None
    """

    pin_statistics_text = (
        phrases.BROADCAST_PIN_STATISTICS.format(pinned, pin_failed) if should_pin else ""
    )

    await full_message_send(
        context,
        completed_phrase.format(statistics_text, pin_statistics_text),
        chat_id=owner.tg_user_id,
        edit_message_id=progress_message_id,
    )
