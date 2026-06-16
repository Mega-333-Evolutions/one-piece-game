import base64
import traceback
import logging
from datetime import datetime
from typing import Tuple, Callable, Any

from peewee import MySQLDatabase, DoesNotExist
from telethon import events
from telethon.errors import RPCError, MessageNotModifiedError, QueryIdInvalidError

import constants as c
from resources import phrases as phrases, Environment as Env
from src.model.Game import Game
from src.model.GroupChat import GroupChat
from src.model.User import User
from src.model.enums.ContextDataKey import ContextDataKey
from src.model.enums.Emoji import Emoji
from src.model.enums.GameStatus import GameStatus
from src.model.enums.Notification import GameTurnNotification, GameOutcomeNotification
from src.model.enums.ReservedKeyboardKeys import ReservedKeyboardKeys
from src.model.enums.SavedMedia import SavedMedia
from src.model.enums.SavedMediaType import SavedMediaType
from src.model.enums.Screen import Screen
from src.model.enums.devil_fruit.DevilFruitAbilityType import DevilFruitAbilityType
from src.model.enums.income_tax.IncomeTaxEventType import IncomeTaxEventType
from src.model.error.CommonChatError import CommonChatException
from src.model.game.GameBoard import GameBoard
from src.model.game.GameOutcome import GameOutcome
from src.model.game.GameTurn import GameTurn
from src.model.game.GameType import GameType
from src.model.game.punkrecords.PunkRecords import PunkRecords
from src.model.game.shambles.Shambles import Shambles
from src.model.game.whoswho.WhosWho import WhosWho
from src.model.pojo.ContextDataValue import ContextDataValue
from src.model.pojo.Keyboard import Keyboard
from src.model.wiki.Character import Character
from src.model.wiki.SupabaseRest import SupabaseRest
from src.model.wiki.Terminology import Terminology
from src.service.bounty_service import add_or_remove_bounty, validate_amount
from src.service.date_service import (
    convert_seconds_to_duration,
    get_remaining_duration,
    get_elapsed_duration,
    get_datetime_in_future_seconds,
)
from src.service.devil_fruit_service import get_ability_adjusted_datetime
from src.service.message_service import (
    mention_markdown_user,
    delete_message,
    full_media_send,
    get_message_url,
    full_message_send,
    full_message_or_media_send_or_edit,
    get_deeplink,
    escape_valid_markdown_chars,
)
from src.service.notification_service import send_notification
from src.utils.phrase_utils import get_outcome_text
from src.utils.string_utils import get_belly_formatted

import src.model.enums.Command as Command
from resources.Database import Database
from src.chat.group.group_chat_manager import manage as manage_group_chat
from src.chat.inline_query.inline_query_manager import manage as manage_inline_query
from src.chat.private.private_chat_manager import manage as manage_private_chat
from src.chat.tgrest.tgrest_chat_manager import manage as manage_tgrest_chat
from src.model.Group import Group
from src.model.GroupUser import GroupUser
from src.model.enums.ContextDataKey import ContextDataType
from src.model.enums.Feature import Feature
from src.model.enums.MessageSource import MessageSource
from src.model.error.ChatWarning import ChatWarning
from src.model.error.CustomException import (
    CommandValidationException,
    NavigationLimitReachedException,
    AnonymousAdminException,
)
from src.model.error.GroupChatError import GroupChatException
from src.model.error.PrivateChatError import PrivateChatException
from src.service.group_service import feature_is_enabled, get_group_or_topic_text, is_main_group
from src.service.user_service import user_is_boss, user_is_muted
from src.utils.context_utils import (
    get_context_data,
    set_context_data,
    get_user_context_data,
    set_user_context_data,
    remove_user_context_data,
)


def init() -> MySQLDatabase:
    """
    Initializes the database connection
    :return: Database connection
    :rtype: MySQLDatabase
    """
    db_obj = Database()
    db = db_obj.get_db()
    return db


