from peewee import fn
from telegram import Update
from telegram.ext import ContextTypes

import src.model.enums.Command as Command
from src.model.User import User
from src.model.enums.CommandName import CommandName
from src.model.enums.MessageSource import MessageSource
from src.service.bounty_service import get_amount_from_string, validate_amount
from src.service.message_service import (
    full_message_or_media_send_or_edit,
    get_message_source,
    mention_markdown_user,
)
from src.utils.string_utils import get_belly_formatted


async def manage(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    command: Command.Command,
    user: User,
    target_user: User = None,
) -> None:
    """
    Manage owner-only bounty adjustment commands.
    :param update: The update
    :param context: The context
    :param command: The command
    :param user: The owner user
    :param target_user: Target user from replied message in groups
    :return: None
    """

    is_group = get_message_source(update) is MessageSource.GROUP
    add_delete_button = is_group

    if is_group:
        if target_user is None:
            await full_message_or_media_send_or_edit(
                context,
                "This command can only be used in reply to someone's message.",
                update=update,
                add_delete_button=True,
            )
            return

        if len(command.parameters) == 0:
            await full_message_or_media_send_or_edit(
                context,
                get_missing_group_amount_text(command),
                update=update,
                add_delete_button=True,
            )
            return

        amount_text = command.parameters[0]
    else:
        if len(command.parameters) == 0:
            await full_message_or_media_send_or_edit(
                context,
                "Please specify the bounty amount.",
                update=update,
            )
            return

        amount_text = command.parameters[0]

        if len(command.parameters) < 2:
            await full_message_or_media_send_or_edit(
                context,
                "Please specify a target user by username or Telegram user ID.",
                update=update,
            )
            return

        target_user = get_target_user(command.parameters[1])
        if target_user is None:
            await full_message_or_media_send_or_edit(
                context,
                "Please specify a target user by username or Telegram user ID.",
                update=update,
            )
            return

    if not await validate_amount(
        update,
        context,
        user,
        amount_text,
        add_delete_button=add_delete_button,
        should_validate_user_has_amount=False,
    ):
        return

    amount = get_amount_from_string(amount_text, user)
    target_user = User.get_by_id(target_user.id)

    if command.name is CommandName.ADD_BOUNTY:
        target_user.bounty += amount
        target_user.save()
        text = "Added ฿{} Berries to {}.".format(
            get_belly_formatted(amount), mention_markdown_user(target_user)
        )
    else:
        if target_user.bounty < amount:
            target_user.bounty = 0
            target_user.save()
            text = (
                "{} doesn't have ฿{} Berries, so their bounty has been set to ฿0 instead.".format(
                    mention_markdown_user(target_user), get_belly_formatted(amount)
                )
            )
        else:
            target_user.bounty -= amount
            target_user.save()
            text = "Took ฿{} Berries from {}.".format(
                get_belly_formatted(amount), mention_markdown_user(target_user)
            )

    await full_message_or_media_send_or_edit(
        context,
        text,
        update=update,
        add_delete_button=add_delete_button,
    )


def get_missing_group_amount_text(command: Command.Command) -> str:
    """
    Get missing amount text for group commands.
    :param command: The command
    :return: Missing amount text
    """

    if command.name is CommandName.ADD_BOUNTY:
        return "Please specify the bounty amount to add."

    return "Please specify the bounty amount to remove."


def get_target_user(identifier: str) -> User | None:
    """
    Resolve a user by Telegram user id or username.
    :param identifier: Telegram user id or username
    :return: User, if found
    """

    identifier = identifier.strip()
    if identifier.startswith("@"):
        identifier = identifier[1:]

    if identifier.isnumeric():
        user = User.get_or_none(User.tg_user_id == identifier)
        if user is not None:
            return user

    if identifier == "":
        return None

    return User.get_or_none(fn.LOWER(User.tg_username) == identifier.lower())
