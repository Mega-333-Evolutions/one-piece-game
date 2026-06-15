from telegram import Update
from telegram.ext import ContextTypes

from src.model.User import User
from src.model.enums.ScoutType import ScoutType
from src.model.pojo.Keyboard import Keyboard
from src.service.fight_plunder_service import (
    private_manage,
    fight_validate,
)


async def manage(
    event: Update, context: ContextTypes.DEFAULT_TYPE, user: User, inbound_keyboard: Keyboard
) -> None:
    """
    Manage the fight request
    :param event: The event object
    :param context: The context object
    :param user: The user object
    :param inbound_keyboard: The keyboard object
    :return: None
    """

    # Validate the request
    if not await fight_validate(event, context, user, False, inbound_keyboard):
        return

    return await private_manage(event, context, user, inbound_keyboard, ScoutType.FIGHT)
