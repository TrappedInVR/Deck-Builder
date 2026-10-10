"""Counting conditions on the commander: WHICH of your cards actually count for it.

Many commanders don't just want "creatures", they want creatures with a condition:
  Arabella, Abandoned Doll   "X is the number of creatures you control with POWER 2 OR LESS"
  Arcades, the Strategist     "Whenever a creature WITH DEFENDER enters..."
  Zur the Enchanter           "search your library for an enchantment card with MANA VALUE 3 OR LESS"
  Ghalta-style payoffs        "creature with POWER 4 OR GREATER"

This module reads those conditions and then judges every card against them:
  meets     the card itself counts (a 1/1 for Arabella, a Wall for Arcades)
  makes     it creates tokens that count (Secure the Wastes' 1/1s for Arabella)
  breaks    it permanently pushes your cards OUT of the condition (an anthem or +1/+1 counters on a
            "power 2 or less" commander); temporary pumps ("until end of turn") are fine
  helps     it pushes your cards INTO a "power N or greater" condition (anthems for a big-creature commander)
  misses    a card of the right kind that doesn't count (a 5-power creature for Arabella): a small penalty, so it
            only makes the deck if it's much better than the alternatives
Only conditions about YOUR cards count ("you control", "under your control", "your library"...): "destroy target
creature with power 3 or less" is removal, not a deck-building condition."""
import re

from analyze import num

KEYWORDS = ("flying", "defender", "deathtouch", "lifelink", "trample", "haste", "first strike", "double strike", "vigilance",
            "menace", "reach", "hexproof", "indestructible", "flash", "ward", "toxic", "infect", "prowess")
_STAT = re.compile(r"\b(creatures?|artifacts?|enchantments?|permanents?|cards?|spells?|tokens?)\b([^.;]{0,30}?)\bwith "
                   r"(power|toughness|mana value|converted mana cost) (\d+|x) or (less|greater|more)", re.I)
_KW = re.compile(r"\b(creatures?|tokens?)\b((?: you control)?) with (%s)\b" % "|".join(KEYWORDS), re.I)
_MINE = re.compile(r"\byou control\b|\bunder your control\b|\byour (?:library|graveyard|hand)\b|\bsearch your\b|\byou cast\b", re.I)
_THEIRS = re.compile(r"\b(?:an opponent controls|you don't control|opponents control|target opponent|target player)\b", re.I)
_COUNTED = re.compile(r"\bnumber of\b|\bfor each\b|\bx is\b|\bequal to\b", re.I)
_TOKEN = re.compile(r"\bcreate[s]? (?:a|an|one|two|three|four|five|x|that many|\w+)? ?(?:tapped )?(?:[\w-]+ )?(\d+|x)/(\d+|x)"
                    r"([^.]{0,80}?)creature tokens?", re.I)
_REPEAT = re.compile(r"\b(?:whenever|at the beginning of)\b|^[^:\n]{0,40}:", re.I | re.M)
_PERM_PUMP = [re.compile(r"(?:other )?creatures you control get \+([1-9]|x)/\+\d+(?![^.]*until end of turn)", re.I),
              re.compile(r"put (?:a|an|\w+) \+1/\+1 counters? on each (?:other )?creature you control", re.I),
              re.compile(r"creatures? you control enters? (?:the battlefield )?with (?:an? |\w+ )?additional \+1/\+1 counters?", re.I),
              re.compile(r"double the (?:power|number of \+1/\+1 counters)", re.I)]
_POWER_DROP = re.compile(r"creatures you control (?:get|have) [-+]?\d+/|base power (?:and toughness )?\d", re.I)


def _clause(text, start, end):
    a = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    nxt = [i for i in (text.find(".", end), text.find("\n", end)) if i != -1]
    return text[a:min(nxt) if nxt else len(text)]


