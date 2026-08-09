"""
Punctuation restoration via FunASR ct-punc, followed by Chinese→English
punctuation mapping and sentence-initial capitalisation.

Works per-segment to avoid alignment issues that come from concatenating
and then redistributing text.
"""

import re
import string

from config import PUNCTUATION_MAP, SENTENCE_TERMINATORS

# Lazy-loaded FunASR model
_model = None


def _get_model():
    """Return a module-level singleton FunASR ct-punc model."""
    global _model
    if _model is None:
        from funasr import AutoModel
        _model = AutoModel(model="ct-punc")
    return _model


def _map_punctuation(text: str) -> str:
    """Replace every Chinese punctuation character with its English equivalent."""
    mapped: list[str] = []
    for ch in text:
        mapped.append(PUNCTUATION_MAP.get(ch, ch))
    return "".join(mapped)


def _capitalize_sentences(text: str) -> str:
    """
    Capitalize the first letter after each sentence terminator (., ?, !).
    Also capitalizes the very first character.
    """
    result: list[str] = []
    cap_next = True

    for ch in text:
        if cap_next and ch.isalpha():
            result.append(ch.upper())
            cap_next = False
        else:
            result.append(ch)

        if ch in SENTENCE_TERMINATORS:
            cap_next = True

    return "".join(result)


def _restore_single(text: str) -> str:
    """Punctuate a single text string via FunASR."""
    if not text or not text.strip():
        return text

    model = _get_model()
    result = model.generate(input=text)

    # result is typically [{"text": "punctuated text"}]
    if isinstance(result, list) and len(result) > 0 and "text" in result[0]:
        return result[0]["text"]
    if isinstance(result, str):
        return result
    # Fallback: return original text unchanged
    return text


def restore_punctuation(segments: list[dict]) -> list[dict]:
    """
    Run FunASR ct-punc **per segment**, then apply Chinese→English
    punctuation mapping and sentence-initial capitalisation.

    Parameters
    ----------
    segments : list[dict]
        ``[{start, end, text, speaker, ...}, ...]``

    Returns
    -------
    list[dict]
        Same structure with ``text`` updated to include punctuation,
        and multi-sentence segments split into individual sentence entries.
    """
    for seg in segments:
        if not seg["text"].strip():
            continue
        # 1. Restore punctuation
        punctuated = _restore_single(seg["text"])
        # 2. Chinese → English mapping
        punctuated = _map_punctuation(punctuated)
        # 3. Capitalise sentences
        punctuated = _capitalize_sentences(punctuated)
        seg["text"] = punctuated.strip()

    # 4. Split merged segments into individual sentences
    segments = split_sentences(segments)

    # 5. Filter hallucinated / low-quality segments
    segments = filter_segments(segments)

    return segments


# ---------------------------------------------------------------------------
# Sentence splitting
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s*")

# Punctuation-only characters (English + common Chinese codepoints)
_PUNCT_ONLY = set(
    string.punctuation
    + "　、。，．《》"
    + "“”‘’（）【】"
)


def _is_pure_punctuation(token: str) -> bool:
    """Return True if *token* contains no letters, digits, or CJK characters."""
    if not token:
        return True
    for ch in token:
        if ch.isalpha() or ch.isdigit():
            return False
        if "一" <= ch <= "鿿" or "぀" <= ch <= "ヿ":
            return False
    return True


def _split_into_sentences(text: str) -> list[str]:
    """Split *text* into individual sentences on ``.`` ``?`` ``!`` boundaries.

    Uses ``\\s*`` so that both ``"Hello. World"`` and ``"你好.今天"``
    (Chinese text with no inter-word space after the mapped period) split
    correctly.
    """
    if not text:
        return []

    parts = re.split(_SENTENCE_SPLIT_RE, text)
    result = [p.strip() for p in parts if p.strip()]

    # If no sentence terminators were found, return as a single sentence
    return result if result else [text.strip()] if text.strip() else []


def _split_proportional(seg: dict, sentences: list[str]) -> list[dict]:
    """Distribute a segment's time range across sentences proportional to
    character count (excluding whitespace and punctuation).
    """
    if not sentences:
        return [seg]

    if len(sentences) == 1:
        return [{
            "start": seg["start"],
            "end": seg["end"],
            "text": sentences[0],
            "speaker": seg.get("speaker", "SPEAKER_UNKNOWN"),
            "words": seg.get("words", []),
            "no_speech_prob": seg.get("no_speech_prob", 0.0),
            "avg_logprob": seg.get("avg_logprob", 0.0),
        }]

    # Count content characters per sentence
    lengths = []
    for s in sentences:
        count = sum(1 for ch in s if not ch.isspace() and ch not in _PUNCT_ONLY)
        lengths.append(max(count, 1))

    total_len = sum(lengths)
    duration = seg["end"] - seg["start"]

    results = []
    current_time = seg["start"]

    for i, sentence in enumerate(sentences):
        proportion = lengths[i] / total_len
        seg_duration = duration * proportion

        if i == len(sentences) - 1:
            seg_end = seg["end"]
        else:
            seg_end = current_time + seg_duration

        results.append({
            "start": round(current_time, 3),
            "end": round(seg_end, 3),
            "text": sentence,
            "speaker": seg.get("speaker", "SPEAKER_UNKNOWN"),
            "words": [],
            "no_speech_prob": seg.get("no_speech_prob", 0.0),
            "avg_logprob": seg.get("avg_logprob", 0.0),
        })
        current_time = seg_end

    return results


