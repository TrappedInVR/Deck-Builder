"""Card logic from Forge (github.com/Card-Forge/forge, GPL-3.0): every card already translated by Forge's community
into structured game logic that follows Magic's Comprehensive Rules.

Why: English rules text has endless wordings for the same thing ("toughness greater than its power",
"power less than toughness"...). Forge collapses every wording into one vocabulary:
  triggers   T:Mode$ Attacks | ValidCard$ Creature.YouCtrl            ("whenever a creature you control attacks")
  filters    Creature.YouCtrl+powerLE2, Creature.powerLTtoughness, Card.Vampire+Other, Instant,Sorcery
  effects    DB$ DamageAll | ValidPlayers$ Player.Opponent              ("deals X damage to each opponent")
  counts     SVar:X:Count$Valid Creature.YouCtrl+powerLE2               (what an X counts)
  statics    S:Mode$ Continuous | Affected$ Elf.Other+YouCtrl | AddPower$ 1
  hints      DeckHints:Type$Vampire, DeckHas:Ability$Token, SVar:BuffedBy:Vampire (Forge's own deck-building hints)
This module reads those scripts (downloaded weekly by the workflow, never stored in the repo), evaluates Forge
filters against any card, and extracts the FACTS the builder needs: what a card counts, what it rewards, what it
wins with, what it needs. Unknown filter words are ignored (logged), so a filter can only be too lenient, never crash.
Credit: card logic from the Forge project (github.com/Card-Forge/forge)."""
import json
import os
import re

DATA_DIR = "data"
_DB = {"by_name": None, "dir": None}


def configure(data_dir):
    """Point at the data folder (common.load_cards calls this with the folder of cards.json)."""
    global DATA_DIR
    if data_dir != DATA_DIR:
        DATA_DIR = data_dir
        _DB["by_name"] = None


def _dirs():
    return [os.path.join(DATA_DIR, d) for d in ("forge/forge-gui/res/cardsfolder", "forge/cardsfolder", "forge")]
UNKNOWN_PROPS = {}

# ------------------------------------------------------------------------------------------------ parsing
def _params(s):
    out = {}
    for part in s.split("|"):
        part = part.strip()
        if "$" in part:
            k, v = part.split("$", 1)
            out[k.strip()] = v.strip()
    return out


def parse(text):
    rec = dict(name=None, types="", pt=None, keywords=[], triggers=[], statics=[], abilities=[], svars={}, hints={},
               oracle="", faces=[])
    cur = rec
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("ALTERNATE") or line == "ALTERNATE":
            face = dict(name=None, types="", pt=None, keywords=[], triggers=[], statics=[], abilities=[], svars={},
                        hints={}, oracle="")
            rec["faces"].append(face)
            cur = face
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        if key == "Name":
            cur["name"] = val
        elif key == "Types":
            cur["types"] = val
        elif key == "PT":
            cur["pt"] = val
        elif key == "K":
            cur["keywords"].append(val)
        elif key == "T":
            cur["triggers"].append(_params(val))
        elif key == "S":
            cur["statics"].append(_params(val))
        elif key == "A":
            cur["abilities"].append(_params(val))
        elif key == "SVar":
            if ":" in val:
                n, v = val.split(":", 1)
                cur["svars"][n] = v
        elif key in ("DeckHints", "DeckHas", "DeckNeeds"):
            cur["hints"][key] = val
        elif key == "Oracle":
            cur["oracle"] = val
    for n in ("BuffedBy", "BuffedByStr"):
        if n in rec["svars"]:
            rec["hints"]["BuffedBy"] = rec["svars"][n]
    return rec


def load(dirs=None, cache=None):
    """{lowercase card name: record}. Uses a JSON cache next to the scripts when it's newer than the folder."""
    if _DB["by_name"] is not None:
        return _DB["by_name"]
    dirs = dirs or _dirs()
    cache = cache or os.path.join(DATA_DIR, "forge_db.json")
    root = next((d for d in dirs if os.path.isdir(d)), None)
    by = {}
    if root:
        try:
            if os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(root):
                by = json.load(open(cache))
        except (OSError, ValueError):
            by = {}
        if not by:
            for dp, _, files in os.walk(root):
                for f in files:
                    if not f.endswith(".txt"):
                        continue
                    try:
                        rec = parse(open(os.path.join(dp, f), encoding="utf-8", errors="replace").read())
                    except OSError:
                        continue
                    if rec["name"]:
                        by[rec["name"].lower()] = rec
                        for face in rec["faces"]:
                            if face.get("name"):
                                by.setdefault(face["name"].lower(), rec)
            try:
                json.dump(by, open(cache, "w"))
            except OSError:
                pass
    _DB["by_name"], _DB["dir"] = by, root
    return by