def rules(cmd, finisher_lines=()):
    """Conditions the commander puts on YOUR cards: [{kind, type, stat, op, n, keyword, text, strong, counts}]."""
    from analyze import _self_name_free
    x = _self_name_free(cmd)
    out, seen = [], set()
    fin = " ".join(finisher_lines).lower()
    for m in _STAT.finditer(x):
        cl = _clause(x, m.start(), m.end())
        if not _MINE.search(cl) or _THEIRS.search(cl) or m.group(4).lower() == "x":
            continue
        typ = m.group(1).lower().rstrip("s")
        stat = "mana value" if m.group(3).lower() == "converted mana cost" else m.group(3).lower()
        op = "le" if m.group(5).lower() == "less" else "ge"
        key = (typ, stat, op, int(m.group(4)))
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(kind="stat", type=typ, stat=stat, op=op, n=int(m.group(4)), keyword=None, text=m.group(0),
                        counts=bool(_COUNTED.search(cl)), strong=bool(_COUNTED.search(cl)) or cl.lower() in fin))
    for m in _KW.finditer(x):
        cl = _clause(x, m.start(), m.end())
        if not _MINE.search(cl) and not re.search(r"\benters\b", cl, re.I) or _THEIRS.search(cl):
            continue
        key = ("creature", m.group(3).lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(kind="keyword", type="creature", stat=None, op=None, n=None, keyword=m.group(3).lower(),
                        text=m.group(0), counts=bool(_COUNTED.search(cl)), strong=bool(_COUNTED.search(cl)) or cl.lower() in fin))
    return out


def describe(r):
    if r["kind"] == "keyword":
        return f"creatures with {r['keyword']}"
    return f"{r['type']}s with {r['stat']} {r['n']} or {'less' if r['op'] == 'le' else 'greater'}"


def _type_ok(c, typ):
    tl = c.get("type_line") or ""
    if typ in ("card", "spell"):
        return "Land" not in tl
    if typ == "permanent":
        return not re.search(r"\b(?:Instant|Sorcery)\b", tl)
    if typ == "token":
        return "Creature" in tl
    return typ.title() in tl


def _value(c, stat):
    if stat == "mana value":
        return c.get("cmc") or 0
    v = c.get(stat)
    if v in (None, "", "*") or "*" in str(v):
        return None
    return num(v)


def _ok(v, r):
    return v is not None and (v <= r["n"] if r["op"] == "le" else v >= r["n"])


def judge(c, r):
    """('meets'|'makes'|'breaks'|'helps'|'misses'|'', reason) for one card against one rule."""
    text = c.get("text") or ""
    tl = c.get("type_line") or ""
    if r["kind"] == "keyword":
        kws = {k.lower() for k in c.get("keywords") or []}
        if "Creature" in tl and (r["keyword"] in kws or re.search(r"(?:^|\n|, )%s\b" % r["keyword"], text, re.I)):
            return "meets", f"a creature with {r['keyword']}"
        for m in _TOKEN.finditer(text):
            if r["keyword"] in m.group(3).lower():
                return "makes", f"makes tokens with {r['keyword']}"
        if re.search(r"creatures you control (?:have|gain) [^.]*\b%s\b" % r["keyword"], text, re.I) and "until end of turn" not in text:
            return "helps", f"gives your creatures {r['keyword']}"
        return "", ""
    if r["type"] in ("creature", "token") and r["stat"] in ("power", "toughness"):
        if "Creature" in tl:
            v = _value(c, r["stat"])
            if _ok(v, r):
                return "meets", f"{r['stat']} {int(v)} counts for your commander"
            if v is not None:
                return "misses", f"{r['stat']} {int(v)} doesn't count ({describe(r)})"
        for m in _TOKEN.finditer(text):
            p = m.group(1) if r["stat"] == "power" else m.group(2)
            if p.lower() != "x" and _ok(int(p), r):
                rep = bool(_REPEAT.search(text))
                return "makes", f"makes {m.group(1)}/{m.group(2)} tokens that count" + (" (repeatedly)" if rep else "")
        if r["stat"] == "power" and any(rx.search(text) for rx in _PERM_PUMP):
            return ("breaks", "permanently pumps your team out of the count") if r["op"] == "le" else \
                   ("helps", "permanently pumps your team into the count")
        return "", ""
    if _type_ok(c, r["type"]):
        v = _value(c, r["stat"])
        if _ok(v, r):
            return "meets", f"{r['stat']} {int(v)} fits ({describe(r)})"
    return "", ""


