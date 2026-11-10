"""ライティングツールの定義（入力項目とプロンプト）

ツールを追加したいときは、build 関数を書いて TOOLS に Tool(...) を1つ足すだけでよい。
画面（app.py）は Field の定義から自動で入力フォームを組み立てる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import requests
from google.genai import types

# ========== 共通ルール ==========

COMMON_RULES = """\
# 共通ルール
- 前置き（「承知しました」「以下が〜です」など）や締めの挨拶は書かず、成果物だけを出力する
- 読みやすさのために Markdown（見出し・箇条書き・太字・表）を適切に使う
- 事実が不確かな内容は断定せず、必要に応じて「※要確認」と注記する
- 特に指定がなければ自然な日本語で書く
"""


def system_prompt(role: str) -> str:
    return f"{role}\n\n{COMMON_RULES}"


# ========== データ構造 ==========


@dataclass
class Field:
    """入力フォームの1項目"""

    key: str
    label: str
    kind: str = "text"  # text / textarea / select / multiselect / slider / checkbox / file
    options: list[str] = field(default_factory=list)
    default: Any = None
    placeholder: str = ""
    help: str | None = None
    required: bool = False
    min_value: int = 0
    max_value: int = 100
    step: int = 1
    height: int = 150
    file_types: list[str] = field(default_factory=list)

    def initial_value(self) -> Any:
        if self.default is not None:
            return self.default
        match self.kind:
            case "select":
                return self.options[0]
            case "multiselect":
                return []
            case "slider":
                return self.min_value
            case "checkbox":
                return False
            case _:
                return ""


@dataclass
class Tool:
    """1つのライティングツール"""

    id: str
    name: str
    icon: str
    description: str
    fields: list[Field]
    system: str
    build: Callable[[dict[str, Any]], list[Any]]  # 入力値 → Gemini に渡す contents（文字列 or Part）
    temperature: float = 0.7
    validate: Callable[[dict[str, Any]], str | None] | None = None  # エラー文を返すと生成を止める
    file_prefix: str = "output"


# ========== プロンプト組み立ての補助 ==========


def section(title: str, value: Any) -> str:
    """値が空でなければ「## 見出し + 内容」を返す"""
    if value is None or value == "" or value == []:
        return ""
    if isinstance(value, list):
        value = "、".join(value)
    return f"## {title}\n{str(value).strip()}\n"


def join(*parts: str) -> str:
    return "\n".join(p for p in parts if p)


# ========== ブログ執筆 ==========


def build_blog(v: dict[str, Any]) -> list[Any]:
    extras = v["extras"]
    prompt = join(
        "以下の条件でブログ記事を執筆してください。\n",
        section("テーマ", v["topic"]),
        section("想定読者", v["audience"]),
        section("盛り込みたいキーワード（SEO）", v["keywords"]),
        section("含めたい内容・メモ", v["notes"]),
        section("文体・トーン", v["tone"]),
        section("目安の文字数", f"約{v['length']}文字（本文）"),
        section("追加で入れる要素", extras),
        """\
# 出力形式
1. 最初に「タイトル案」を3つ箇条書きで出す
2. 続けて記事本文を書く（# タイトル → 導入文 → ## 見出し / ### 小見出し → まとめ）
3. 見出しは読者が流し読みしても内容が分かる具体的なものにする
4. キーワードは不自然にならない範囲で、タイトル・見出し・本文に含める""",
        "5. 最後に「メタディスクリプション（120文字前後）」を付ける"
        if "メタディスクリプション" in extras
        else "",
    )
    return [prompt]