def end(db: MySQLDatabase) -> None:
    """
    Ends the database connection
    :param db: Database connection
    :return: None
    """
    db.close()


async def manage_regular(event: events.NewMessage.Event, context: Any) -> None:
    """
    Manage a regular message
    """
    context.application.create_task(manage(event, context, False))


async def manage_callback(event: events.CallbackQuery.Event, context: Any) -> None:
    """
    Manage a callback message
    """
    context.application.create_task(manage(event, context, True))


async def manage(event: Any, context: Any, is_callback: bool) -> None:
    """
    Manage a message
    """
    now = datetime.now()
    current_tg_user_id = str(event.sender_id) if getattr(event, 'sender_id', None) else None

    # Sending message disguised as channel, ignore
    if current_tg_user_id == "777000":
        return

    if current_tg_user_id is not None:
        if not await check_current_requests(context):
            return
        set_user_context_data(context, ContextDataKey.LAST_REQUEST, now)

    db = init()
    try:
        await manage_after_db(event, context, is_callback)
    except AnonymousAdminException:  # Wasn't able to infer the user
        pass
    except Exception as e:
        logging.error(event)
        logging.error(e, exc_info=True)
        logging.error(traceback.format_stack())
    finally:
        end(db)

    if is_callback:
        try:
            await event.answer()
        except RPCError:
            pass

    if current_tg_user_id is not None:
        remove_current_request(context, now)


