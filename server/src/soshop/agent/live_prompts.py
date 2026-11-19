"""Prompts and cue text for the live host.

The live host is driven by *director cues*: short private stage directions
sent as user turns (``[ĐẠO DIỄN] ...`` / ``[DIRECTOR] ...``). The system prompt
tells the model to act on them and never read them aloud. Viewer comments come
in as ``[BÌNH LUẬN] name: text`` / ``[COMMENT] name: text`` so the model can
answer them by name. Keeping every cue a user turn also satisfies chat
templates that reject a request with no user message.

Dependency-light on purpose (no LiveKit imports): the API's script generator
and the unit tests import from here.
"""

# Prompt text is kept one instruction per line for readability.
# ruff: noqa: E501

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

DIRECTOR_TAG = {"vi": "[ĐẠO DIỄN]", "en": "[DIRECTOR]"}
COMMENT_TAG = {"vi": "[BÌNH LUẬN]", "en": "[COMMENT]"}

PRODUCT_BEATS = 3
"""A product is presented in this many spoken turns (lead-in, detail, price +
call to action). Short turns keep the delivery conversational and make pause,
skip and viewer questions land between turns rather than mid-monologue."""


@dataclass(frozen=True)
class HostProfile:
    name: str
    intro: str
    personality: str
    language: str
    notes: str


@dataclass(frozen=True)
class ProductFacts:
    name: str
    price: float | None
    currency: str
    description: str
    highlights: str
    script: str


@dataclass(frozen=True)
class ViewerComment:
    name: str
    text: str


def _lang(language: str) -> str:
    return "vi" if language == "vi" else "en"


# ------------------------------------------------------------ text cleaning

_BRACKET_TAG_RE = re.compile(r"\[[^\]\n]{0,40}\]")
_MARKUP_RE = re.compile(r"[*_#`~<>|]+")
_EMOJI_RE = re.compile(
    "["
    "\U0001f000-\U0001faff"
    "\U00002600-\U000027bf"
    "\U0001f1e6-\U0001f1ff"
    "\U00002190-\U000021ff"
    "\U00002b00-\U00002bff"
    "\U0000fe00-\U0000fe0f"
    "\U0000200d"
    "]+"
)
# CJK ideographs, kana and hangul. Some open models drift into Chinese
# mid-sentence ("SPF五十"); no Vietnamese or English voice can read that.
_CJK_RE = re.compile("[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]+")
_SPACES_RE = re.compile(r"\s{2,}|[\t\r\n]")


MARKUP_START_RE = re.compile(r"[<{]")
"""First character of structured output (a tool call, JSON, XML). A spoken
reply never contains one, so everything from it onward is dropped."""

_NOT_SPEECH_RE = re.compile(r"[<>{}]|tool_?call|/function|/parameter", re.IGNORECASE)


def looks_like_speech(text: str) -> bool:
    """False for replies that are tool-call or JSON debris rather than talk."""
    return bool(text.strip()) and _NOT_SPEECH_RE.search(text) is None


def history_cue(cue: str) -> str:
    """Compact form of a cue for the chat history.

    Keeps any leading viewer comments and the first director line, and drops
    the product fact sheet that follows it: the next cue repeats the facts it
    needs, and history is resent on every request.
    """
    kept: list[str] = []
    for line in cue.splitlines():
        kept.append(line)
        if line.startswith(tuple(DIRECTOR_TAG.values())):
            break
    return "\n".join(kept)


def clean_spoken_chunk(text: str) -> str:
    """Strip characters a TTS voice would read aloud or choke on.

    Safe to apply to streamed chunks: it only removes single characters
    (emoji, markdown symbols) and never needs to see a whole sentence.
    """
    return _CJK_RE.sub("", _EMOJI_RE.sub("", _MARKUP_RE.sub("", text)))


def clean_spoken_text(text: str) -> str:
    """Full-string cleanup: also drops ``[TAG]`` echoes and tidies whitespace."""
    cleaned = clean_spoken_chunk(_BRACKET_TAG_RE.sub("", text))
    return _SPACES_RE.sub(" ", cleaned).strip()