BLOG = Tool(
    id="blog",
    name="ブログ執筆",
    icon="📰",
    description="テーマとキーワードから、見出し構成つきのブログ記事を書きます。",
    system=system_prompt(
        "あなたはSEOに強いプロのWebライターです。読者の悩みに寄り添い、具体例を交えた"
        "読みやすく役に立つ記事を書きます。"
    ),
    fields=[
        Field("topic", "テーマ・タイトル", required=True, placeholder="例: 在宅ワークで集中力を保つ方法"),
        Field("audience", "想定読者", placeholder="例: 在宅勤務を始めたばかりの20〜30代会社員"),
        Field("keywords", "キーワード（SEO）", placeholder="例: 在宅ワーク 集中力 コツ"),
        Field(
            "notes",
            "含めたい内容・メモ",
            kind="textarea",
            height=110,
            placeholder="自分の体験談、入れたいポイント、参考情報などを箇条書きで",
        ),
        Field("tone", "文体・トーン", kind="select", options=["親しみやすい（です・ます）", "専門的・信頼感", "カジュアル（だ・である）", "フォーマル"]),
        Field("length", "文字数の目安", kind="slider", min_value=800, max_value=6000, step=200, default=2000),
        Field(
            "extras",
            "追加で入れる要素",
            kind="multiselect",
            options=["目次", "具体例・体験談", "比較表", "FAQ（よくある質問）", "CTA（行動を促す一文）", "メタディスクリプション"],
            default=["具体例・体験談", "メタディスクリプション"],
        ),
    ],
    build=build_blog,
    temperature=0.8,
    file_prefix="blog",
)


# ========== メール作成・返信 ==========


def build_email(v: dict[str, Any]) -> list[Any]:
    is_reply = v["mode"] == "受信メールに返信する"
    count = v["count"]
    prompt = join(
        "以下の条件で返信メールを作成してください。\n" if is_reply else "以下の条件で新規メールを作成してください。\n",
        section("受信したメール", v["received"]) if is_reply else "",
        section("伝えたいこと・要点", v["points"]),
        section("相手との関係", v["relation"]),
        section("トーン", v["tone"]),
        section("差出人（署名に使う名前・会社名など）", v["signature"]),
        f"""\
# 出力形式
- {count}案を作成する。{"各案は「## 案1（〇〇寄り）」のように、方向性が分かる見出しを付ける" if count > 1 else "見出しは不要"}
- 各案は「件名：」の行と本文で構成する{"（返信なので件名は「Re: 元の件名」を基本にする）" if is_reply else ""}
- 宛名・挨拶・本文・結び・署名の順に、そのまま送れる形で書く
- 相手との関係とトーンに合った敬語レベルにする
- 不明な固有名詞や日時は【　】で囲んだプレースホルダにする（例: 【日付】）""",
    )
    return [prompt]


def validate_email(v: dict[str, Any]) -> str | None:
    if v["mode"] == "受信メールに返信する" and not v["received"].strip():
        return "返信する場合は「受信したメール」を貼り付けてください。"
    return None


EMAIL = Tool(
    id="email",
    name="メール作成・返信",
    icon="✉️",
    description="受信メールへの返信文や新規メールを、関係性とトーンに合わせて作ります。",
    system=system_prompt(
        "あなたはビジネスメールの達人です。日本のビジネスマナーに沿い、簡潔で失礼がなく、"
        "相手が次に何をすればよいか分かるメールを書きます。"
    ),
    fields=[
        Field("mode", "種類", kind="select", options=["受信メールに返信する", "新規メールを書く"]),
        Field("received", "受信したメール", kind="textarea", height=180, placeholder="返信する場合は、相手のメール本文を貼り付け"),
        Field(
            "points",
            "伝えたいこと・要点",
            kind="textarea",
            height=110,
            required=True,
            placeholder="例: 打ち合わせは来週火曜の午後なら可能。資料は前日までに送る。",
        ),
        Field("relation", "相手との関係", kind="select", options=["社外（取引先・お客様）", "社内（上司・目上）", "社内（同僚・部下）", "友人・知人"]),
        Field("tone", "トーン", kind="select", options=["丁寧", "標準", "やわらかめ", "カジュアル"], default="標準"),
        Field("signature", "差出人（署名）", placeholder="例: 株式会社〇〇 山田"),
        Field("count", "案の数", kind="slider", min_value=1, max_value=3, default=2),
    ],
    build=build_email,
    validate=validate_email,
    temperature=0.6,
    file_prefix="email",
)


# ========== 要約 ==========