async def manage_after_db(
    event: Any, context: Any, is_callback: bool
) -> None:
    """
    Manage a regular message after the database is initialized
    """
    # Telethon native Message Source mapping
    if getattr(event, 'is_private', False):
        message_source = MessageSource.PRIVATE
    elif getattr(event, 'is_group', False) or getattr(event, 'is_channel', False):
        message_source = MessageSource.GROUP
    else:
        message_source = MessageSource.ND

    user = User()
    sender = await event.get_sender()
    chat = await event.get_chat()
    
    if sender is not None:
        # Check for anonymous admin or channel broadcast
        if getattr(sender, 'broadcast', False) or getattr(sender, 'megagroup', False):
            return
            
        tg_user_id = str(event.sender_id)
        user: User = await get_user(tg_user_id, sender, should_save=False)

        # Check if the user is authorized
        if (
            Env.LIMIT_TO_AUTHORIZED_USERS.get_bool()
            and user.tg_user_id not in Env.AUTHORIZED_USERS.get_list()
        ):
            return

        # Check if user in authorized groups
        if Env.LIMIT_TO_AUTHORIZED_GROUPS.get_bool():
            group_ids = Env.AUTHORIZED_GROUPS.get_list()

            # Group not authorized
            if (
                message_source is MessageSource.GROUP
                and str(event.chat_id) not in group_ids
            ):
                logging.error(f"Unauthorized group {event.chat_id}: Leaving chat")
                await context.client.delete_dialog(event.chat_id)
                return

            # User not a member of an authorized group
            if message_source is MessageSource.PRIVATE and not await user.in_authorized_groups(
                context
            ):
                return

        user.private_screen_previous_step = user.private_screen_step
        user.save()

    # Leave chat if not recognized
    if message_source is MessageSource.ND:
        if str(event.chat_id) != Env.UPDATES_CHAT_ID.get():
            logging.error(f"Unknown message source for {event.chat_id}: Leaving chat")
            await context.client.delete_dialog(event.chat_id)
        return

    # Group Setup
    group_chat = None
    if message_source is MessageSource.GROUP:
        group: Group = await add_or_update_group(
            event, (user if event.sender_id else None)
        )
        group_chat: GroupChat = await add_or_update_group_chat(event, group)
        await add_text_message_bounty(event, context, user, group_chat, is_callback)

    command: Command.Command = Command.ND
    keyboard = None
    
    # Safe text extraction
    text = getattr(event, 'text', '') or getattr(event, 'data', b'').decode('utf-8', 'ignore')
    is_command_msg = text.startswith('/')
    
    # Check for /start command in groups before command parsing
    if (
        is_command_msg
        and message_source is MessageSource.GROUP
        and text.startswith("/start")
    ):
        bot_username = Env.BOT_USERNAME.get()
        start_url = f"https://t.me/{bot_username}?start=start"
        inline_keyboard = [
            [
                Keyboard(
                    phrases.COMMAND_START_IN_GROUP_BUTTON,
                    url=start_url,
                )
            ]
        ]
        await full_message_send(
            context,
            phrases.COMMAND_START_IN_GROUP_ERROR,
            event=event,
            keyboard=inline_keyboard,
        )
        return
    
    try:
        try:
            if is_command_msg:
                if "/start " in text:  # Start with parameter
                    start_parameter = text.replace("/start ", "")
                    try:
                        parameter_decoded = base64.b64decode(start_parameter).decode()
                        keyboard = Keyboard.get_from_callback_query_or_info(
                            context,
                            message_source,
                            info_str=str(parameter_decoded),
                            from_deeplink=True,
                        )
                        command = Command.get_by_screen(keyboard.screen)
                        command_name = command.name
                    except (UnicodeDecodeError, ValueError):
                        command_name = start_parameter
                else:
                    command_name = (text.split(" ")[0])[1:].lower()
                    command_name = command_name.replace("@" + Env.BOT_USERNAME.get(), "")

                if keyboard is None:
                    if command_name.strip() != "":
                        command = Command.get_by_name(command_name, message_source)

                try:
                    command.parameters = text.split(" ")[1:]
                except IndexError:
                    pass

        except (AttributeError, ValueError):
            if is_callback:
                keyboard = Keyboard.get_from_callback_query_or_info(
                    context, message_source, event
                )

                if not keyboard.info:
                    return

                if keyboard.screen is not None:
                    try:
                        command = Command.get_by_screen(keyboard.screen)
                    except ValueError:
                        command = Command.Command(None, keyboard.screen)

        target_user: User | None = None
        if keyboard is None:
            try:
                if getattr(event, 'is_reply', False):
                    reply_msg = await event.get_reply_message()
                    if reply_msg and reply_msg.sender_id:
                        reply_sender = await reply_msg.get_sender()
                        target_user = await get_user(
                            str(reply_msg.sender_id), reply_sender
                        )
            except AttributeError:
                pass
            except AnonymousAdminException:
                pass

        # Start command, reset private screen
        if command is Command.PVT_START:
            user.private_screen_list = None
            user.reset_private_screen()

        # Check for spam only if a valid command or private chat
        if command != Command.ND or message_source is MessageSource.PRIVATE:
            if await is_spam(event, context, message_source, command, user):
                logging.warning(
                    f"Spam detected for chat {event.chat_id}: Ignoring message"
                )
                return

        if command != Command.ND or is_callback:
            if not await validate(
                event,
                context,
                command,
                user,
                keyboard,
                target_user,
                is_callback,
                message_source,
                group_chat,
            ):
                return

        if command != Command.ND:
            command.message_source = message_source

        match message_source:
            case MessageSource.PRIVATE:
                await manage_private_chat(event, context, command, user, keyboard)
            case MessageSource.GROUP:
                await manage_group_chat(
                    event, context, command, user, keyboard, target_user, is_callback, group_chat
                )
            case MessageSource.TG_REST:
                await manage_tgrest_chat(event, context)
            case MessageSource.INLINE_QUERY:
                await manage_inline_query(event, context)
            case _:
                raise ValueError("Invalid message source")
    except DoesNotExist as dne:
        await full_message_or_media_send_or_edit(context, phrases.ITEM_NOT_FOUND, event=event)
        logging.warning(event)
        logging.exception(dne)
    except (PrivateChatException, GroupChatException, CommonChatException) as ce:
        user.should_update_model = False
        previous_screens = (
            user.get_private_screen_list()[:-1]
            if message_source is MessageSource.PRIVATE
            else None
        )
        try:
            await full_message_send(
                context,
                str(ce),
                event=event,
                previous_screens=previous_screens,
                from_exception=True,
            )
        except Exception: 
            try:
                await full_message_or_media_send_or_edit(
                    context,
                    str(ce),
                    event=event,
                    previous_screens=previous_screens,
                    from_exception=True,
                )
            except Exception: 
                await full_message_or_media_send_or_edit(
                    context,
                    escape_valid_markdown_chars(str(ce)),
                    event=event,
                    previous_screens=previous_screens,
                    from_exception=True,
                )
    except MessageNotModifiedError as bre:
        logging.error(f"Updated message same as previous in chat {event.chat_id}")
    except QueryIdInvalidError as bre:
        logging.warning("Ignored stale callback query.")
    except NavigationLimitReachedException:
        await full_message_send(
            context,
            phrases.NAVIGATION_LIMIT_REACHED,
            event=event,
            answer_callback=True,
            show_alert=True,
        )
    except ChatWarning as cw:
        await full_message_or_media_send_or_edit(
            context,
            escape_valid_markdown_chars(str(cw)),
            event=event,
            inbound_keyboard=keyboard,
            from_exception=True,
            show_alert=True,
        )
    except Exception as e:
        logging.error(event)
        logging.error(e, exc_info=True)
        logging.error(traceback.format_stack())

    if user.should_update_model and user.tg_user_id is not None:
        user.save()