def format_price(price: float | None, currency: str, language: str) -> str:
    """Human-readable price, e.g. ``199.000 đồng`` or ``$24.50``."""
    if price is None:
        return ""
    code = (currency or "").upper()
    if code == "VND":
        amount = f"{round(price):,}".replace(",", ".")
        return f"{amount} đồng" if _lang(language) == "vi" else f"{amount} VND"
    amount = f"{price:,.2f}".rstrip("0").rstrip(".")
    if code == "USD":
        return f"${amount}" if _lang(language) == "en" else f"{amount} đô la Mỹ"
    return f"{amount} {code}".strip()


# ------------------------------------------------------------ system prompt

_SYSTEM_VI = """Bạn là {name}, đang livestream bán hàng trực tiếp trên mạng xã hội.
Về bạn: {intro}
Tính cách: {personality}
Thông tin phiên live và cửa hàng: {notes}

Sản phẩm trong phiên live hôm nay:
{catalog}

Cách nói:
- Nói tiếng Việt tự nhiên, gần gũi như một streamer thật. Xưng "mình", gọi khán giả là "cả nhà" hoặc "mọi người". Chỉ dùng chữ Việt, tuyệt đối không dùng chữ Hán hay ngôn ngữ khác.
- Mỗi lượt chỉ nói 2 đến 4 câu ngắn, có cảm xúc, thỉnh thoảng hỏi lại khán giả. Không nói dài dòng.
- Văn nói thuần túy: không emoji, không ký hiệu, không gạch đầu dòng, không markdown. Giá tiền và con số viết thành chữ dễ đọc, ví dụ "một trăm chín mươi chín nghìn đồng".
- Không lặp lại nguyên văn câu đã nói. Mỗi lượt nối tiếp mạch chuyện của lượt trước.

Quy tắc:
- Tin nhắn bắt đầu bằng {director} là chỉ dẫn riêng dành cho bạn. Làm theo, nhưng tuyệt đối không đọc ra và không nhắc đến đạo diễn hay kịch bản.
- Tin nhắn bắt đầu bằng {comment} là bình luận của khán giả. Trả lời tự nhiên và gọi tên họ.
- Chỉ dùng thông tin sản phẩm được cung cấp. Không bịa giá, thành phần, khuyến mãi hay cam kết. Nếu không biết thì nói sẽ kiểm tra lại.
- Nếu khán giả hỏi thẳng bạn có phải AI không thì trả lời trung thực rằng bạn là người dẫn chương trình AI."""

_SYSTEM_EN = """You are {name}, hosting a live shopping stream on social media right now.
About you: {intro}
Personality: {personality}
About this stream and the shop: {notes}

Products in today's stream:
{catalog}

How you talk:
- Natural, warm spoken English, like a real streamer talking to viewers. English only, never switch to another language or script.
- Two to four short sentences per turn, with feeling. Ask viewers a question now and then. Never ramble.
- Pure speech: no emoji, symbols, bullet points or markdown. Say prices and numbers as words that read well aloud.
- Never repeat a line you already said. Each turn continues the flow of the previous one.

Rules:
- A message starting with {director} is a private stage direction for you. Follow it, but never read it aloud and never mention a director or a script.
- A message starting with {comment} is a viewer comment. Answer naturally and use their name.
- Use only the product facts you are given. Never invent prices, ingredients, promotions or guarantees. If you do not know, say you will check.
- If a viewer asks directly whether you are an AI, answer honestly that you are an AI host."""

_EMPTY = {"vi": "(chưa có)", "en": "(none yet)"}


def _catalog(products: Sequence[ProductFacts], language: str) -> str:
    if not products:
        return _EMPTY[_lang(language)]
    lines = []
    for index, product in enumerate(products, start=1):
        price = format_price(product.price, product.currency, language)
        lines.append(f"{index}. {product.name}" + (f" ({price})" if price else ""))
    return "\n".join(lines)


def build_system_prompt(profile: HostProfile, products: Sequence[ProductFacts]) -> str:
    lang = _lang(profile.language)
    template = _SYSTEM_VI if lang == "vi" else _SYSTEM_EN
    return template.format(
        name=profile.name,
        intro=profile.intro.strip() or _EMPTY[lang],
        personality=profile.personality.strip() or _EMPTY[lang],
        notes=profile.notes.strip() or _EMPTY[lang],
        catalog=_catalog(products, lang),
        director=DIRECTOR_TAG[lang],
        comment=COMMENT_TAG[lang],
    )


