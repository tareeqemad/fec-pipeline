"""Open a page as plain text: python fetch.py URL [grep-regex] -> visible text (or only lines matching the regex, with context)."""
import html, re, subprocess, sys
t = subprocess.run(["curl", "-sL", "-m", "30", "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
                    "-H", "Accept-Language: en-US,en;q=0.9", sys.argv[1]], capture_output=True, text=True, errors="replace").stdout
t = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", t)
t = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d|address)>", "\n", t)
lines = [re.sub(r"[ \t]+", " ", html.unescape(re.sub("<[^>]+>", " ", l))).strip() for l in t.split("\n")]
lines = [l for l in lines if l]
if len(sys.argv) > 2:
    rx = re.compile(sys.argv[2], re.I)
    for i, l in enumerate(lines):
        if rx.search(l): print(" / ".join(lines[max(0, i - 2): i + 3])[:600])
else:
    print("\n".join(lines)[:15000])