async def validate(
    event: Any,
    context: Any,
    command: Command.Command,
    user: User,
    inbound_keyboard: Keyboard,
    target_user: User,
    is_callback: bool,
    message_source: MessageSource,
    group_chat: GroupChat,
) -> bool:
    """
    Validate the command natively for Telethon
    """

    # Validate keyboard interaction
    if is_callback and ReservedKeyboardKeys.AUTHORIZED_USERS in inbound_keyboard.info:
        if (
            int(user.id) not in inbound_keyboard.info[ReservedKeyboardKeys.AUTHORIZED_USERS]
        ):  # Unauthorized
            await full_message_send(
                context,
                phrases.KEYBOARD_USE_UNAUTHORIZED,
                event,
                answer_callback=True,
                show_alert=True,
            )
            return False

        # Delete request, best effort
        if ReservedKeyboardKeys.DELETE in inbound_keyboard.info:
            await event.delete()
            return False

    # Always accept delete request in private chat
    if (
        is_callback
        and message_source is MessageSource.PRIVATE
        and ReservedKeyboardKeys.DELETE in inbound_keyboard.info
    ):
        await event.delete()
        return False

    _message_is_reply = getattr(event, 'is_reply', False)
    _is_main_group = group_chat is not None and is_main_group(group_chat)
    is_restricted_feature_error = False

    inline_keyboard: list[list[Keyboard]] = []
    try:
        # Is active
        if not command.active:
            if command.replaced_by is not None:
                raise CommandValidationException(
                    phrases.COMMAND_NOT_ACTIVE_WITH_REPLACEMENT_ERROR.format(
                        command.get_replaced_by_formatted()
                    )
                )
            raise CommandValidationException(phrases.COMMAND_NOT_ACTIVE_ERROR)

        # Feature not allowed in group_chat
        if command.feature is not None and message_source is MessageSource.GROUP:
            if not feature_is_enabled(group_chat, command.feature):
                raise CommandValidationException(
                    phrases.COMMAND_FEATURE_DISABLED_ERROR.format(
                        get_group_or_topic_text(group_chat)
                    )
                )

        # Cannot be used while arrested
        if not command.allow_while_arrested:
            if (
                user.is_arrested()
                and not (command.allow_while_arrested_temporary and user.is_arrested_temporary())
                and not (command.allow_reply_to_arrested and is_callback)
            ):
                raise CommandValidationException(phrases.COMMAND_WHILE_ARRESTED_ERROR)

        # Required location
        if command.required_location is not None:
            if user.location_level < command.required_location.level:
                if command.required_location.is_first_new_world():
                    raise CommandValidationException(phrases.COMMAND_FOR_NEW_WORLD_USERS_ERROR)

                text = phrases.COMMAND_FOR_USERS_AFTER_LOCATION_ERROR.format(
                    escape_valid_markdown_chars(command.required_location.name),
                    escape_valid_markdown_chars(user.get_location_name()),
                )
                if user.bounty < command.required_location.required_bounty:
                    text += phrases.COMMAND_FOR_USERS_AFTER_LOCATION_BOUNTY_ERROR.format(
                        get_belly_formatted(user.bounty),
                        get_belly_formatted(command.required_location.required_bounty),
                        get_belly_formatted(
                            command.required_location.required_bounty - user.bounty
                        ),
                    )

                if not user.is_crew_member() and user.can_join_crew:
                    text += phrases.COMMAND_FOR_USERS_AFTER_LOCATION_ERROR_JOIN_CREW
                    inline_keyboard.append(
                        [
                            Keyboard(
                                phrases.KEY_JOIN_A_CREW,
                                screen=Screen.PVT_CREW_SEARCH,
                                is_deeplink=True,
                            )
                        ]
                    )

                raise CommandValidationException(text)

        # Can only be used in reply to a message
        if command.only_in_reply:
            try:
                if not _message_is_reply:
                    raise CommandValidationException(phrases.COMMAND_NOT_IN_REPLY_ERROR)
            except AttributeError:
                pass

        # Cannot be in reply to yourself
        if not command.allow_self_reply:
            try:
                if _message_is_reply:
                    reply_msg = await event.get_reply_message()
                    if reply_msg and reply_msg.sender_id == event.sender_id:
                        raise CommandValidationException(phrases.COMMAND_IN_REPLY_TO_ERROR)
            except AttributeError:
                pass

        # Cannot be in reply to a Bot
        if not command.allow_reply_to_bot and not is_callback:
            try:
                if _message_is_reply:
                    reply_msg = await event.get_reply_message()
                    if reply_msg:
                        reply_sender = await reply_msg.get_sender()
                        if getattr(reply_sender, 'bot', False) and target_user is None:
                            raise CommandValidationException(phrases.COMMAND_IN_REPLY_TO_BOT_ERROR)
            except AttributeError:
                pass

        # Cannot be in reply to an arrested user
        if not command.allow_reply_to_arrested:
            try:
                if target_user and target_user.is_arrested():
                    raise CommandValidationException(phrases.COMMAND_IN_REPLY_TO_ARRESTED_ERROR)
            except AttributeError:
                pass

        # Can only be used by a Crew Captain
        if command.only_by_crew_captain:
            if not user.is_crew_captain():
                raise CommandValidationException(phrases.COMMAND_ONLY_BY_CREW_CAPTAIN_ERROR)

        # Can only be used by a Crew Captain or First Mate
        if command.only_by_crew_captain_or_first_mate:
            if not (is_callback and not command.only_by_crew_captain_or_first_mate_keyboard):
                if not user.is_crew_captain_or_first_mate():
                    raise CommandValidationException(
                        phrases.COMMAND_ONLY_BY_CREW_CAPTAIN_OR_FIRST_MATE_ERROR
                    )

        # Can only be used by a boss
        if command.only_by_boss:
            if not user_is_boss(user):
                raise CommandValidationException(phrases.COMMAND_ONLY_BY_BOSS_ERROR)

        # Can only be used by a chat admin (Will need to update user.is_chat_admin later)
        if command.only_by_chat_admin:
            if not await user.is_chat_admin(event):
                raise CommandValidationException(phrases.COMMAND_ONLY_BY_CHAT_ADMIN_ERROR)

        if not is_callback:
            if command.only_in_reply_to_crew_member:
                if target_user and not target_user.is_crew_member():
                    raise CommandValidationException(
                        phrases.COMMAND_NOT_IN_REPLY_TO_CREW_MEMBER_ERROR
                    )

        if command.feature is not None and message_source is MessageSource.GROUP:
            feature: Feature = command.feature
            if feature.is_restricted() and not _is_main_group:
                is_restricted_feature_error = True
                raise CommandValidationException("")

        if inbound_keyboard is not None and inbound_keyboard.from_deeplink:
            if not command.allow_deeplink:
                raise CommandValidationException(phrases.COMMAND_NOT_ALLOWED_FROM_DEEPLINK_ERROR)

    except CommandValidationException as cve:
        if is_restricted_feature_error:  
            return False
        if not command.answer_callback and user_is_muted(user, group_chat):
            await event.delete()
        else:
            if (command.answer_callback and is_callback) or command.send_message_if_error:
                await full_message_or_media_send_or_edit(
                    context,
                    str(cve),
                    event=event,
                    add_delete_button=(inbound_keyboard is None),
                    answer_callback=command.answer_callback,
                    show_alert=command.show_alert,
                    inbound_keyboard=inbound_keyboard,
                    keyboard=inline_keyboard,
                )
        return False

    return True