SUMMARY_STYLES = {
    "箇条書き": "重要なポイントを箇条書きで",
    "3行要約": "全体をちょうど3行（各行1文）で",
    "段落（文章）": "まとまった文章（段落）で",
    "見出し付きレポート": "「概要」「要点」「結論・示唆」などの見出しを付けたレポート形式で",
}


def build_summary(v: dict[str, Any]) -> list[Any]:
    contents: list[Any] = []
    text = v["text"].strip()

    upload = v["file"]
    if upload is not None:
        if upload.name.lower().endswith(".pdf"):
            # PDF は Gemini にそのまま渡して読ませる
            contents.append(types.Part.from_bytes(data=upload.getvalue(), mime_type="application/pdf"))
            source = "添付したPDF" + ("と下の文章" if text else "")
        else:
            file_text = upload.getvalue().decode("utf-8", errors="replace")
            text = f"{file_text}\n\n{text}".strip()
            source = "下の文章"
    else:
        source = "下の文章"

    prompt = join(
        f"{source}を要約してください。\n",
        section("形式", SUMMARY_STYLES[v["style"]]),
        section("長さ", v["length"]),
        section("重視する観点・読み手", v["focus"]),
        """\
# ルール
- 元の文章にない情報は加えない
- 数字・固有名詞・結論は正確に残す
- 最後に「キーワード」として重要語を3〜5個挙げる""",
        f"\n# 要約する文章\n\"\"\"\n{text}\n\"\"\"" if text else "",
    )
    contents.append(prompt)
    return contents


def validate_summary(v: dict[str, Any]) -> str | None:
    if not v["text"].strip() and v["file"] is None:
        return "要約する文章を貼り付けるか、ファイルをアップロードしてください。"
    return None


SUMMARY = Tool(
    id="summary",
    name="要約",
    icon="📋",
    description="長い文章やPDF・テキストファイルを、目的に合わせた形式で要約します。",
    system=system_prompt("あなたは情報整理のプロです。長い文章から本質を抜き出し、短時間で理解できる要約を作ります。"),
    fields=[
        Field("text", "要約する文章", kind="textarea", height=220, placeholder="記事・資料・議事録などを貼り付け"),
        Field("file", "またはファイルをアップロード", kind="file", file_types=["txt", "md", "pdf"], help="PDF / テキスト / Markdown に対応"),
        Field("style", "形式", kind="select", options=list(SUMMARY_STYLES)),
        Field("length", "長さ", kind="select", options=["短め（要点だけ）", "標準", "詳しめ"], default="標準"),
        Field("focus", "重視する観点・読み手", placeholder="例: 経営者向けに、コストへの影響を中心に"),
    ],
    build=build_summary,
    validate=validate_summary,
    temperature=0.3,
    file_prefix="summary",
)


# ========== 校正・リライト ==========

REWRITE_MODES = {
    "誤字脱字・文法チェック": "誤字脱字・文法の誤り・表記ゆれ・不自然な表現を直す。内容と文体は変えない",
    "読みやすくリライト": "意味を変えずに、一文を短くし、構成を整理して読みやすく書き直す",
    "ビジネス文・敬語に変換": "ビジネスの場で使える丁寧で正しい敬語の文章にする",
    "やわらかい表現に変換": "親しみやすく、やわらかい印象の文章にする",
    "簡潔に短縮": "要点を残して、元の半分程度の長さに短くする",
    "詳しく膨らませる": "具体例や説明を補って、元の1.5〜2倍程度の分量に膨らませる",
}


def build_rewrite(v: dict[str, Any]) -> list[Any]:
    prompt = join(
        "次の文章を、指定のモードで修正してください。\n",
        section("モード", f"{v['mode']}：{REWRITE_MODES[v['mode']]}"),
        section("補足の指示", v["notes"]),
        "# 出力形式\n## 修正後の文章\n（修正後の全文）",
        "\n## 主な変更点\n| 修正前 | 修正後 | 理由 |\n の表で主な変更点をまとめる（多い場合は重要なもの10件まで）"
        if v["show_changes"]
        else "",
        f"\n# 元の文章\n\"\"\"\n{v['text']}\n\"\"\"",
    )
    return [prompt]