# ---------------------------------------------------------------------- cues


def _comments_block(comments: Sequence[ViewerComment], language: str) -> str:
    tag = COMMENT_TAG[_lang(language)]
    return "\n".join(f"{tag} {c.name}: {c.text}" for c in comments)


def _with_comments(cue: str, comments: Sequence[ViewerComment], language: str, hint: str) -> str:
    if not comments:
        return cue
    return f"{_comments_block(comments, language)}\n{cue} {hint}"


def opening_cue(language: str) -> str:
    if _lang(language) == "vi":
        return (
            f"{DIRECTOR_TAG['vi']} Phiên live vừa bắt đầu. Chào khán giả thật tự nhiên, "
            "giới thiệu ngắn gọn bạn là ai và hôm nay sẽ có gì. 2 đến 3 câu."
        )
    return (
        f"{DIRECTOR_TAG['en']} The stream just went live. Greet viewers naturally, say "
        "briefly who you are and what today's stream is about. Two or three sentences."
    )


_CHAT_TOPICS_VI = (
    "Hỏi thăm khán giả đang xem từ đâu và hôm nay của họ thế nào.",
    "Kể một chuyện nhỏ, vui trong ngày hôm nay của bạn.",
    "Hé lộ nhẹ nhàng những sản phẩm sắp giới thiệu để khán giả tò mò, chưa nói chi tiết hay giá.",
    "Chia sẻ một mẹo nhỏ hữu ích liên quan đến các mặt hàng của cửa hàng.",
    "Cảm ơn những người đang xem, nhắc mọi người bấm theo dõi và chia sẻ phiên live.",
    "Hỏi khán giả một câu vui để mọi người bình luận, ví dụ sở thích hoặc thói quen mua sắm.",
    "Kể vì sao bạn thích công việc livestream và gặp gỡ mọi người.",
    "Nhắc khán giả cứ bình luận câu hỏi, bạn sẽ đọc và trả lời ngay.",
)
_CHAT_TOPICS_EN = (
    "Ask viewers where they are watching from and how their day is going.",
    "Share a small, fun story from your day.",
    "Tease the products coming up so viewers get curious, without details or prices.",
    "Share one handy tip related to what the shop sells.",
    "Thank the people watching and remind them to follow and share the stream.",
    "Ask viewers a fun question they can answer in the comments.",
    "Say why you love going live and meeting people.",
    "Remind viewers to drop questions in the comments and you will answer right away.",
)


def chat_topic_count(language: str) -> int:
    return len(_CHAT_TOPICS_VI if _lang(language) == "vi" else _CHAT_TOPICS_EN)


def chat_cue(language: str, topic_index: int, comments: Sequence[ViewerComment]) -> str:
    """Free-talk cue. With comments pending, answering them is the whole turn."""
    lang = _lang(language)
    tag = DIRECTOR_TAG[lang]
    if comments:
        ask = (
            f"{tag} Trả lời các bình luận trên một cách tự nhiên, gọi tên người hỏi. 2 đến 3 câu."
            if lang == "vi"
            else f"{tag} Answer the comments above naturally, using the viewers' names. "
            "Two or three sentences."
        )
        return f"{_comments_block(comments, lang)}\n{ask}"
    topics = _CHAT_TOPICS_VI if lang == "vi" else _CHAT_TOPICS_EN
    topic = topics[topic_index % len(topics)]
    if lang == "vi":
        return f"{tag} Trò chuyện tự do với khán giả. Gợi ý: {topic} 2 đến 3 câu."
    return f"{tag} Chat freely with viewers. Idea: {topic} Two or three sentences."


def say_cue(language: str, text: str) -> str:
    if _lang(language) == "vi":
        return (
            f"{DIRECTOR_TAG['vi']} Hãy nói với khán giả nội dung sau bằng lời của bạn, "
            f"tự nhiên, 1 đến 3 câu: {text}"
        )
    return (
        f"{DIRECTOR_TAG['en']} Tell viewers the following in your own words, naturally, "
        f"in one to three sentences: {text}"
    )


