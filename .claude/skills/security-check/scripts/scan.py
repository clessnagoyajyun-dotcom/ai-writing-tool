"""機械的なセキュリティ確認（標準ライブラリだけで動く）

使い方: python scan.py <プロジェクトのフォルダ>

見つけた秘密の値は、必ず伏せ字にして表示する。
ここで出る項目は手がかりなので、本当に問題かどうかはコードを読んで判断する。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SKIP_DIRS = {".venv", "venv", "__pycache__", ".git", "backup", "security-reports", "node_modules", ".claude"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".html", ".example"}

# 秘密の値らしい文字列
SECRET_PATTERNS = [
    ("Google の APIキー", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("OpenAI 形式のキー", re.compile(r"sk-(?!ant-)[0-9A-Za-z_\-]{20,}")),
    ("Anthropic 形式のキー", re.compile(r"sk-ant-[0-9A-Za-z_\-]{20,}")),
    ("GitHub のトークン", re.compile(r"gh[pousr]_[0-9A-Za-z]{30,}")),
    (
        "コードに直接書かれたキーやパスワード",
        re.compile(r"""(?i)(?:api_?key|secret|password|passwd|token)\s*=\s*["']([^"'\s]{12,})["']"""),
    ),
]

# 使い方しだいで危険になる書き方
RISKY_PATTERNS = [
    ("HTML をそのまま表示", re.compile(r"unsafe_allow_html\s*=\s*True|\bst\.html\(|components\.(?:v1\.)?html\(")),
    ("文字列をプログラムとして実行", re.compile(r"\beval\(|\bexec\(")),
    ("外部コマンドの実行", re.compile(r"\bsubprocess\.|\bos\.system\(|\bos\.popen\(|shell\s*=\s*True")),
    ("安全でないデータ読み込み", re.compile(r"\bpickle\.loads?\(|\bmarshal\.loads?\(|yaml\.load\(")),
    ("URL への接続", re.compile(r"\brequests\.(?:get|post|put|delete|head)\(|\burlopen\(|\bhttpx\.(?:get|post)\(")),
    ("証明書の確認を無効化", re.compile(r"verify\s*=\s*False")),
    ("ファイルの読み書き", re.compile(r"\bopen\(|\.write_text\(|\.write_bytes\(")),
    ("例外の内容を画面に表示", re.compile(r"st\.(?:error|exception|write)\(.*\{e\}|st\.exception\(")),
    ("ログや print への出力", re.compile(r"\bprint\(|\blogging\.\w+\(|\blogger\.\w+\(")),
]

PLACEHOLDER = re.compile(r"(?i)ここに|your|example|xxx|dummy|placeholder|<.*>")

# キーの形式は変わることがあるので、「名前=値」の形でも探す（.env や設定ファイル用）
ASSIGNMENT = re.compile(
    r"""(?i)^\s*(?:export\s+)?([A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*)\s*=\s*["']?([^\s"'#]{12,})"""
)
ASSIGNMENT_SUFFIXES = {".toml", ".ini", ".cfg", ".yaml", ".yml"}


def mask(value: str) -> str:
    """固定の伏せ字にする。種類が分かる決まった先頭部分だけは残す"""
    m = re.match(r"AIza|sk-ant-|sk-|gh[pousr]_", value)
    return (m.group(0) if m else "") + "********"


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if SKIP_DIRS & set(path.relative_to(root).parts[:-1]):
            continue
        if path.name.startswith(".env") or path.suffix.lower() in TEXT_SUFFIXES or path.name == ".gitignore":
            yield path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def is_env_file(path: Path) -> bool:
    return path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example"


def env_values(root: Path) -> list[tuple[str, str]]:
    """.env に書かれた秘密の値を (名前, 値) で返す"""
    path = root / ".env"
    if not path.exists():
        return []
    matches = (ASSIGNMENT.match(line) for line in read_text(path).splitlines())
    return [(m.group(1), m.group(2)) for m in matches if m and not PLACEHOLDER.search(m.group(2))]


def scan_secrets(root: Path) -> list[str]:
    found = []
    secrets = env_values(root)
    for path in iter_files(root):
        rel = path.relative_to(root).as_posix()
        safe_place = is_env_file(path) or path.name == "secrets.toml"
        note = "（保管場所として正しい）" if safe_place else "【要確認】"
        for no, line in enumerate(read_text(path).splitlines(), 1):
            hits: dict[str, str] = {}  # 値 → 種類（同じ値を二重に数えない）
            for label, pattern in SECRET_PATTERNS:
                for m in pattern.finditer(line):
                    hits.setdefault(m.group(m.lastindex or 0), label)
            if safe_place or path.suffix in ASSIGNMENT_SUFFIXES:
                if m := ASSIGNMENT.match(line):
                    hits.setdefault(m.group(2), f"{m.group(1)} の値")
            if not safe_place:  # .env の値が別のファイルに写っていないか（形式に関係なく見つかる）
                for name, value in secrets:
                    if value in line:
                        hits[value] = f".env の {name} と同じ値"
            for value, label in hits.items():
                if not PLACEHOLDER.search(value):
                    found.append(f"{rel}:{no} {label}: {mask(value)} {note}")
    return found


def scan_risky(root: Path) -> list[str]:
    found = []
    for path in iter_files(root):
        if path.suffix != ".py":
            continue
        rel = path.relative_to(root).as_posix()
        for no, line in enumerate(read_text(path).splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for label, pattern in RISKY_PATTERNS:
                if pattern.search(line):
                    found.append(f"{rel}:{no} {label}: {line.strip()[:100]}")
    return found


def config_value(text: str, section: str, key: str) -> str | None:
    """config.toml の値を読む（Python 3.10 でも動くよう簡単な読み方にしている）"""
    current = ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
        elif current == section and "=" in line:
            name, value = line.split("=", 1)
            if name.strip() == key:
                return value.strip().strip("\"'")
    return None


def scan_settings(root: Path) -> list[str]:
    notes = []

    gitignore = root / ".gitignore"
    ignored = read_text(gitignore).split() if gitignore.exists() else []
    for name in (".env", ".streamlit/secrets.toml"):
        notes.append(f"{'OK' if name in ignored else '要確認'}: .gitignore の {name}")

    has_git = (root / ".git").exists() or (root.parent / ".git").exists()
    notes.append(f"git: {'使用中' if has_git else '未使用（.gitignore は共有時に .env を守らない）'}")
    notes.append(f".env: {'あり' if (root / '.env').exists() else 'なし'}")
    notes.append(f"secrets.toml: {'あり' if (root / '.streamlit/secrets.toml').exists() else 'なし'}")

    config = root / ".streamlit/config.toml"
    text = read_text(config) if config.exists() else ""
    address = config_value(text, "server", "address")
    notes.append(f"server.address: {address or '未設定（全ての入口で待ち受ける）'}")
    for key, safe in (("enableCORS", "true"), ("enableXsrfProtection", "true"), ("enableStaticServing", "false")):
        value = config_value(text, "server", key)
        state = "初期値のまま" if value is None else value
        mark = "要確認" if value is not None and value.lower() != safe else "OK"
        notes.append(f"{mark}: server.{key}: {state}")
    notes.append(f"server.maxUploadSize: {config_value(text, 'server', 'maxUploadSize') or '未設定（200MB）'}")

    requirements = root / "requirements.txt"
    if requirements.exists():
        lines = [x.strip() for x in read_text(requirements).splitlines() if x.strip() and not x.startswith("#")]
        loose = [x for x in lines if "==" not in x]
        notes.append(f"requirements.txt: {len(lines)}件中 {len(loose)}件はバージョン未固定")
    return notes


def print_section(title: str, items: list[str]) -> None:
    print(f"\n## {title}")
    print("\n".join(f"- {x}" for x in items) if items else "- 該当なし")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows でも日本語を文字化けさせない
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    print(f"# 機械的な確認の結果: {root.name}")
    print_section("秘密の値らしき文字列（伏せ字）", scan_secrets(root))
    print_section("使い方しだいで危険な書き方", scan_risky(root))
    print_section("設定", scan_settings(root))


if __name__ == "__main__":
    main()
