"""AIライティングツール（Streamlit + Gemini API）

起動: streamlit run app.py
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv
from google.genai import errors as genai_errors

load_dotenv()  # gemini.py が GEMINI_MODEL を読む前に .env を読み込む

import gemini  # noqa: E402
from tools import CHAT_SYSTEM, TOOLS, Field, Tool  # noqa: E402

st.set_page_config(page_title="AIライティングツール", page_icon="✍️", layout="wide")

CHAT_PAGE = "chat"
HISTORY_PAGE = "history"
TOOLS_BY_ID = {t.id: t for t in TOOLS}


@dataclass
class Settings:
    api_key: str | None
    model: str
    temperature: float


# ========== 状態の初期化 ==========


def wkey(tool: Tool, f: Field) -> str:
    return f"{tool.id}__{f.key}"


def init_state() -> None:
    ss = st.session_state
    ss.setdefault("history", [])  # 生成結果の履歴（ブラウザを再読み込みすると消える）
    ss.setdefault("chat_messages", [])
    ss.setdefault("results", {})  # ツールごとの最新の出力
    ss.setdefault("convs", {})  # ツールごとの会話（修正指示で続きをやり取りするため）
    ss.setdefault("errors", {})

    # 入力値の初期値を入れる。さらに毎回入れ直すことで、
    # 別のツールに切り替えても入力内容が消えないようにする
    for tool in TOOLS:
        for f in tool.fields:
            if f.kind == "file":
                continue  # ファイルアップローダーは session_state から値を設定できない
            k = wkey(tool, f)
            ss[k] = ss[k] if k in ss else f.initial_value()


# ========== APIキー ==========


def find_api_key() -> str | None:
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if key:
        return key
    try:
        return st.secrets.get("GEMINI_API_KEY")
    except Exception:  # secrets.toml が無い場合
        return None


# ========== サイドバー ==========


def render_sidebar() -> tuple[str, Settings]:
    labels = {t.id: f"{t.icon} {t.name}" for t in TOOLS}
    labels[CHAT_PAGE] = "💬 自由チャット"
    labels[HISTORY_PAGE] = f"📜 履歴（{len(st.session_state.history)}）"

    with st.sidebar:
        st.title("✍️ AIライティング")
        page = st.radio(
            "ツール",
            list(labels),
            format_func=lambda p: labels[p],
            key="page",
            label_visibility="collapsed",
        )

        st.divider()
        env_key = find_api_key()
        with st.expander("⚙️ 設定", expanded=not env_key):
            if env_key:
                api_key = env_key
                st.caption("🔑 APIキー: .env / secrets から読み込み済み")
            else:
                api_key = st.text_input(
                    "Gemini APIキー",
                    type="password",
                    key="api_key_input",
                    help="Google AI Studio（https://aistudio.google.com/apikey）で取得できます。"
                    "毎回入力しなくて済むよう、.env に GEMINI_API_KEY を書いておくのがおすすめです。",
                ) or None

            models = (gemini.list_models(api_key) if api_key else []) or gemini.FALLBACK_MODELS
            default = gemini.DEFAULT_MODEL if gemini.DEFAULT_MODEL in models else models[0]
            model = st.selectbox("モデル", models, index=models.index(default), key="model")

            default_temp = TOOLS_BY_ID[page].temperature if page in TOOLS_BY_ID else 0.7
            temperature = st.slider(
                "創造性（temperature）",
                0.0,
                2.0,
                value=default_temp,
                step=0.1,
                key=f"temp_{page}",
                help="低いほど正確で安定、高いほど自由で多様な文章になります。ツールごとに適した値が初期設定されています。",
                disabled=page == HISTORY_PAGE,
            )

    return page, Settings(api_key=api_key, model=model, temperature=temperature)


# ========== 生成の共通処理 ==========


def run_generation(system: str, contents: list, settings: Settings) -> str | None:
    """結果をストリーミング表示し、成功したら全文を返す"""
    if not settings.api_key:
        st.error("Gemini APIキーが設定されていません。サイドバーの「⚙️ 設定」から入力してください。")
        return None
    try:
        with st.spinner("生成中…"):
            text = st.write_stream(
                gemini.stream(settings.api_key, settings.model, contents, system, settings.temperature)
            )
    except genai_errors.APIError as e:
        st.error(f"Gemini API でエラーが発生しました（{e.code}）: {e.message}")
        return None
    except Exception as e:  # ネットワークエラーなど
        st.error(f"エラーが発生しました: {e}")
        return None

    if not isinstance(text, str) or not text.strip():
        st.warning("結果が空でした。入力内容を変えるか、もう一度お試しください。")
        return None
    return text


def add_history(tool: Tool, title: str, text: str) -> None:
    st.session_state.history.append(
        {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "tool_id": tool.id,
            "title": title,
            "text": text,
        }
    )


def is_filled(value) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return value not in (None, [])


def short(text: str, n: int = 40) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n] + "…"


# ========== ボタンのコールバック ==========
# ボタンが押されたらここで依頼内容を pending に積み、
# 画面の描画時に出力欄の位置でストリーミング生成する


def on_generate(tool: Tool) -> None:
    ss = st.session_state
    values = {f.key: ss.get(wkey(tool, f)) for f in tool.fields}

    missing = [f.label for f in tool.fields if f.required and not is_filled(values[f.key])]
    if missing:
        ss.errors[tool.id] = "「" + "」「".join(missing) + "」を入力してください。"
        return
    if tool.validate and (message := tool.validate(values)):
        ss.errors[tool.id] = message
        return

    first_input = next((values[f.key] for f in tool.fields if f.kind in ("text", "textarea") and is_filled(values[f.key])), "")
    ss.pending = {
        "tool_id": tool.id,
        "contents": [gemini.user_message(tool.build(values))],
        "title": short(first_input) or tool.name,
    }


def on_refine(tool: Tool) -> None:
    ss = st.session_state
    k = f"{tool.id}__refine"
    instruction = ss.get(k, "").strip()
    if not instruction:
        ss.errors[f"{tool.id}__refine"] = "修正指示を入力してください。"
        return
    ss[k] = ""  # 入力欄を空に戻す
    ss.pending = {
        "tool_id": tool.id,
        "contents": ss.convs[tool.id]
        + [gemini.user_message([f"{instruction}\n\n（この指示を反映した成果物の全体を、同じ形式で出力してください）"])],
        "title": "修正: " + short(instruction),
    }


def on_clear(tool: Tool) -> None:
    st.session_state.results.pop(tool.id, None)
    st.session_state.convs.pop(tool.id, None)


# ========== ツールのページ ==========


def render_field(tool: Tool, f: Field) -> None:
    label = f.label + (" *" if f.required else "")
    k = wkey(tool, f)
    common = {"help": f.help, "key": k}
    match f.kind:
        case "text":
            st.text_input(label, placeholder=f.placeholder, **common)
        case "textarea":
            st.text_area(label, placeholder=f.placeholder, height=f.height, **common)
        case "select":
            st.selectbox(label, f.options, **common)
        case "multiselect":
            st.multiselect(label, f.options, **common)
        case "slider":
            st.slider(label, f.min_value, f.max_value, step=f.step, **common)
        case "checkbox":
            st.checkbox(label, **common)
        case "file":
            st.file_uploader(label, type=f.file_types, **common)
        case _:
            raise ValueError(f"未対応の入力タイプです: {f.kind}")


def render_tool_page(tool: Tool, settings: Settings) -> None:
    ss = st.session_state
    st.header(f"{tool.icon} {tool.name}")
    st.caption(tool.description)

    left, right = st.columns([1, 1.3], gap="large")

    # ---------- 入力フォーム ----------
    with left:
        with st.form(f"{tool.id}__form"):
            for f in tool.fields:
                render_field(tool, f)
            st.form_submit_button("✨ 生成する", type="primary", on_click=on_generate, args=(tool,), width="stretch")
        if message := ss.errors.pop(tool.id, None):
            st.error(message)

    # ---------- 出力 ----------
    with right:
        st.subheader("出力")
        box = st.container(border=True)

        pending = ss.get("pending")
        if pending and pending["tool_id"] == tool.id:
            del ss["pending"]
            with box:
                text = run_generation(tool.system, pending["contents"], settings)
            if text:
                ss.results[tool.id] = text
                ss.convs[tool.id] = pending["contents"] + [gemini.model_message(text)]
                add_history(tool, pending["title"], text)
            elif tool.id in ss.results:  # 失敗したときは前回の結果を残して表示
                box.markdown(ss.results[tool.id], unsafe_allow_html=True)
        elif tool.id in ss.results:
            box.markdown(ss.results[tool.id], unsafe_allow_html=True)

        text = ss.results.get(tool.id)
        if not text:
            if not pending:
                box.info("左のフォームに入力して「✨ 生成する」を押してください。")
            return

        render_result_actions(tool, text)


def render_result_actions(tool: Tool, text: str) -> None:
    ss = st.session_state
    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    st.caption(f"{len(text):,} 文字")
    c1, c2, c3 = st.columns(3)
    c1.download_button("⬇️ Markdown", text, file_name=f"{tool.file_prefix}_{stamp}.md", mime="text/markdown", width="stretch")
    c2.download_button("⬇️ テキスト", text, file_name=f"{tool.file_prefix}_{stamp}.txt", mime="text/plain", width="stretch")
    c3.button("🗑️ クリア", on_click=on_clear, args=(tool,), width="stretch")

    with st.expander("📋 コピー用（右上のアイコンでコピー）"):
        st.code(text, language=None, wrap_lines=True)

    # ---------- 修正指示（前回の出力を踏まえて書き直す） ----------
    with st.form(f"{tool.id}__refine_form", border=False):
        st.text_input(
            "🔁 修正指示",
            key=f"{tool.id}__refine",
            placeholder="例: もっと短く / 結論を先に / 2案目をベースにもう少しカジュアルに",
        )
        st.form_submit_button("修正する", on_click=on_refine, args=(tool,))
    if message := ss.errors.pop(f"{tool.id}__refine", None):
        st.error(message)


# ========== 自由チャット ==========


def render_chat_page(settings: Settings) -> None:
    ss = st.session_state
    head, clear = st.columns([5, 1])
    head.header("💬 自由チャット")
    if clear.button("🗑️ 会話をリセット", width="stretch"):
        ss.chat_messages = []
        st.rerun()
    st.caption("文章の相談・添削・言い換えなど、フォームにない依頼は自由にどうぞ。")

    for m in ss.chat_messages:
        with st.chat_message(m["role"]):
            st.markdown(m["content"])

    if prompt := st.chat_input("例: 「よろしくお願いします」以外の結びの言葉を10個ください"):
        ss.chat_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        contents = [
            gemini.user_message([m["content"]]) if m["role"] == "user" else gemini.model_message(m["content"])
            for m in ss.chat_messages
        ]
        with st.chat_message("assistant"):
            text = run_generation(CHAT_SYSTEM, contents, settings)
        if text:
            ss.chat_messages.append({"role": "assistant", "content": text})
        else:
            ss.chat_messages.pop()  # 失敗したら送信前の状態に戻す


# ========== 履歴 ==========


def render_history_page() -> None:
    ss = st.session_state
    st.header("📜 履歴")
    st.caption("このセッションで生成した結果です。ブラウザを再読み込みすると消えるので、残したいものはダウンロードしてください。")

    history = ss.history
    if not history:
        st.info("まだ履歴はありません。")
        return

    def entry_title(h: dict) -> str:
        tool = TOOLS_BY_ID[h["tool_id"]]
        return f"{tool.icon} {tool.name} ｜ {h['title']} ｜ {h['time']}"

    all_md = "\n\n---\n\n".join(f"# {entry_title(h)}\n\n{h['text']}" for h in history)
    c1, c2, _ = st.columns([1, 1, 2])
    c1.download_button(
        "⬇️ すべてMarkdownで保存",
        all_md,
        file_name=f"history_{datetime.now():%Y%m%d_%H%M}.md",
        mime="text/markdown",
        width="stretch",
    )
    if c2.button("🗑️ 履歴をすべて消去", width="stretch"):
        ss.history = []
        st.rerun()

    for i, h in reversed(list(enumerate(history))):
        with st.expander(entry_title(h)):
            st.markdown(h["text"])
            st.download_button(
                "⬇️ Markdownで保存",
                h["text"],
                file_name=f"{TOOLS_BY_ID[h['tool_id']].file_prefix}_{i + 1}.md",
                mime="text/markdown",
                key=f"history_dl_{i}",
            )


# ========== メイン ==========


def main() -> None:
    init_state()
    page, settings = render_sidebar()
    if page == CHAT_PAGE:
        render_chat_page(settings)
    elif page == HISTORY_PAGE:
        render_history_page()
    else:
        render_tool_page(TOOLS_BY_ID[page], settings)


main()
