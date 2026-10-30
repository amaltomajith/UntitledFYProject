from textutils import is_palindrome, slugify, title_case, truncate, word_count


def test_slugify():
    assert slugify("  Hello, World!  ") == "hello-world"


def test_truncate():
    assert truncate("abcdefghij", 6) == "abc..."
    assert truncate("abc", 6) == "abc"
    assert truncate("hello", 5) == "hello"


def test_word_count():
    assert word_count("one  two\nthree") == 3


def test_is_palindrome():
    assert is_palindrome("A man, a plan, a canal: Panama")
    assert not is_palindrome("abc")


def test_title_case():
    assert title_case("the lord of the rings") == "The Lord of the Rings"