async def get_user(
    tg_user_id: str, effective_user: Any, should_save: bool = True
) -> User:
    """
    Create or get the user natively for Telethon
    """
    user = User.get_or_none(User.tg_user_id == tg_user_id)
    if user is None:
        user = User()
        user.tg_user_id = tg_user_id

    # Update name only if not anonymous
    if effective_user and str(effective_user.id) == tg_user_id:
        user.tg_first_name = getattr(effective_user, 'first_name', '')
        user.tg_last_name = getattr(effective_user, 'last_name', '')
        user.tg_username = getattr(effective_user, 'username', '')

    user.last_message_date = datetime.now()
    user.is_active = True

    if should_save:
        user.save()

    return user


async def add_or_update_group(event, user: User) -> Group:
    """
    Adds or updates a group_chat for Telethon
    """
    group = Group.get_or_none(Group.tg_group_id == event.chat_id)

    if group is None:
        group = Group()
        group.tg_group_id = event.chat_id

    chat = await event.get_chat()
    group.tg_group_name = getattr(chat, 'title', '')
    group.tg_group_username = getattr(chat, 'username', '')
    group.is_forum = getattr(chat, 'forum', False)
    group.last_message_date = datetime.now()
    group.is_active = True
    group.save()

    if user is not None:
        group_user = GroupUser.get_or_none((GroupUser.group == group) & (GroupUser.user == user))
        if group_user is None:
            group_user = GroupUser()
            group_user.group = group
            group_user.user = user

        group_user.last_message_date = datetime.now()
        group_user.is_active = True
        group_user.is_admin = await user.is_chat_admin(event)
        group_user.save()

    return group