REWRITE = Tool(
    id="rewrite",
    name="校正・リライト",
    icon="🖊️",
    description="誤字脱字チェック、読みやすい書き直し、敬語への変換、短縮・加筆などを行います。",
    system=system_prompt("あなたは経験豊富な編集者・校正者です。書き手の意図を尊重しながら、文章の質を高めます。"),
    fields=[
        Field("text", "文章", kind="textarea", height=240, required=True, placeholder="校正・リライトしたい文章を貼り付け"),
        Field("mode", "モード", kind="select", options=list(REWRITE_MODES)),
        Field("notes", "補足の指示", placeholder="例: 「です・ます」に統一 / 専門用語は残す"),
        Field("show_changes", "変更点の一覧も出す", kind="checkbox", default=True),
    ],
    build=build_rewrite,
    temperature=0.3,
    file_prefix="rewrite",
)


# ========== 翻訳 ==========


def build_translate(v: dict[str, Any]) -> list[Any]:
    prompt = join(
        f"次の文章を{v['target']}に翻訳してください。\n",
        section("翻訳のスタイル", v["style"]),
        "# 出力形式\n## 翻訳\n（訳文）",
        "\n## 解説\n訳し方に工夫した箇所、ニュアンスの違い、注意すべき表現を日本語で簡潔に説明する"
        if v["explain"]
        else "",
        f"\n# 元の文章\n\"\"\"\n{v['text']}\n\"\"\"",
    )
    return [prompt]


TRANSLATE = Tool(
    id="translate",
    name="翻訳",
    icon="🌐",
    description="自然な訳文を作ります。必要に応じてニュアンスの解説も付けます。",
    system=system_prompt("あなたはプロの翻訳者です。直訳ではなく、訳文を読む人にとって自然で、意図が正しく伝わる翻訳をします。"),
    fields=[
        Field("text", "翻訳する文章", kind="textarea", height=220, required=True),
        Field(
            "target",
            "翻訳先の言語",
            kind="select",
            options=["英語", "日本語", "中国語（簡体字）", "中国語（繁体字）", "韓国語", "スペイン語", "フランス語", "ドイツ語"],
        ),
        Field("style", "スタイル", kind="select", options=["自然な訳（意訳寄り）", "原文に忠実（直訳寄り）", "ビジネス向け（フォーマル）", "カジュアル（会話調）"]),
        Field("explain", "訳し方の解説も付ける", kind="checkbox", default=False),
    ],
    build=build_translate,
    temperature=0.3,
    file_prefix="translation",
)


# ========== SNS投稿 ==========

SNS_RULES = {
    "X（旧Twitter）": "1投稿140文字以内（全角）。冒頭1行で興味を引く",
    "Instagram": "キャプションとして、最初の1〜2行で引き込み、改行を多めに。最後にハッシュタグをまとめる",
    "Threads": "500文字以内。会話のような自然な語り口",
    "Facebook": "やや長めでもよい。ストーリー性を持たせる",
    "LinkedIn": "ビジネスパーソン向け。学びや知見が伝わる構成",
    "note（紹介文）": "記事への誘導文として、読みたくなる要約を200文字程度で",
}


def build_sns(v: dict[str, Any]) -> list[Any]:
    rules = [SNS_RULES[v["platform"]]]
    rules.append("関連するハッシュタグを3〜8個付ける" if v["hashtags"] else "ハッシュタグは付けない")
    rules.append("絵文字を適度に使う" if v["emoji"] else "絵文字は使わない")
    prompt = join(
        f"{v['platform']}向けの投稿文を{v['count']}案作成してください。\n",
        section("投稿したい内容", v["topic"]),
        section("目的", v["purpose"]),
        section("トーン", v["tone"]),
        section("ルール", "\n".join(f"- {r}" for r in rules)),
        "# 出力形式\n各案を「## 案1」のような見出しで区切り、そのまま投稿できる本文だけを書く。",
    )
    return [prompt]