def get(card):
    by = load()
    n = card["name"].lower()
    return by.get(n) or by.get(n.split(" // ")[0])


def status():
    by = load()
    return dict(cards=len(by), dir=_DB["dir"])


# ------------------------------------------------------------------------------------------------ filters
_KW = {"withFlying": "flying", "withTrample": "trample", "withDeathtouch": "deathtouch", "withLifelink": "lifelink",
       "withDefender": "defender", "withMenace": "menace", "withHaste": "haste", "withReach": "reach",
       "withVigilance": "vigilance", "withFirst Strike": "first strike", "withDouble Strike": "double strike",
       "withHexproof": "hexproof", "withIndestructible": "indestructible", "withFlash": "flash", "withInfect": "infect"}
_COLORS = {"White": "W", "Blue": "U", "Black": "B", "Red": "R", "Green": "G"}
_STAT = re.compile(r"^(power|toughness|cmc|CMC)(LE|LT|GE|GT|EQ|NE)(\d+|power|toughness)$")
_IGNORE = {"YouCtrl", "YouOwn", "Other", "StrictlyOther", "inZoneBattlefield", "untapped", "IsCommander", "wasCastByYou",
           "wasCast", "Self", "Card", "nonToken"}          # true for "a card you'd put in your deck" (or not checkable)
