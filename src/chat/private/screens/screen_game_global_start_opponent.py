import datetime

from telegram import Update
from telegram.ext import ContextTypes

from resources import phrases
from src.chat.common.screens.screen_game_manage import dispatch_game
from src.model.Game import Game
from src.model.User import User
from src.model.enums.GameStatus import GameStatus
from src.model.enums.ReservedKeyboardKeys import ReservedKeyboardKeys
from src.model.enums.Screen import Screen
from src.model.game.GameType import GameType
from src.model.pojo.Keyboard import Keyboard
from src.service.date_service import datetime_is_after, get_remaining_duration
from src.service.game_service import (
    get_game_from_keyboard,
    collect_game_wagers_and_set_in_progress,
    get_text,
)
from src.service.message_service import (
    full_message_send,
    get_yes_no_keyboard,
    full_media_send,
    full_message_or_media_send_or_edit,
)
from src.utils.string_utils import get_belly_formatted


async def manage(
    update: Update, context: ContextTypes.DEFAULT_TYPE, inbound_keyboard: Keyboard, user: User
) -> None:
    """
    Manage the game input screen
    :param update: The update
    :param context: The context
    :param inbound_keyboard: The inbound keyboard
    :param user: The user
    :return: None
    """

    # Guard clause: If there is no keyboard payload (e.g., user typed raw text), abort.
    if inbound_keyboard is None:
        return

    game: Game = get_game_from_keyboard(inbound_keyboard)

    game_status: GameStatus = GameStatus(game.status)
    game_type: GameType = GameType(game.type)

    # Challenger trying to accept this own challenge, simply redirect
    if game.is_challenger(user):
        await dispatch_game(update, context, user, inbound_keyboard, game)
        return

    # Challenge has an opponent
    if game.opponent is not None:
        # Accepted by another player
        if not game.is_player(user):
            await full_message_or_media_send_or_edit(
                context, 
                text=phrases.GAME_GLOBAL_ALREADY_ACCEPTED, 
                update=update
            )
            return

        await dispatch_game(update, context, user, inbound_keyboard, game)
        return

    if game_status is not GameStatus.IN_PROGRESS:
        await full_message_or_media_send_or_edit(
            context, 
            text=phrases.ITEM_IN_WRONG_STATUS, 
            update=update
        )
        return

    # Opponent does not have enough bounty
    if user.bounty < game.wager:
        await full_message_or_media_send_or_edit(
            context,
            text=phrases.ACTION_INSUFFICIENT_BOUNTY.format(get_belly_formatted(game.wager)),
            update=update
        )
        return

    if user.game_accept_global_cooldown_end_date and datetime_is_after(
        user.game_accept_global
