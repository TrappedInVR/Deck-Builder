"""Shared helpers: card loading and deck-file parsing."""
import json, re
from tags import tag

def load_cards(path="data/cards.json"):
    """Return {lowercase name: card}. Double-faced cards are also indexed by front face."""
    with open(path, encoding="utf-8") as f:
        cards = json.load(f)
    idx = {}
    for c in cards:
        if "_reminder_stripped" not in c:
            # reminder text "(It's an artifact with ... Add one mana ...)" would fool the rules, so drop it
            c["text"] = re.sub(r" ?\([^()]*\)", "", c.get("text") or "").strip()
            c["_reminder_stripped"] = True
        c["tags"] = tag(c)                    # re-tag on load: rule fixes apply even to yesterday's cached download
        idx[c["name"].lower()] = c
        if " // " in c["name"]:
            idx.setdefault(c["name"].split(" // ")[0].lower(), c)
    return idx

def parse_deck(path):
    """Parse 'N Card Name' lines (Moxfield/Archidekt style). Returns [(count, name)]."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(("#", "//")):
                continue
            m = re.match(r"^(\d+)x?\s+(.+?)(?:\s+\([A-Za-z0-9]+\).*)?$", line)
            if m:
                out.append((int(m.group(1)), m.group(2).strip()))
    return out

def pips(mana_cost):
    return {c: len(re.findall(r"\{[^}]*%s[^}]*\}" % c, mana_cost or "")) for c in "WUBRG"}
