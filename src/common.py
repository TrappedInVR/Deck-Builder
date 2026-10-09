"""Shared helpers: card loading and deck-file parsing."""
import json, re
from tags import tag

# Cards that are never part of the 100-card main deck. Each has its own deck/zone or lives outside the game:
#   Attraction (Attraction deck), Contraption (Contraption deck), Stickers (sticker sheets, outside the game),
#   Plane/Phenomenon (planar deck), Scheme (Archenemy), Vanguard, Dungeon (outside the game), Conspiracy (draft only),
#   Bounty (bounty deck), Emblem/token/art/helper cards (type "Card": Monarch, Initiative, Day/Night...).
NOT_MAIN_DECK = re.compile(r"\b(?:Attraction|Contraption|Stickers?|Plane|Phenomenon|Scheme|Vanguard|Dungeon|Conspiracy|Emblem|Bounty)\b|^Card\b")
# Cards that sit in the 99 but only work with something outside it (Commander has no sideboard), or only in a draft,
# or for ante, or only on digital clients:
NEEDS_EXTRA = re.compile(
    r"\bAttractions?\b|\bContraptions?\b|\bassemble\b|\bstickers?\b|\broll to visit\b|\{TK\}|\btickets?\b"   # Unfinity extras
    r"|from outside the game(?![^.]*\bor discard)"                                         # Wishes (Learn still works: it can rummage)
    r"|\bdraft(?:ed|ing)?\b|\bplaying for ante\b|\bante\b"                              # draft-matters, ante
    r"|\bperpetually\b|\bseek\b|\bconjure\b|\bspecialize\b|\bspellbook\b", re.I)          # digital-only (Alchemy)
NOT_MAIN_LAYOUTS = ("token", "double_faced_token", "emblem", "art_series", "sticker", "planar", "scheme", "vanguard", "augment", "host")


def main_deck_card(c):
    """False for cards that can't count toward (or don't work in) a normal 100-card Commander deck."""
    text = re.sub(r" ?\([^()]*\)", "", c.get("text") or "")          # ignore reminder text (companions' "outside the game")
    for n in sorted({c["name"], c["name"].split(" // ")[0], c["name"].split(",")[0]}, key=len, reverse=True):
        text = text.replace(n, "~")                                     # ignore its own name ("Kongming's Contraptions")
    return not NOT_MAIN_DECK.search(c.get("type_line") or "") and not NEEDS_EXTRA.search(text) \
        and c.get("layout") not in NOT_MAIN_LAYOUTS


def load_cards(path="data/cards.json"):
    """Return {lowercase name: card}. Double-faced cards are also indexed by front face."""
    with open(path, encoding="utf-8") as f:
        cards = json.load(f)
    idx = {}
    for c in cards:
        if not main_deck_card(c):
            continue                          # Attractions, sticker sheets & co. never go in the 99
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
