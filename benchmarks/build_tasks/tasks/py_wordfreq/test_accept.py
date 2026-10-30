import subprocess, sys
from wordfreq import top_words

def run(*args):
    return subprocess.run([sys.executable, "wordfreq.py", *args], capture_output=True, text=True)

def test_counts_and_ties():
    assert top_words("b a B a c", 2) == [("a", 2), ("b", 2)]
    assert top_words("Don't stop, don't!", 1) == [("don't", 2)]

def test_more_than_exists():
    assert top_words("one", 5) == [("one", 1)]
    assert top_words("", 3) == []

def test_cli(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("x y x z x y")
    assert run(str(f), "--top", "2").stdout.split("\n")[:2] == ["x 3", "y 2"]
    assert len(run(str(f)).stdout.strip().split("\n")) == 3

def test_missing_file():
    r = run("nope.txt")
    assert r.returncode == 2 and r.stderr.strip() and not r.stdout.strip()