def _split_by_words(seg: dict, sentences: list[str]) -> list[dict]:
    """Split a segment into sentences using word-level timestamps.

    Counts content tokens per sentence and allocates words sequentially.
    Falls back to ``_split_proportional`` when token/word counts diverge
    by more than 2× (common with CJK text where there are no inter-word
    spaces).
    """
    words = seg.get("words", [])
    if not words or not sentences:
        return _split_proportional(seg, sentences)

    content_counts = []
    for sentence in sentences:
        token_count = 0
        for token in sentence.split():
            if not _is_pure_punctuation(token):
                token_count += 1
        content_counts.append(token_count)

    total_content = sum(content_counts)
    total_words = len(words)

    if total_content == 0 or total_words == 0:
        return _split_proportional(seg, sentences)

    # Sanity check: diverging counts → proportional fallback
    if total_content / total_words > 2.0 or total_words / total_content > 2.0:
        return _split_proportional(seg, sentences)

    results = []
    word_idx = 0

    for i, sentence in enumerate(sentences):
        needed = max(content_counts[i], 1)
        is_last = (i == len(sentences) - 1)

        if is_last:
            allocated = words[word_idx:]
        elif word_idx + needed > total_words:
            allocated = words[word_idx:] if word_idx < total_words else []
        else:
            allocated = words[word_idx:word_idx + needed]

        if not allocated:
            sub_seg = {
                "start": seg["start"],
                "end": seg["end"],
                "text": " ".join(sentences[i:]),
                "speaker": seg.get("speaker", "SPEAKER_UNKNOWN"),
                "words": [],
                "no_speech_prob": seg.get("no_speech_prob", 0.0),
                "avg_logprob": seg.get("avg_logprob", 0.0),
            }
            results.extend(_split_proportional(sub_seg, sentences[i:]))
            break

        results.append({
            "start": round(allocated[0]["start"], 3),
            "end": round(allocated[-1]["end"], 3),
            "text": sentence,
            "speaker": seg.get("speaker", "SPEAKER_UNKNOWN"),
            "words": allocated,
            "no_speech_prob": seg.get("no_speech_prob", 0.0),
            "avg_logprob": seg.get("avg_logprob", 0.0),
        })
        word_idx += len(allocated)

    return results


def _split_by_gaps(seg: dict, gap_threshold: float = 0.5) -> list[dict]:
    """Split a single segment at word-level silence gaps > *gap_threshold*.

    When the speaker pauses noticeably (> 0.5 s) between phrases that
    Whisper reported as one continuous segment, this produces separate
    entries with accurate per-phrase timestamps.

    Parameters
    ----------
    seg : dict
        ``{start, end, text, speaker, words, no_speech_prob, avg_logprob}``.
    gap_threshold : float
        Minimum inter-word silence (seconds) that triggers a split.

    Returns
    -------
    list[dict]
        One or more sub-segments.  Returns ``[seg]`` unchanged when no gaps
        exceed the threshold.
    """
    words = seg.get("words", [])
    if not words:
        return [seg]

    # Find split points — indices *after* which a gap exceeds the threshold
    split_after: list[int] = []
    for i in range(len(words) - 1):
        gap = words[i + 1]["start"] - words[i]["end"]
        if gap > gap_threshold:
            split_after.append(i)

    if not split_after:
        return [seg]

    # Build sub-segments
    results: list[dict] = []
    start_idx = 0

    for cut_idx in split_after:
        chunk_words = words[start_idx : cut_idx + 1]
        # Reconstruct text by stripping per-word whitespace and joining
        # without a separator — Whisper tokens for CJK are single characters,
        # and injecting spaces between them produces "穿 上 喽" instead of
        # "穿上喽".
        chunk_text = "".join(w["word"].strip() for w in chunk_words)
        if chunk_text:
            results.append({
                "start": round(chunk_words[0]["start"], 3),
                "end": round(chunk_words[-1]["end"], 3),
                "text": chunk_text,
                "speaker": seg.get("speaker", "SPEAKER_UNKNOWN"),
                "words": chunk_words,
                "no_speech_prob": seg.get("no_speech_prob", 0.0),
                "avg_logprob": seg.get("avg_logprob", 0.0),
            })
        start_idx = cut_idx + 1

    # Final chunk (after the last gap)
    final_words = words[start_idx:]
    final_text = "".join(w["word"].strip() for w in final_words)
    if final_text:
        results.append({
            "start": round(final_words[0]["start"], 3),
            "end": round(final_words[-1]["end"], 3),
            "text": final_text,
            "speaker": seg.get("speaker", "SPEAKER_UNKNOWN"),
            "words": final_words,
            "no_speech_prob": seg.get("no_speech_prob", 0.0),
            "avg_logprob": seg.get("avg_logprob", 0.0),
        })

    return results if results else [seg]


