from textutils import is_palindrome, slugify, title_case, truncate, word_count


def test_slugify_digits():
    assert slugify("Already-slugged text 42") == "already-slugged-text-42"


def test_truncate_edges():
    assert truncate("abcdef", 6) == "abcdef"
    assert truncate("abcdefg", 3) == "..."


def test_word_count_empty():
    assert word_count("") == 0


def test_is_palindrome_quotes():
    assert is_palindrome("No 'x' in Nixon")


def test_title_case_other():
    assert title_case("a tale of two cities") == "A Tale of Two Cities"
