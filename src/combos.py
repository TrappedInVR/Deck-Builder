"""Combos from Commander Spellbook (data/combos.json, made by fetch_combos.py).

Each combo carries Commander Spellbook's own bracket tag, computed from how many cards it needs, how fast it can
go off (mana needed), and what it does:
  E Exhibition (Bracket 1)  C Core (B2)  O Oddball (B2)  P Powerful (B3)  S Spicy (B3)  R Ruthless (B4)  B Banned
Bracket 3 forbids early-game two-card infinite combos -> Ruthless (R) and Banned (B) combos are never allowed.
Each vibe sets the highest tag it accepts and how many combos it actively builds in (options.py)."""
import json
import math
import os

TAG_RANK = {"E": 1, "C": 2, "O": 3, "P": 4, "S": 5, "R": 6, "B": 7}
TAG_NAMES = {"E": "Exhibition (Bracket 1 style)", "C": "Core (Bracket 2 style)", "O": "Oddball (Bracket 2 style)",
             "P": "Powerful (Bracket 3)", "S": "Spicy (Bracket 3)", "R": "Ruthless (Bracket 4: early two-card combo)",
             "B": "uses a banned card"}
CREDIT = "Combo data from Commander Spellbook (commanderspellbook.com)"


class ComboDB:
    def __init__(self, combos, timestamp=None):
        self.combos = combos
        self.timestamp = timestamp
        self.by_card = {}
        for i, c in enumerate(combos):
            c["_cards"] = [n.lower() for n in c["cards"]]
            for n in set(c["_cards"]):
                self.by_card.setdefault(n, []).append(i)
                if " // " in n:
                    self.by_card.setdefault(n.split(" // ")[0], []).append(i)

    def __len__(self):
        return len(self.combos)

    def _names(self, names):
        out = set()
        for n in names:
            n = n.lower()
            out.add(n)
            if " // " in n:
                out.add(n.split(" // ")[0])
        return out

    def _has(self, combo, have):
        return all(n in have or n.split(" // ")[0] in have for n in combo["_cards"])

    def complete(self, names):
        """Combos whose every card is in `names` (cards only; generic 'requires' templates are reported, not checked)."""
        have = self._names(names)
        seen, out = set(), []
        for n in have:
            for i in self.by_card.get(n, ()):
                if i in seen:
                    continue
                seen.add(i)
                if self._has(self.combos[i], have):
                    out.append(self.combos[i])
        return out

    def near(self, names, allowed, limit=6):
        """Combos missing exactly one card, where that card is in `allowed` (a set of lowercase names you could add)."""
        have = self._names(names)
        seen, out = set(), []
        for n in have:
            for i in self.by_card.get(n, ()):
                if i in seen:
                    continue
                seen.add(i)
                c = self.combos[i]
                missing = [x for x in c["_cards"] if x not in have and x.split(" // ")[0] not in have]
                if len(missing) == 1 and missing[0] in allowed:
                    out.append((c, missing[0]))
        out.sort(key=lambda r: -(r[0].get("pop") or 0))
        return out[:limit]

    def involving(self, name):
        return [self.combos[i] for i in self.by_card.get(name.lower(), ())]


def load(path="data/combos.json"):
    """Return a ComboDB, or None if the file is missing/unreadable (the builder then works without combos)."""
    if not os.path.exists(path):
        return None
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    combos = doc.get("combos") if isinstance(doc, dict) else doc
    return ComboDB(combos or [], doc.get("timestamp") if isinstance(doc, dict) else None)


def allowed(combo, max_tag):
    """Allowed under this vibe's ceiling AND always under Bracket 3 (never R or B)."""
    t = combo.get("tag") or "R"
    return TAG_RANK.get(t, 6) <= min(TAG_RANK[max_tag], TAG_RANK["S"])


def popularity_bonus(combo):
    return min(12.0, 3 * math.log10((combo.get("pop") or 0) + 1))


def describe(combo):
    res = ", ".join(combo.get("results") or []) or "a combo"
    extra = f" (also needs: {', '.join(combo['templates'])})" if combo.get("templates") else ""
    return f"{' + '.join(combo['cards'])} -> {res}{extra}"


def link(combo):
    return f"https://commanderspellbook.com/combo/{combo['id']}/" if combo.get("id") else ""
