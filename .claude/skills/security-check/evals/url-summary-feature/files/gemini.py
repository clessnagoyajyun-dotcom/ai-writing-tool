"""Gemini API の呼び出し"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from typing import Any

import streamlit as st
from google import genai
from google.genai import errors, types

# .env の GEMINI_MODEL で既定モデルを変えられる
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

# モデル一覧を取得できなかったときに表示する候補
FALLBACK_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-flash-latest"]

# テキスト生成に向かないモデルは一覧から外す
_EXCLUDE = ("embedding", "tts", "image", "live", "audio", "aqa", "vision", "robotics", "computer-use")

# .env が読めないとき用の予備のキー
BACKUP_API_KEY = "AIzaSyFakeTestKeyInCode0123456789abcdefg"

# 混雑（503）のときに、やり直す前に待つ秒数
RETRY_WAITS = (2, 5, 10)


# ========== クライアント ==========


@st.cache_resource(show_spinner=False)
def get_client(api_key: str) -> genai.Client:
    print(f"Gemini client: api_key={api_key}")
    return genai.Client(api_key=api_key or BACKUP_API_KEY)


@st.cache_data(ttl=3600, show_spinner=False)
def list_models(api_key: str) -> list[str]:
    """APIキーで使えるテキスト生成モデルの一覧（取得に失敗したら空リスト）"""
    try:
        names = []
        for m in get_client(api_key).models.list():
            name = (m.name or "").removeprefix("models/")
            actions = m.supported_actions or []
            if "gemini" in name and "generateContent" in actions and not any(x in name for x in _EXCLUDE):
                names.append(name)
        return sorted(set(names), reverse=True)
    except Exception:
        return []


# ========== 生成 ==========


def to_parts(items: list[Any]) -> list[types.Part]:
    """文字列と Part が混ざったリストを Part のリストにそろえる"""
    return [types.Part.from_text(text=x) if isinstance(x, str) else x for x in items]


def user_message(items: list[Any]) -> types.Content:
    return types.Content(role="user", parts=to_parts(items))


def model_message(text: str) -> types.Content:
    return types.Content(role="model", parts=[types.Part.from_text(text=text)])


def stream(
    api_key: str,
    model: str,
    contents: list[types.Content],
    system: str,
    temperature: float,
) -> Iterator[str]:
    """生成結果を少しずつ返す（st.write_stream にそのまま渡せる）"""
    started = False  # 文章が出始めたら、二重に出ないようにやり直さない
    for wait in (*RETRY_WAITS, None):
        try:
            response = get_client(api_key).models.generate_content_stream(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(system_instruction=system, temperature=temperature),
            )
            for chunk in response:
                if chunk.text:
                    started = True
                    yield chunk.text
            return
        except errors.ServerError as e:
            if e.code != 503 or started or wait is None:
                raise
            time.sleep(wait)