async def add_or_update_group_chat(event, group: Group) -> GroupChat:
    """
    Adds or updates a group_chat/topic for Telethon
    """
    chat = await event.get_chat()
    tg_topic_id = None
    
    if getattr(chat, 'forum', False) and getattr(event, 'is_reply', False):
        reply_msg = await event.get_reply_message()
        if reply_msg and reply_msg.reply_to:
            tg_topic_id = reply_msg.reply_to.reply_to_top_id or reply_msg.reply_to.reply_to_msg_id

    group_chat = GroupChat.get_or_none(
        (GroupChat.group == group) & (GroupChat.tg_topic_id == tg_topic_id)
    )

    if group_chat is None:
        group_chat = GroupChat()
        group_chat.group = group
        group_chat.tg_topic_id = tg_topic_id

    group_chat.last_message_date = datetime.now()
    group_chat.is_active = True
    group_chat.save()

    return group_chat


async def add_text_message_bounty(
    event: Any,
    context: Any,
    user: User,
    group_chat: GroupChat,
    is_callback: bool,
) -> None:
    """
    Award taxable bounty for regular text messages natively for Telethon
    """
    if is_callback or user is None or user.tg_user_id is None:
        return

    text = getattr(event, 'text', '')
    if not text:
        return

    if text.startswith(("/", ".", "!")):
        return

    if getattr(event, 'forward', None) is not None:
        return

    if len(text.split()) <= 3:
        return

    sender = await event.get_sender()
    if getattr(sender, 'bot', False):
        return

    if not is_main_group(group_chat) or user.is_arrested():
        return

    await add_or_remove_bounty(
        user,
        Env.MAIN_GROUP_TEXT_MESSAGE_BOUNTY_REWARD.get_int(),
        context=context,
        event=event,
    )