SNS = Tool(
    id="sns",
    name="SNS投稿",
    icon="📱",
    description="X・Instagram・Threads などの投稿文を、媒体の特徴に合わせて作ります。",
    system=system_prompt("あなたはSNSマーケティングに詳しいコピーライターです。媒体ごとの文化に合った、反応されやすい投稿を作ります。"),
    fields=[
        Field("topic", "投稿したい内容", kind="textarea", height=130, required=True, placeholder="例: 新しいブログ記事「在宅ワークの集中術」を公開した"),
        Field("platform", "媒体", kind="select", options=list(SNS_RULES)),
        Field("purpose", "目的", kind="select", options=["告知・宣伝", "情報発信・ノウハウ", "共感・日常", "集客・リンクへの誘導"]),
        Field("tone", "トーン", kind="select", options=["親しみやすい", "ていねい", "元気・ポップ", "落ち着いた・知的"]),
        Field("count", "案の数", kind="slider", min_value=1, max_value=5, default=3),
        Field("hashtags", "ハッシュタグを付ける", kind="checkbox", default=True),
        Field("emoji", "絵文字を使う", kind="checkbox", default=True),
    ],
    build=build_sns,
    temperature=0.9,
    file_prefix="sns",
)


# ========== タイトル・キャッチコピー ==========


def build_copy(v: dict[str, Any]) -> list[Any]:
    prompt = join(
        f"次の内容について「{v['kind']}」の案を{v['count']}個考えてください。\n",
        section("内容・概要", v["subject"]),
        section("ターゲット", v["audience"]),
        section("訴求したいポイント", v["appeal"]),
        """\
# 出力形式
1. | No. | 案 | 狙い・ポイント | の表で全案を出す
2. 切り口（数字・疑問形・ベネフィット・意外性など）がばらけるようにする
3. 最後に「## おすすめ TOP3」として、理由付きで3つ選ぶ""",
    )
    return [prompt]


COPY = Tool(
    id="copy",
    name="タイトル・キャッチコピー",
    icon="💡",
    description="記事タイトル、キャッチコピー、メール件名、ネーミングなどの案を一度にたくさん出します。",
    system=system_prompt("あなたは数々のヒットを生んだコピーライターです。短い言葉で心をつかむ表現を考えます。"),
    fields=[
        Field("subject", "内容・概要", kind="textarea", height=130, required=True, placeholder="例: 30代向けの時短レシピをまとめた記事"),
        Field(
            "kind",
            "種類",
            kind="select",
            options=["ブログ記事のタイトル", "キャッチコピー", "メールの件名", "YouTube動画のタイトル", "商品・サービス名（ネーミング）", "セミナー・イベント名"],
        ),
        Field("audience", "ターゲット", placeholder="例: 共働きで忙しい30代"),
        Field("appeal", "訴求したいポイント", placeholder="例: 10分で作れる / 洗い物が少ない"),
        Field("count", "案の数", kind="slider", min_value=5, max_value=20, default=10),
    ],
    build=build_copy,
    temperature=1.0,
    file_prefix="copy",
)


# ========== アイデア出し・構成案 ==========

IDEA_KINDS = {
    "ブログ・記事のネタ": "書けそうな記事テーマを、想定読者と切り口つきで",
    "記事・資料の構成案（アウトライン）": "見出し（H2/H3）レベルの構成案と、各見出しで書く内容のメモを",
    "企画・イベント案": "企画名・概要・実現のポイントつきで",
    "動画・YouTubeのネタ": "タイトル案と動画の流れつきで",
    "課題解決のアイデア": "アイデアと、メリット・デメリットつきで",
}


def build_ideas(v: dict[str, Any]) -> list[Any]:
    kind = v["kind"]
    is_outline = kind.startswith("記事・資料の構成案")
    prompt = join(
        f"次のテーマについて、{kind}を出してください。\n",
        section("テーマ", v["theme"]),
        section("前提・条件", v["conditions"]),
        section("出し方", IDEA_KINDS[kind]),
        "" if is_outline else section("数", f"{v['count']}個（ありきたりな案だけでなく、意外性のある案も混ぜる）"),
    )
    return [prompt]


