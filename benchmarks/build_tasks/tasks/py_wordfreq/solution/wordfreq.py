import argparse, collections, re, sys

def top_words(text, n):
    counts = collections.Counter(re.findall(r"[a-z']+", text.lower()))
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("file")
    p.add_argument("--top", type=int, default=3)
    a = p.parse_args()
    try:
        text = open(a.file, encoding="utf-8").read()
    except OSError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    for word, count in top_words(text, a.top):
        print(word, count)
