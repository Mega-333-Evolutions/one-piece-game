import re

from confusable_homoglyphs import confusables
from telegram import Update
from unidecode import unidecode
from wantedposter.wantedposter import (
    WantedPoster,
    VerticalAlignment,
    CaptureCondition,
    Effect,
    Stamp,
)

import constants as c
from src.model.Leaderboard import Leaderboard
from src.model.LeaderboardUser import LeaderboardUser
from src.model.User import User
from src.model.enums.BossType import BossType
from src.model.enums.LeaderboardRank import LeaderboardRank, get_rank_by_index
from src.service.devil_fruit_service import user_has_eaten_devil_fruit
from src.utils.download_utils import generate_temp_file_path

# Fallback name used in the extremely rare case where, after sanitization, nothing
# usable is left of a player's name (e.g. it was made up entirely of emoji/symbols)
BOUNTY_POSTER_FALLBACK_NAME = "Unknown"


# "Fancy font" generators (common for Discord/Telegram display names) often reuse characters
# from completely unrelated scripts purely because they look like Latin letters (e.g. Canadian
# Aboriginal Syllabics, Cherokee). unidecode has no way to know this -- it transliterates based
# on the character's real phonetic meaning, not its visual shape, so these end up turning into
# unrelated syllables (e.g. "ᖇ" looks like "R" but unidecode reads it as the Cree syllable
# "tlhi", "Ꮿ" looks like "W" but unidecode reads it as the Cherokee syllable "ya"). This map
# catches reported cases by mapping the visual look-alike directly to the Latin letter it's
# actually being used to represent, bypassing unidecode for these characters. Not exhaustive --
# there are many different "fancy font" styles in the wild -- but can be extended here whenever
# a new one is reported
FANCY_FONT_LOOKALIKE_CHAR_MAP = {
    "ᖇ": "R",  # Canadian Syllabics Tlhi, used to look like "R"
    "ᑢ": "C",  # Canadian Syllabics West-Cree Twa, used to look like "C"
    "ᒪ": "L",  # Canadian Syllabics Ma, used to look like "L"
    "ᘿ": "E",  # Canadian Syllabics Carrier Tla, used to look like "E"
    "Ꮋ": "H",  # Cherokee Letter Mi, used to look like "H"
    "Ꮿ": "W",  # Cherokee Letter Ya, used to look like "W"
    "Ꮇ": "M",  # Cherokee Letter Lu, used to look like "M"
}


def _get_homoglyph_latin_letter(char: str) -> str | None:
    """
    General-purpose fallback for FANCY_FONT_LOOKALIKE_CHAR_MAP, for "fancy font" look-alike
    characters that haven't been reported/added there yet. Looks up Unicode's own official
    "confusables" data (the same dataset used for anti-phishing/anti-spoofing detection, listing
    characters from any script that are visually confusable with one another) for a plain ASCII
    letter/digit that this character could be mistaken for. Only called for non-ASCII characters,
    since otherwise this would find characters that look like an existing ASCII letter and
    "correct" the letter into a different one (e.g. it considers "l" a look-alike of "1", which
    would wrongly replace a legitimate "1" with "l")
    :param char: A single non-ASCII character
    :return: A single ASCII letter/digit if a confident look-alike is found, otherwise None
    """

    matches = confusables.is_confusable(char, greedy=True)
    if not matches:
        return None

    for match in matches:
        for homoglyph in match.get("homoglyphs", []):
            candidate = homoglyph.get("c", "")
            if len(candidate) == 1 and candidate.isascii() and candidate.isalnum():
                return candidate

    return None