async def is_spam(
    event: Any,
    context: Any,
    message_source: MessageSource,
    command: Command,
    user: User,
) -> bool:
    """
    Check if the message is spam
    """
    if message_source is MessageSource.PRIVATE:
        context_data_type = ContextDataType.USER
        inner_key = None
    elif message_source is MessageSource.GROUP:
        context_data_type = ContextDataType.BOT
        inner_key = str(event.chat_id)
    else:
        return False  

    if user is not None and user.get_current_private_screen() is Screen.PVT_GAME_GUESS_INPUT:
        return False

    if command is not None and command.screen is Screen.GRP_RUSSIAN_ROULETTE_GAME:
        return False

    try:
        past_messages_date_list: list[datetime] = get_context_data(
            context, context_data_type, ContextDataKey.PAST_MESSAGES_DATE, inner_key=inner_key
        )
    except CommonChatException:
        past_messages_date_list = []

    now = datetime.now()
    past_messages_date_list = [
        x
        for x in past_messages_date_list
        if now
        < get_datetime_in_future_seconds(
            Env.ANTI_SPAM_TIME_INTERVAL_SECONDS.get_int(), start_time=x
        )
    ]

    spam_limit = (
        Env.ANTI_SPAM_PRIVATE_CHAT_MESSAGE_LIMIT.get_int()
        if message_source is MessageSource.PRIVATE
        else Env.ANTI_SPAM_GROUP_CHAT_MESSAGE_LIMIT.get_int()
    )

    if len(past_messages_date_list) >= spam_limit:
        if len(past_messages_date_list) == spam_limit and message_source is MessageSource.PRIVATE:
            past_messages_date_list.append(now)
            set_context_data(
                context,
                context_data_type,
                ContextDataKey.PAST_MESSAGES_DATE,
                past_messages_date_list,
                inner_key=inner_key,
            )
            await full_message_send(
                context,
                phrases.ANTI_SPAM_WARNING,
                event=event,
                quote_if_group=False,
                new_message=True,
            )
        return True

    past_messages_date_list.append(now)
    set_context_data(
        context,
        context_data_type,
        ContextDataKey.PAST_MESSAGES_DATE,
        past_messages_date_list,
        inner_key=inner_key,
    )

    return False


async def check_current_requests(context: Any) -> bool:
    """
    Make sure that there are no request currently being processed for a user
    """
    try:
        if (
            datetime.now()
            - get_user_context_data(
                context, ContextDataKey.LAST_REQUEST, tolerate_key_exception=False
            )
        ).total_seconds() > 10:
            return True
        return False
    except KeyError:
        return True


def remove_current_request(context: Any, inserted_time: datetime) -> None:
    """
    Remove a request from the dictionary
    """
    try:
        value = get_user_context_data(
            context, ContextDataKey.LAST_REQUEST, tolerate_key_exception=False
        )
        if value == inserted_time:
            remove_user_context_data(context, ContextDataKey.LAST_REQUEST)
    except KeyError:
        pass
