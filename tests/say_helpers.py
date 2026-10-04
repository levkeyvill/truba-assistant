"""Проверка готовых ответов по ключу и фактам."""

import config
from core import personas


def variants(key, **fields):
    """Все допустимые варианты выбранного характера с подставленными фактами."""
    preset = config.PERSONA_PRESET
    phrases = personas.PRESETS.get(preset, {}).get("say", {}).get(key)
    return {phrase.format(**fields) for phrase in (phrases or personas.SAY_NEUTRAL[key])}


def assert_said(test, actual, key, **fields):
    test.assertIn(actual, variants(key, **fields))
