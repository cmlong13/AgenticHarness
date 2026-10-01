"""Extract a subagent's last assistant text block verbatim from its task output transcript."""
import json
import sys

src, dst = sys.argv[1], sys.argv[2]
last = None
for line in open(src, encoding="utf-8"):
    try:
        d = json.loads(line)
    except ValueError:
        continue
    m = d.get("message", {})
    if d.get("type") == "assistant" and isinstance(m.get("content"), list):
        for c in m["content"]:
            if c.get("type") == "text":
                last = c["text"]
with open(dst, "w", encoding="utf-8", newline="") as fh:
    fh.write(last)
print(repr(last[:60]), repr(last[-30:]), "has &gt;:", "&gt;" in last, "has ->:", "->" in last)
