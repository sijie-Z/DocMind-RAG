"""Translation and language tools."""

import json
import logging
from typing import Any

from app.agent.registry import register_tool

logger = logging.getLogger(__name__)

LANG_NAMES = {
    "zh": "中文", "en": "English", "ja": "日本語", "ko": "한국어",
    "fr": "Français", "de": "Deutsch", "es": "Español", "pt": "Português",
    "ru": "Русский", "ar": "العربية", "hi": "हिन्दी", "th": "ไทย",
    "vi": "Tiếng Việt", "it": "Italiano", "nl": "Nederlands",
    "pl": "Polski", "tr": "Türkçe", "id": "Bahasa Indonesia",
    "ms": "Bahasa Melayu", "fil": "Filipino",
}


@register_tool(
    name="translate_text",
    description=(
        "Translate text between languages using AI. Supports 30+ languages "
        "including Chinese, English, Japanese, Korean, French, German, "
        "Spanish, Russian, Arabic, and more. Provide source language or 'auto' for auto-detection."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to translate",
            },
            "target_language": {
                "type": "string",
                "description": "Target language code (zh, en, ja, ko, fr, de, es, etc.) or full name (Chinese, English, etc.)",
                "default": "zh",
            },
            "source_language": {
                "type": "string",
                "description": "Source language code or 'auto' for auto-detection",
                "default": "auto",
            },
        },
        "required": ["text", "target_language"],
    },
    tags=["language", "translation"],
)
async def translate_text(
    text: str,
    target_language: str = "zh",
    source_language: str = "auto",
    **_: Any,
) -> str:
    if not text or len(text) < 2:
        return "Error: Text too short to translate."

    # Normalize language codes
    target_lang = _normalize_lang(target_language)
    source_lang = _normalize_lang(source_language) if source_language != "auto" else "auto"

    try:
        from app.core.config import settings
        from app.dependencies import get_rag_pipeline

        pipeline = get_rag_pipeline()
        if not pipeline.openai_client:
            return "Translation unavailable: LLM not configured."

        source_hint = "" if source_lang == "auto" else f" from {LANG_NAMES.get(source_lang, source_lang)}"
        target_name = LANG_NAMES.get(target_lang, target_lang)

        resp = await pipeline.openai_client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are a professional translator. "
                        f"Translate the following text{source_hint} to {target_name} ({target_lang}). "
                        f"Provide ONLY the translation, no explanations."
                    ),
                },
                {"role": "user", "content": text[:4000]},
            ],
            temperature=0.1,
            max_tokens=2000,
        )
        return resp.choices[0].message.content or "Translation failed."
    except Exception as e:
        return f"Translation error: {type(e).__name__}: {e}"


@register_tool(
    name="detect_language",
    description=(
        "Detect the language of the input text. Returns the language name, "
        "ISO 639-1 code, and confidence level."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to analyze for language detection",
            },
        },
        "required": ["text"],
    },
    tags=["language"],
)
async def detect_language(text: str, **_: Any) -> str:
    if not text or len(text) < 3:
        return "Error: Text too short for reliable detection (minimum 3 characters)."

    try:
        # Try using langdetect library
        try:
            from langdetect import DetectorFactory, detect_langs
            DetectorFactory.seed = 0
            results = detect_langs(text[:500])
            langs = []
            for r in results[:3]:
                code = r.lang
                name = LANG_NAMES.get(code, code)
                langs.append(f"{name} ({code}): {r.prob:.0%}")
            return "Language detection:\n" + "\n".join(f"- {l}" for l in langs)
        except ImportError:
            pass

        # Fallback: simple heuristic detection
        return _heuristic_detect(text)

    except Exception as e:
        return f"Detection error: {type(e).__name__}: {e}"


def _normalize_lang(lang: str) -> str:
    """Normalize language input to ISO 639-1 code."""
    lang_lower = lang.lower().strip()

    # Direct code match
    if lang_lower in LANG_NAMES:
        return lang_lower

    # Full name match
    name_to_code = {v.lower(): k for k, v in LANG_NAMES.items()}
    if lang_lower in name_to_code:
        return name_to_code[lang_lower]

    # Common aliases
    aliases = {
        "chinese": "zh", "english": "en", "japanese": "ja",
        "korean": "ko", "french": "fr", "german": "de",
        "spanish": "es", "portuguese": "pt", "russian": "ru",
        "arabic": "ar", "hindi": "hi",
    }
    if lang_lower in aliases:
        return aliases[lang_lower]

    return lang_lower  # return as-is if no match


def _heuristic_detect(text: str) -> str:
    """Simple heuristic language detection based on character ranges."""
    cjk_count = sum(1 for c in text if '一' <= c <= '鿿' or '㐀' <= c <= '䶿')
    hiragana_count = sum(1 for c in text if '぀' <= c <= 'ゟ')
    katakana_count = sum(1 for c in text if '゠' <= c <= 'ヿ')
    hangul_count = sum(1 for c in text if '가' <= c <= '힯')
    latin_count = sum(1 for c in text if c.isascii() and c.isalpha())

    total = len(text)

    if cjk_count / total > 0.3:
        return json.dumps({"language": "中文 (zh)", "confidence": "high"}, ensure_ascii=False)
    if hiragana_count + katakana_count > 3:
        return json.dumps({"language": "日本語 (ja)", "confidence": "high"}, ensure_ascii=False)
    if hangul_count / total > 0.3:
        return json.dumps({"language": "한국어 (ko)", "confidence": "high"}, ensure_ascii=False)
    if latin_count / total > 0.5:
        return json.dumps({"language": "English (en)", "confidence": "medium"}, ensure_ascii=False)

    return json.dumps({"language": "Unknown", "confidence": "low"}, ensure_ascii=False)