_QTY = dict(a=1, an=1, one=1, two=2, three=3, four=4, five=5, six=6, seven=7, x=3)
_TOKEN_N = re.compile(r"\bcreates? (?:(a|an|one|two|three|four|five|six|seven|x|that many|a number of) )?(?:tapped )?(?:[\w-]+ )?"
                      r"(\d+|x)/(\d+|x)([^.]{0,80}?)creature tokens?", re.I)


def _line_of(text, pos):
    a = text.rfind("\n", 0, pos) + 1
    b = text.find("\n", pos)
    return text[a:b if b != -1 else len(text)]


def bodies(c, r):
    """How many bodies that COUNT for the commander this card adds over a game: itself (if it qualifies) plus the
    tokens it makes. A repeatable maker ('whenever...', 'at the beginning of...', an activated ability) counts x3.
    Returns (bodies, parts) where parts explains it ('itself', '2 tokens', 'tokens every turn')."""
    if r["type"] not in ("creature", "token"):
        return 0.0, []
    text = c.get("text") or ""
    tl = c.get("type_line") or ""
    n, parts = 0.0, []
    if "Creature" in tl and judge(c, dict(r, _self=True))[0] == "meets":
        n += 1
        parts.append("itself")
    for m in _TOKEN_N.finditer(text):
        if r["kind"] == "keyword":
            ok = r["keyword"] in m.group(4).lower()
        else:
            p = m.group(2) if r["stat"] == "power" else m.group(3)
            ok = p.lower() != "x" and _ok(int(p), r)
        if not ok:
            continue
        q = (m.group(1) or "a").lower()
        qty = _QTY.get(q, 2)
        line = _line_of(text, m.start())
        rep = bool(re.search(r"\b(?:whenever|at the beginning of)\b", line, re.I)) or bool(re.match(r"^[^:]{0,60}:", line))
        n += qty * (3 if rep else 1)
        parts.append(f"{'tokens every turn' if rep else str(qty) + ' token' + ('s' if qty > 1 else '')} ({m.group(2)}/{m.group(3)})")
        break
    return n, parts


def score(c, rule_list):
    """Synergy adjustment and reason for a card against all of the commander's conditions.
    For a condition the commander COUNTS ('X is the number of creatures you control with power 2 or less'), the
    value is how many qualifying bodies the card adds, and how cheaply: Young Pyromancer (a 2-power body that keeps
    making 1/1s) beats a lone 1/1, which beats a 5-power creature (which costs points: it takes a slot and doesn't count)."""
    s, why = 0.0, ""
    for r in rule_list:
        w = 1.5 if r["strong"] else 1.0
        b, parts = bodies(c, r)
        if b > 0:
            cmc = max(c.get("cmc") or 0, 1)
            d = (5 + 5 * min(b, 4) + 3 * min(b / cmc, 2)) * w
            reason = ("a body that counts for your commander" if parts == ["itself"] else
                      f"{' + '.join(parts).replace('itself', 'a body')} that count for your commander")
        else:
            k, reason = judge(c, r)
            d = {"meets": 9, "makes": 11, "helps": 6, "breaks": -14, "misses": -10 if r["strong"] else -6}.get(k, 0) * w
            if k == "misses" and r["type"] not in ("creature", "token"):
                d = 0
        s += d
        if d and not why:
            why = reason
    return s, why


def counts_creatures(rule_list):
    """Does the commander count how many qualifying creatures you have? (go-wide: run more of them)"""
    return any(r["type"] in ("creature", "token") and (r["counts"] or r["strong"]) for r in rule_list)
