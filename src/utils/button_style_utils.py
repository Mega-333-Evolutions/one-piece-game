import re

from src.model.enums.ButtonStyle import ButtonStyle


def normalize_button_text(text: str) -> str:
    """
    Normalize button text for semantic style lookup.
    """

    if not text:
        return ""

    words = re.findall(r"[A-Za-z]+", text.lower())
    return " ".join(words)


def get_semantic_button_style(text: str) -> ButtonStyle | None:
    """
    Return a default style for globally-known semantic button labels.
    """

    normalized_text = normalize_button_text(text)

    if normalized_text in {
        "yes",
        "accept",
        "accept offer",
        "confirm",
        "send request",
        "play",
        "only won",
        "open",
    }:
        return ButtonStyle.SUCCESS

    if normalized_text in {
        "no",
        "reject",
        "cancel",
        "delete",
        "remove",
        "disband",
        "only lost",
        "close",
    }:
        return ButtonStyle.DANGER

    if normalized_text in {
        "back",
        "back to list",
        "start immediately as global",
    }:
        return ButtonStyle.PRIMARY

    return None