def get_bounty_poster_name(name: str) -> str:
    """
    Sanitizes a name for use on a wanted poster.

    Names are shown in their original script whenever possible (e.g. Arabic "سجاد" stays
    as "سجاد"). Transliteration to Latin via unidecode is only a fallback for characters
    that are not Unicode letters/digits and cannot otherwise be represented.

    unidecode doesn't just transliterate — it also *describes* characters it has no real
    letter equivalent for (e.g. the chess piece "♚" becomes "black king", an emoji becomes
    a description). Those multi-word descriptions are never appended; such characters are
    discarded instead.

    Known "fancy font" look-alike characters (see FANCY_FONT_LOOKALIKE_CHAR_MAP) are still
    mapped to their intended Latin letter directly. Any other non-alphanumeric non-ASCII
    look-alike not in that map is checked against Unicode's confusables data
    (see _get_homoglyph_latin_letter) before falling back to unidecode.
    :param name: The raw name to sanitize, as it is on Telegram
    :return: The sanitized name, safe to pass to the poster generator
    """

    if not name:
        return ""

    kept_chars: list[str] = []
    for char in name:
        if char.isspace():
            kept_chars.append(" ")
            continue

        if char in FANCY_FONT_LOOKALIKE_CHAR_MAP:
            kept_chars.append(FANCY_FONT_LOOKALIKE_CHAR_MAP[char])
            continue

        # Prefer the character as-is for any Unicode letter or digit (any script)
        if char.isalnum():
            kept_chars.append(char)
            continue

        transliterated = unidecode(char).strip()

        if not char.isascii():
            homoglyph = _get_homoglyph_latin_letter(char)
            if homoglyph is not None:
                kept_chars.append(homoglyph)
                continue

        # Fallback: keep a clean transliteration when the original cannot be shown as-is
        if transliterated and " " not in transliterated:
            kept_chars.append(transliterated)

    cleaned = re.sub(r"\s+", " ", "".join(kept_chars)).strip()

    # Strip decorative punctuation (e.g. "{}", "|", "*"), keeping letters/digits from any
    # script and common name punctuation (apostrophe, hyphen, period)
    cleaned = re.sub(r"[^\w '\-.]", "", cleaned, flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip()


def _wanted_poster_get_full_name(self, max_length: int | None) -> str:
    """
    Builds the full name for the poster without transliterating non-Latin scripts.

    The upstream wantedposter library always runs unidecode here, which turns names like
    "سجاد" into unrelated Latin text ("sjd"). This replacement keeps names as sanitized
    by get_bounty_poster_name above.
    """

    first_name = (self.first_name or "").upper().strip()
    last_name = (self.last_name or "").upper().strip()

    full_name = f"{last_name} {first_name}".strip()

    if max_length is None or len(full_name) <= max_length:
        return full_name

    full_name = first_name
    if len(full_name) <= max_length:
        return full_name

    parts = full_name.split(" ")
    result = ""
    for part in parts:
        if len(result + " " + part) > max_length:
            return full_name
        result += " " + part

    full_name = parts[0] if len(result) == 0 else result
    if len(full_name) <= max_length:
        return full_name

    return full_name[: (max_length - 2)] + "."


# The library transliterates names again in __get_full_name; patch it out so non-Latin
# names survive until they are drawn on the poster
WantedPoster._WantedPoster__get_full_name = _wanted_poster_get_full_name


async def get_bounty_poster(
    update: Update, user: User, telegram_user=None
) -> str:
    """
    Gets the bounty poster of a user
    :param update: Telegram update
    :param user: The user to get the poster of
    :param telegram_user: Optional Telegram user to fetch the portrait for
    :return: The path to the poster
    """

    from src.service.user_service import get_user_profile_photo, get_boss_type

    # The underlying poster generator renders names as "LAST FIRST".
    # Swap inputs so the poster displays "FIRST LAST".
    poster_first_name = get_bounty_poster_name((user.tg_first_name or "").strip())
    poster_last_name = get_bounty_poster_name((user.tg_last_name or "").strip())

    # If sanitization stripped everything (e.g. an emoji-only name), fall back to a
    # placeholder rather than showing a blank name on the poster
    if not poster_first_name and not poster_last_name:
        poster_first_name = BOUNTY_POSTER_FALLBACK_NAME

    wanted_poster = WantedPoster(
        portrait=await get_user_profile_photo(update, telegram_user),
        first_name=poster_last_name,
        last_name=poster_first_name,
        bounty=user.bounty,
    )

    capture_condition: CaptureCondition = CaptureCondition.DEAD_OR_ALIVE
    effects: list[Effect] = []
    stamp: Stamp | None = None

    if user_has_eaten_devil_fruit(user):
        capture_condition = CaptureCondition.ONLY_ALIVE

    boss_type: BossType = get_boss_type(user)
    if boss_type not in [None, BossType.WARLORD]:
        capture_condition = CaptureCondition.ONLY_DEAD

    # Warlord, add frost effect and warlord stamp
    match boss_type:
        # Warlord, add frost effect and warlord stamp
        case BossType.WARLORD:  # Frost effect and warlord stamp
            effects.append(Effect.FROST)
            stamp = Stamp.WARLORD

        # Admin, Pirate King or Legendary Pirate, add lightning effect
        case BossType.ADMIN | BossType.PIRATE_KING | BossType.LEGENDARY_PIRATE:
            effects.append(Effect.LIGHTNING)

            # Admin, add "Flee on sight" stamp
            if boss_type is BossType.ADMIN:
                stamp = Stamp.FLEE_ON_SIGHT
            # Pirate King and Legendary Pirate, add "Do not engage" stamp
            else:
                stamp = Stamp.DO_NOT_ENGAGE

    return wanted_poster.generate(
        output_poster_path=generate_temp_file_path(c.BOUNTY_POSTER_EXTENSION),
        portrait_vertical_align=VerticalAlignment.TOP,
        capture_condition=capture_condition,
        effects=effects,
        stamp=stamp,
        # Removed the default 16 character cap: beyond it, the library's old fallback logic
        # could end up displaying a blank name (see get_bounty_poster_name above for why
        # names could exceed it unnecessarily). The poster's name plate already auto-scales
        # text that doesn't fit, so longer names just render smaller instead of disappearing
        full_name_max_length=None,
    )


def get_bounty_poster_limit(leaderboard_user: LeaderboardUser) -> int:
    """
    Gets the bounty poster limit of a user by their leaderboard position
    :param leaderboard_user: The user to get the bounty poster limit of
    :return: The bounty poster limit of the user
    """

    leaderboard_rank: LeaderboardRank = get_rank_by_index(leaderboard_user.rank_index)
    return leaderboard_rank.bounty_poster_limit


async def reset_bounty_poster_limit(reset_previous_leaderboard: bool = False) -> None:
    """
    Resets the bounty poster limit

    :param reset_previous_leaderboard: If to reset the limit for users if the previous leaderboard
    """

    from src.service.leaderboard_service import get_leaderboard

    if reset_previous_leaderboard:
        # Reset the limit for users
        previous_leaderboard: Leaderboard = get_leaderboard(1)
        previous_leaderboard_users_id = LeaderboardUser.select(LeaderboardUser.user).where(
            LeaderboardUser.leaderboard == previous_leaderboard
        )
        if previous_leaderboard is not None:
            User.update(bounty_poster_limit=0).where(
                User.id.in_(previous_leaderboard_users_id)
            ).execute()

    # Reset the limit for the current leaderboard users
    current_leaderboard: Leaderboard = get_leaderboard()
    if current_leaderboard is not None:
        for leaderboard_user in current_leaderboard.leaderboard_users:
            leaderboard_user: LeaderboardUser = leaderboard_user

            User.update(bounty_poster_limit=get_bounty_poster_limit(leaderboard_user)).where(
                User.id == leaderboard_user.user
            ).execute()
