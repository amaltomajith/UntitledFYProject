"""String helpers."""
import re


def slugify(text):
    """Lowercase, trim, and join words with single hyphens."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(words)


def truncate(text, limit, suffix="..."):
    """Cut text to at most `limit` characters, including the suffix."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def word_count(text):
    """Number of whitespace-separated words."""
    return len(text.split())


def is_palindrome(text):
    """True if text reads the same backwards, ignoring case and non-alphanumerics."""
    cleaned = [c.lower() for c in text if c.isalnum()]
    return cleaned == cleaned[::-1]


def title_case(text, minor=("a", "an", "the", "of", "and")):
    """Capitalise words except minor words; the first word is always capitalised."""
    words = text.split()
    out = []
    for i, w in enumerate(words):
        if w.lower() in minor:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)