IDEAS = Tool(
    id="ideas",
    name="アイデア出し・構成案",
    icon="🧠",
    description="ネタ出し、記事の構成案（アウトライン）、企画案などのブレストを手伝います。",
    system=system_prompt("あなたは発想力豊かな企画・編集のプロです。実用的な案と、視点をずらした新鮮な案の両方を出します。"),
    fields=[
        Field("theme", "テーマ", required=True, placeholder="例: 副業としてのWeb制作"),
        Field("kind", "出してほしいもの", kind="select", options=list(IDEA_KINDS)),
        Field("conditions", "前提・条件", kind="textarea", height=100, placeholder="例: 初心者向け / 予算はかけない / 自分の強みは〇〇"),
        Field("count", "案の数（構成案では無視）", kind="slider", min_value=3, max_value=20, default=10),
    ],
    build=build_ideas,
    temperature=1.0,
    file_prefix="ideas",
)


# ========== メモ整形・議事録 ==========

NOTE_FORMATS = {
    "議事録": "「日時・参加者（分かる範囲）」「議題」「決定事項」「議論の要点」「ToDo（担当・期限）」「次回までの課題」の見出しで",
    "ToDoリスト": "やるべきことをチェックボックス（- [ ]）形式で、優先度順に。担当や期限が分かれば併記して",
    "報告書": "「概要」「経緯」「現状」「課題」「今後の対応」の見出しで、上司に提出できる文体で",
    "日報": "「今日やったこと」「成果・気づき」「課題」「明日やること」の見出しで",
    "整理されたメモ": "話題ごとに見出しを付け、箇条書きで分かりやすく",
}


def build_notes(v: dict[str, Any]) -> list[Any]:
    prompt = join(
        f"次の走り書きのメモ・文字起こしを「{v['format']}」に整形してください。\n",
        section("形式", NOTE_FORMATS[v["format"]]),
        section("補足", v["notes"]),
        "# ルール\n- メモにない事実を作らない。不明な項目は「（不明）」とする\n- 話し言葉や重複は整理して、簡潔な書き言葉にする",
        f"\n# 元のメモ\n\"\"\"\n{v['text']}\n\"\"\"",
    )
    return [prompt]


NOTES = Tool(
    id="notes",
    name="メモ整形・議事録",
    icon="🗂️",
    description="走り書きのメモや文字起こしを、議事録・ToDo・報告書・日報の形に整えます。",
    system=system_prompt("あなたは優秀な秘書です。散らかった情報を、あとから誰が読んでも分かる形に整理します。"),
    fields=[
        Field("text", "メモ・文字起こし", kind="textarea", height=240, required=True, placeholder="会議のメモ、音声の文字起こし、思いつきの箇条書きなどを貼り付け"),
        Field("format", "整形する形式", kind="select", options=list(NOTE_FORMATS)),
        Field("notes", "補足", placeholder="例: 会議名は「週次定例」、参加者は山田・佐藤・鈴木"),
    ],
    build=build_notes,
    temperature=0.3,
    file_prefix="notes",
)


# ========== 一覧 ==========

# ========== URL要約 ==========


def build_url_summary(v: dict[str, Any]) -> list[Any]:
    res = requests.get(v["url"].strip(), verify=False)
    return [join("次のWebページの内容を要約してください。\n", section("ページの内容", res.text))]


URL_SUMMARY = Tool(
    id="url_summary",
    name="URL要約",
    icon="🔗",
    description="URLを入れると、そのページの内容を要約します。",
    system=system_prompt("あなたは要約の専門家です。"),
    fields=[Field("url", "URL", required=True, placeholder="https://...")],
    build=build_url_summary,
    file_prefix="url_summary",
)

TOOLS: list[Tool] = [
    URL_SUMMARY,BLOG, EMAIL, SUMMARY, REWRITE, TRANSLATE, SNS, COPY, IDEAS, NOTES]

CHAT_SYSTEM = system_prompt(
    "あなたは頼れるライティングアシスタントです。文章の作成・添削・アイデア出し・言葉選びの相談など、"
    "書くことに関するあらゆる依頼に応えます。依頼があいまいなときは、よくある解釈で一度書いてから、"
    "調整できる点を短く提案してください。"
).replace("- 前置き（「承知しました」「以下が〜です」など）や締めの挨拶は書かず、成果物だけを出力する\n", "")