# ---------------------------------------------------------------------------
# Chinese word segmentation (jieba)
# ---------------------------------------------------------------------------

# Punctuation characters that should be isolated as separate tokens.
_TOKENIZE_PUNCT = r"。，、；：！？．.？!?,;:""''（）()【】《》…—"


def tokenize_text(text: str) -> str:
    """Segment Chinese text into words separated by spaces, with punctuation
    characters isolated as individual tokens.

    English words and digits pass through ``jieba`` un-split (jieba recognises
    them as single tokens).  The result has exactly one space between every
    pair of adjacent tokens.

    Parameters
    ----------
    text : str
        Raw text, possibly mixed Chinese / English / punctuation.

    Returns
    -------
    str
        Tokenised text, e.g. ``"今天 天气 真好 ， 我们 一起 去 公园 吧 。"``

    Examples
    --------
    >>> tokenize_text("今天天气真好，我们一起去公园吧。")
    '今天 天气 真好 ， 我们 一起 去 公园 吧 。'

    >>> tokenize_text("Hello,世界!")
    'Hello , 世界 !'
    """
    import jieba

    if not text or not text.strip():
        return text

    # Split on punctuation, keeping the punctuation as separate elements
    pattern = rf"([{re.escape(_TOKENIZE_PUNCT)}])"
    parts = re.split(pattern, text)

    tokens: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Punctuation character → keep as a standalone token
        if re.fullmatch(pattern, part):
            tokens.append(part)
        else:
            # Content segment → jieba word segmentation
            tokens.extend(jieba.lcut(part, cut_all=False))

    return " ".join(tokens)


def split_sentences(segments: list[dict]) -> list[dict]:
    """Split each segment into individual sentences with per-sentence timestamps.

    Uses word-level timestamps (from Whisper) when available, falling back
    to proportional time distribution otherwise.

    Even when a segment contains no sentence-boundary punctuation, it is still
    checked for word-level time gaps > 0.5 s — a long silence *within* a
    segment is treated as an implicit boundary.

    Parameters
    ----------
    segments : list[dict]
        ``[{start, end, text, speaker, words}, ...]``

    Returns
    -------
    list[dict]
        Same structure, with multi-sentence segments split into single-sentence
        entries.  Each entry has ``speaker``, ``start``, ``end``, ``text``,
        and ``words``.
    """
    if not segments:
        return []

    result = []

    for seg in segments:
        text = seg.get("text", "")
        if not text.strip():
            result.append(seg)
            continue

        sentences = _split_into_sentences(text)

        if len(sentences) > 1:
            # Multiple sentences detected via punctuation — split by words
            words = seg.get("words", [])
            if words:
                result.extend(_split_by_words(seg, sentences))
            else:
                result.extend(_split_proportional(seg, sentences))
        else:
            # Single sentence — still check for time gaps within words
            words = seg.get("words", [])
            if words:
                gap_parts = _split_by_gaps(seg, gap_threshold=0.5)
                result.extend(gap_parts)
            else:
                result.append(seg)

    # Apply Chinese word segmentation to every segment's text
    for seg in result:
        seg["text"] = tokenize_text(seg.get("text", ""))

    return result


# ---------------------------------------------------------------------------
# Post-processing quality filters
# ---------------------------------------------------------------------------

def filter_segments(segments: list[dict]) -> list[dict]:
    """
    Remove segments that are almost certainly noise / hallucination artefacts.

    Filters applied (each independently):

    1. **Text too short** — fewer than 2 content characters (Whisper often
       produces single-syllable gibberish for background sounds).
    2. **High ``no_speech_prob``** — value > 0.9 means Silero VAD was very
       confident this audio segment is NOT speech.
    3. **Low confidence** — ``avg_logprob < -2.5``.  Whisper's per-token log
       probability averaged over the segment; values below this threshold
       correlate strongly with hallucinations.

    Parameters
    ----------
    segments : list[dict]
        ``[{start, end, text, speaker, no_speech_prob, avg_logprob, ...}, ...]``

    Returns
    -------
    list[dict]
        Filtered segment list.
    """
    kept = []
    for seg in segments:
        text = seg.get("text", "").strip()

        # 1. Drop very short text (single character or empty)
        content_chars = [ch for ch in text if not ch.isspace() and not ch in (
            "。", "，", "？", "！", "；", "：", "、", "．",
            ".", ",", "?", "!", ";", ":",
        )]
        if len(content_chars) < 2:
            continue

        # 2. Drop segments where Silero VAD says it's not speech
        no_speech = seg.get("no_speech_prob", 0.0)
        if no_speech > 0.9:
            continue

        # 3. Drop very low-confidence segments
        avg_logprob = seg.get("avg_logprob", 0.0)
        if avg_logprob < -2.5:
            continue

        kept.append(seg)

    return kept
