# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A personal AI writing tool (Japanese UI) built with Python + Streamlit + the Gemini API (`google-genai` SDK). It runs locally on one PC and has no database or login. See `README.md` for the user-facing feature list.

## Commands (Windows / PowerShell)

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env     # then set GEMINI_API_KEY (optional: GEMINI_MODEL)
streamlit run app.py            # http://localhost:8501
```

There are no tests, linter or build step. To check a change, run the app and use the tool you changed. Calling the API needs a real key.

## Architecture

Three modules, each with one job:

- **`tools.py`** holds the data. Each writing tool is a `Tool` dataclass: a list of `Field`s, a system prompt, a `build(values) -> contents` function, an optional `validate(values) -> error | None`, and a default `temperature`. Everything goes in the `TOOLS` list at the bottom. System prompts are built with `system_prompt(role)`, which adds the shared `COMMON_RULES` (output only the deliverable, use Markdown, mark uncertain facts with ※要確認). User prompts are assembled with `section()` / `join()`, which skip empty values. `build` returns a mix of strings and `types.Part` (PDF uploads are passed as `Part.from_bytes`; text files are decoded into the prompt).
- **`app.py`** is generic UI. It never mentions a specific tool: the sidebar, form, output pane, result actions and refine box are all generated from the `Tool`/`Field` definitions. Adding a tool means only editing `tools.py`. A new `Field.kind` needs a case in both `render_field()` (app.py) and `Field.initial_value()` (tools.py). Chat and history are special pages (`CHAT_PAGE`, `HISTORY_PAGE`) rather than `Tool`s.
- **`gemini.py`** is the only place that touches the SDK: a cached client, a model list (filtered to text-generation Gemini models, falling back to `FALLBACK_MODELS`), message helpers (`user_message`, `model_message`) and `stream()`, which yields text chunks for `st.write_stream`.

### Streamlit state flow (non-obvious)

- Widget keys are `"{tool.id}__{field.key}"` (`wkey()`). `init_state()` writes every widget value back into `session_state` on every rerun, which keeps a tool's inputs when you switch to another tool (Streamlit would otherwise drop the keys of widgets that aren't drawn). File uploaders are skipped because their value can't be set.
- Generation is split across two steps on purpose. Button `on_click` callbacks (`on_generate`, `on_refine`) only validate and put a request in `session_state.pending`. Then `render_tool_page` consumes `pending` during the normal render, so the streamed output appears inside the output box.
- `session_state.convs[tool.id]` keeps the full Gemini conversation for each tool. A refine instruction is appended to it, so the model rewrites the previous result. `results` holds the latest text, and `history` collects every generation in the session. All of this is lost on browser reload by design.
- `load_dotenv()` must run before `import gemini`, because `gemini.DEFAULT_MODEL` reads `GEMINI_MODEL` at import time. The API key is looked up in this order: env `GEMINI_API_KEY` → `GOOGLE_API_KEY` → `st.secrets` → a sidebar input.

## Conventions

- User-facing text, docstrings and comments are in Japanese. Section headers use `# ========== … ==========`.
- Uses Python 3.10+ syntax (`match`, `X | None`) and the newer Streamlit `width="stretch"` argument (`streamlit>=1.40`).
- `.env` and `.streamlit/secrets.toml` are gitignored. Never commit API keys.