def _facts_block(product: ProductFacts, language: str) -> str:
    lang = _lang(language)
    price = format_price(product.price, product.currency, lang)
    labels = (
        ("Tên", "Giá", "Mô tả", "Điểm nổi bật", "Gợi ý kịch bản")
        if lang == "vi"
        else ("Name", "Price", "Description", "Highlights", "Talking points")
    )
    values = (product.name, price, product.description, product.highlights, product.script)
    return "\n".join(
        f"- {label}: {value.strip()}"
        for label, value in zip(labels, values, strict=True)
        if value and value.strip()
    )


_BEATS_VI = (
    "Phần 1 trên 3: dẫn dắt tự nhiên từ câu chuyện đang nói sang sản phẩm này, cho biết nó là gì "
    "và vì sao bạn thích. Chưa nói giá. 2 đến 3 câu.",
    "Phần 2 trên 3: nói kỹ hơn hai đến ba điểm nổi bật, cách dùng hoặc ai hợp với sản phẩm. "
    "Chưa nói giá. 3 đến 4 câu.",
    "Phần 3 trên 3: công bố giá bằng chữ, nhấn vào giá trị nhận được và kêu gọi khán giả "
    "chốt đơn ngay. 2 đến 3 câu.",
)
_BEATS_EN = (
    "Part 1 of 3: move naturally from what you were saying into this product, say what it is "
    "and why you like it. Do not mention the price yet. Two or three sentences.",
    "Part 2 of 3: go deeper on two or three highlights, how to use it or who it suits. "
    "Still no price. Three or four sentences.",
    "Part 3 of 3: reveal the price in words, stress the value and ask viewers to order now. "
    "Two or three sentences.",
)


def product_cue(
    language: str,
    product: ProductFacts,
    *,
    beat: int,
    index: int,
    total: int,
    comments: Sequence[ViewerComment],
) -> str:
    """Cue for one spoken turn of a product presentation.

    The facts are repeated on every beat because the chat history is
    truncated aggressively to stay inside small token-per-minute limits.
    """
    lang = _lang(language)
    tag = DIRECTOR_TAG[lang]
    beats = _BEATS_VI if lang == "vi" else _BEATS_EN
    step = beats[min(max(beat, 0), len(beats) - 1)]
    facts = _facts_block(product, lang)
    if lang == "vi":
        cue = (
            f"{tag} Giới thiệu sản phẩm {index} trên {total}.\n{facts}\n{step} "
            "Diễn đạt bằng lời của bạn, không đọc nguyên văn gợi ý."
        )
        hint = "Nếu bình luận trên liên quan thì trả lời ngắn gọn trong lượt này."
    else:
        cue = (
            f"{tag} Present product {index} of {total}.\n{facts}\n{step} "
            "Use your own words, do not read the talking points verbatim."
        )
        hint = "If the comments above are relevant, answer them briefly in this turn."
    return _with_comments(cue, comments, lang, hint)


def products_done_cue(language: str) -> str:
    if _lang(language) == "vi":
        return (
            f"{DIRECTOR_TAG['vi']} Bạn vừa giới thiệu xong tất cả sản phẩm. Cảm ơn khán giả, "
            "nhắc ai muốn xem lại sản phẩm nào thì bình luận, rồi tiếp tục trò chuyện. 2 đến 3 câu."
        )
    return (
        f"{DIRECTOR_TAG['en']} You just finished presenting every product. Thank viewers, "
        "invite them to comment if they want to see one again, then keep chatting. "
        "Two or three sentences."
    )


def goodbye_cue(language: str) -> str:
    if _lang(language) == "vi":
        return (
            f"{DIRECTOR_TAG['vi']} Phiên live sắp kết thúc. Cảm ơn và chào tạm biệt khán giả "
            "thật ấm áp. 1 đến 2 câu."
        )
    return (
        f"{DIRECTOR_TAG['en']} The stream is ending. Thank viewers and say a warm goodbye. "
        "One or two sentences."
    )