_TYPE_WORDS = {"Card", "Permanent", "Creature", "Artifact", "Enchantment", "Land", "Planeswalker", "Instant", "Sorcery",
               "Battle", "Spell", "Legendary", "Equipment", "Aura", "Vehicle", "Token", "Historic"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _prop_ok(p, card):
    if p in _IGNORE or p.startswith("YouCtrl$"):
        return True
    tl = card.get("type_line") or ""
    text = (card.get("text") or "").lower()
    m = _STAT.match(p)
    if m:
        stat, op, rhs = m.groups()
        val = {"power": _num(card.get("power")), "toughness": _num(card.get("toughness")),
               "cmc": _num(card.get("cmc")), "CMC": _num(card.get("cmc"))}[stat]
        other = {"power": _num(card.get("power")), "toughness": _num(card.get("toughness"))}.get(rhs, _num(rhs))
        if val is None or other is None:
            return False
        return {"LE": val <= other, "LT": val < other, "GE": val >= other, "GT": val > other, "EQ": val == other,
                "NE": val != other}[op]
    if p in _KW:
        kws = {k.lower() for k in card.get("keywords") or []}
        return _KW[p] in kws or bool(re.search(r"(?:^|\n|, )%s\b" % _KW[p], text))
    if p.startswith("without") and ("with" + p[7:]) in _KW:
        return not _prop_ok("with" + p[7:], card)
    if p in _COLORS:
        return _COLORS[p] in (card.get("colors") or card.get("identity") or [])
    if p == "Colorless":
        return not (card.get("colors") or [])
    if p == "MultiColor":
        return len(card.get("colors") or card.get("identity") or []) >= 2
    if p.startswith("non") and len(p) > 3:
        return not _type_ok(p[3:], card)
    if p in ("token",):
        return False
    if p in ("Legendary", "Historic"):
        return "Legendary" in tl or (p == "Historic" and ("Artifact" in tl or "Saga" in tl))
    if p[:1].isupper() and p.isalpha():
        return _type_ok(p, card)
    UNKNOWN_PROPS[p] = UNKNOWN_PROPS.get(p, 0) + 1
    return True                                    # unknown: lenient


def _type_ok(word, card):
    tl = card.get("type_line") or ""
    if word in ("Card", "Spell"):
        return "Land" not in tl.split("//")[0] if word == "Spell" else True
    if word == "Permanent":
        return not re.search(r"\b(?:Instant|Sorcery)\b", tl)
    if word == "Historic":
        return bool(re.search(r"\b(?:Legendary|Artifact|Saga)\b", tl))
    if re.search(r"\b%s\b" % re.escape(word), tl):
        return True
    return word not in _TYPE_WORDS and "changeling" in (card.get("text") or "").lower() and "Creature" in tl


def matches(flt, card):
    """Does `card` satisfy a Forge filter like 'Creature.YouCtrl+powerLE2' or 'Instant,Sorcery'?"""
    for alt in (flt or "").split(","):
        alt = alt.strip()
        if not alt:
            continue
        base, _, props = alt.partition(".")
        base = base.lstrip("!")
        if base and not _type_ok(base, card):
            continue
        if all(_prop_ok(p.strip(), card) for p in props.split("+") if p.strip()):
            return True
    return False


def describe_filter(flt):
    """'Creature.YouCtrl+powerLE2' -> 'creatures with power 2 or less'."""
    words = []
    for alt in flt.split(","):
        base, _, props = alt.partition(".")
        bits = []
        for p in props.split("+"):
            m = _STAT.match(p)
            if m:
                stat, op, rhs = m.groups()
                opw = {"LE": "or less", "LT": "less than", "GE": "or greater", "GT": "greater than", "EQ": "exactly",
                       "NE": "not"}[op]
                if rhs.isdigit():
                    bits.append(f"{stat.lower()} {rhs} {opw}" if op in ("LE", "GE") else f"{stat.lower()} {opw} {rhs}")
                else:
                    bits.append(f"{stat.lower()} {opw} {rhs}")
            elif p in _KW:
                bits.append(_KW[p])
            elif p in _COLORS or p in ("Legendary", "Historic", "Colorless", "MultiColor"):
                bits.insert(0, p.lower())
            elif p[:1].isupper() and p.isalpha() and p not in _IGNORE and p not in _TYPE_WORDS:
                bits.insert(0, p)                  # a subtype inside the props: Creature.Elf -> Elf creatures
        plural = {"Sorcery": "sorceries", "Card": "cards", "Permanent": "permanents"}
        noun = plural.get(base, (base.lower() if base in _TYPE_WORDS else base) + "s")
        adj = " ".join(b for b in bits if " " not in b)
        withs = [b for b in bits if " " in b]
        words.append((adj + " " if adj else "") + noun + (" with " + " and ".join(withs) if withs else ""))
    return " or ".join(words)


def has_stat_props(flt):
    return any(_STAT.match(p) or p in _KW for alt in flt.split(",") for p in alt.partition(".")[2].split("+"))


# ------------------------------------------------------------------------------------------------ facts
_EVENT = {"Attacks": "attacks", "AttackersDeclared": "attacks", "SpellCast": "cast", "DamageDone": "damage",
          "DamageDoneOnce": "damage", "Phase": "phase", "Drawn": "draw", "LifeGained": "gain_life",
          "Sacrificed": "sacrifice", "Discarded": "discard", "LandPlayed": "land_enters", "CounterAddedOnce": "counters_put",
          "CounterAdded": "counters_put", "Taps": "becomes_tapped", "Blocks": "blocks", "Cycled": "cycle",
          "TokenCreated": "token_enters", "LifeLost": "opponent_loses_life"}


def _effects(rec, start):
    """Follow an Execute$/ability chain (SubAbility$) and return [(api, params)]."""
    out, seen, cur = [], set(), start
    while cur and cur not in seen:
        seen.add(cur)
        prm = _params(cur) if "$" in cur and ("DB$" in cur or "AB$" in cur or "SP$" in cur) else None
        if prm is None:
            v = rec["svars"].get(cur)
            if not v:
                break
            prm = _params(v)
        api = prm.get("DB") or prm.get("AB") or prm.get("SP")
        if api:
            out.append((api, prm))
        cur = prm.get("SubAbility")
    return out


def facts(card):
    """What a card DOES, in the builder's terms, read from its Forge script. None if Forge has no script."""
    rec = get(card)
    if not rec:
        return None
    events, wins, counts, buffs, needs, outputs = [], [], [], [], [], []
    for t in rec["triggers"]:
        if t.get("Secondary") == "True":
            continue
        mode = t.get("Mode", "")
        ev = _EVENT.get(mode)
        if mode == "ChangesZone":
            if t.get("Destination") == "Battlefield":
                ev = "enters"
            elif t.get("Origin") == "Battlefield" and t.get("Destination") == "Graveyard":
                ev = "dies"
        effs = _effects(rec, t.get("Execute"))
        events.append(dict(event=ev or mode, filter=t.get("ValidCard") or t.get("ValidSource") or "",
                           desc=t.get("TriggerDescription", ""), effects=[a for a, _ in effs]))
        outputs += effs
    for a in rec["abilities"]:
        effs = _effects(rec, None) or []
        api = a.get("AB") or a.get("SP")
        if api:
            effs = [(api, a)] + _effects(rec, a.get("SubAbility"))
        outputs += effs
        cost = a.get("Cost", "")
        m = re.search(r"tapXType<(\d+)/([A-Za-z.+]+)>", cost)
        if m:
            needs.append(dict(count=int(m.group(1)), filter=m.group(2), text=f"tap {m.group(1)} untapped {m.group(2)}"))
        m = re.search(r"Sac<(\d+)/([A-Za-z.+]+)>", cost)
        if m:
            needs.append(dict(count=int(m.group(1)), filter=m.group(2), text=f"sacrifice {m.group(2)}", sac=True))
    for api, prm in outputs:
        if api in ("DamageAll", "DealDamage") and "Opponent" in (prm.get("ValidPlayers", "") + prm.get("Defined", "")):
            wins.append("damages every opponent")
        elif api == "LoseLife" and "Opponent" in prm.get("Defined", ""):
            wins.append("drains the table")
        elif api == "WinsGame":
            wins.append("wins the game outright")
        elif api == "AddPhase" and "Combat" in prm.get("ExtraPhase", "Combat"):
            wins.append("takes extra combats")
        elif api == "PumpAll" and "YouCtrl" in prm.get("ValidCards", "") and _num(re.sub(r"[^0-9]", "", prm.get("NumAtt", "")) or 0) and \
                (_num(re.sub(r"[^0-9]", "", prm.get("NumAtt", "0"))) or 0) >= 2:
            wins.append("pumps the whole team (overrun)")
        if api in ("PutCounterAll", "PumpAll") and prm.get("ValidCards") and "Self" not in prm.get("ValidCards"):
            buffs.append(dict(filter=prm["ValidCards"], how=api))
    for s in rec["statics"]:
        mode = s.get("Mode")
        if mode == "Continuous" and s.get("Affected") and "Self" not in s.get("Affected") and \
                (s.get("AddPower") or s.get("AddToughness") or s.get("AddKeyword") or s.get("AddAbility")):
            buffs.append(dict(filter=s["Affected"], how="static"))
        if mode == "ReduceCost" and s.get("ValidCard") and s.get("Activator", "You") == "You":
            buffs.append(dict(filter=s["ValidCard"], how="cost"))
    for n, v in rec["svars"].items():
        m = re.match(r"Count\$Valid (\S+)", v)
        if m and "YouCtrl" in m.group(1):
            counts.append(m.group(1))
    hints = rec.get("hints", {})
    if hints.get("BuffedBy"):
        for t in hints["BuffedBy"].split(","):
            buffs.append(dict(filter=t.strip() + ".YouCtrl", how="hint"))
    deck_types = []
    for part in (hints.get("DeckHints", "") + "&" + hints.get("DeckNeeds", "")).split("&"):
        m = re.match(r"\s*Type\$(.+)", part)
        if m:
            deck_types += [x.strip() for x in m.group(1).split("|") if x.strip()]
    return dict(events=events, wins=sorted(set(wins)), counts=counts, buffs=buffs, needs=needs,
                apis=sorted({a for a, _ in outputs}), deck_types=deck_types, hints=hints,
                keywords=rec["keywords"])


def to_conditions(flt, strong=True):
    """Convert a Forge filter into the builder's condition records (conditions.py), so everything downstream
    (scoring, caps, anthems, the simulator) works the same whichever source the condition came from."""
    out = []
    for alt in flt.split(","):
        base, _, props = alt.partition(".")
        typ = "creature" if base in ("Creature", "Card", "Permanent") or base not in _TYPE_WORDS else base.lower()
        for p in props.split("+"):
            m = _STAT.match(p)
            if m:
                stat, op, rhs = m.groups()
                stat = "mana value" if stat.lower() == "cmc" else stat.lower()
                text = describe_filter(alt)
                if rhs in ("power", "toughness"):
                    hi = rhs if op in ("LT", "LE") else stat          # powerLTtoughness -> toughness is bigger
                    out.append(dict(kind="compare", type="creature", stat=hi, op="gt", n=None, keyword=None, text=text,
                                    counts=strong, strong=strong, source="forge"))
                else:
                    n = int(rhs)
                    o, n = {"LE": ("le", n), "LT": ("le", n - 1), "GE": ("ge", n), "GT": ("ge", n + 1)}.get(op, (None, n))
                    if o:
                        out.append(dict(kind="stat", type=typ, stat=stat, op=o, n=n, keyword=None, text=text,
                                        counts=strong, strong=strong, source="forge"))
            elif p in _KW:
                out.append(dict(kind="keyword", type="creature", stat=None, op=None, n=None, keyword=_KW[p],
                                text=describe_filter(alt), counts=strong, strong=strong, source="forge"))
    return out


def subtype_of(flt):
    """'Goblin.YouCtrl' -> 'Goblin'; 'Creature.Legendary+YouCtrl' -> 'Legendary'; None for plain card types."""
    base, _, props = flt.split(",")[0].partition(".")
    if base and base not in _TYPE_WORDS:
        return base
    for p in props.split("+"):
        if p in ("Legendary", "Historic") or (p[:1].isupper() and p.isalpha() and p not in _TYPE_WORDS
                                                  and p not in _IGNORE and p not in _COLORS and p not in ("Colorless", "MultiColor")):
            return p
    return None
