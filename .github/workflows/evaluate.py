"""Card quality: score every card 0-100 by reading its mana cost, card type, power/toughness, keywords and
rules text, and comparing it against the best-in-class card for the job it does.

Benchmarks (what ~85-95 looks like in each role):
  ramp      Sol Ring, Arcane Signet, Nature's Lore, Birds of Paradise
  draw      Rhystic Study / Phyrexian Arena style engines, Night's Whisper, Harmonize
  removal   Swords to Plowshares, Beast Within, Anguished Unmaking, Chaos Warp
  counter   Counterspell, Arcane Denial, Swan Song, Fierce Guardianship
  sweeper   Damnation, Toxic Deluge, one-sided wipes (Ruinous Ultimatum)
  recursion Reanimate, Animate Dead, Eternal Witness
  protection Heroic Intervention, Teferi's Protection, Lightning Greaves
  tutor     Demonic Tutor
  creature  a body bigger than its mana value with good keywords, plus whatever its text does

Each role score is: a base for the KIND of effect (exile beats bounce, permanent ramp beats a ritual...)
  + flexibility (instant speed, modes, broad targets, repeatable) - mana value above the benchmark
  - restrictions and drawbacks (narrow targets, symmetric effects, life loss, enters tapped...).
The result is explainable: assess() also returns short notes like "instant", "exiles", "2 MV vs 1 for the best"."""
import re

ROLES = ("ramp", "draw", "removal", "counter", "sweeper", "recursion", "protection", "tutor")

_N = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "x": 3, "that many": 2}


def _n(word):
    word = (word or "").lower()
    return int(word) if word.isdigit() else _N.get(word, 1)


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rx(*parts):
    return re.compile("|".join(parts), re.I)


INSTANT = re.compile(r"\bInstant\b")
REPEAT = _rx(r"(?:whenever|at the beginning of)", r"^\{[^}]*\}(?:, ?\{[^}]*\})*[^:\n]{0,30}:", r"\{t\}[^:\n]{0,30}:")
MODAL = _rx(r"choose (?:one|two|three|one or more|one or both|any number)\b", r"\bescalate\b", r"\bentwine\b")
FLEX = _rx(r"\bcycling\b", r"\bchannel\b", r"\bkicker\b", r"\bflashback\b", r"\bbuyback\b", r"\boverload\b", r"\bentwine\b")
CONDITIONAL = _rx(r"\bonly if\b", r"\bif you control (?:a|an|three|four|five|seven|\d)", r"activate only", r"\bunless\b(?! .{0,30}pays?)",
                  r"cast this spell only")
SYMMETRIC = _rx(r"each player (?:draws|gains|puts|searches|may)", r"each opponent (?:may )?(?:draws|searches)")
LIFE_COST = re.compile(r"(?:you lose|pay) (\d+|x) life", re.I)
SELF_HARM = _rx(r"you lose the game", r"sacrifice (?:it|~|this \w+) at the beginning of the (?:next )?end step", r"at the beginning of your upkeep, sacrifice",
                r"doesn't untap during your untap step", r"\bcumulative upkeep\b", r"\becho\b", r"\bdefender\b", r"can't block",
                r"enters(?: the battlefield)? tapped", r"skip your", r"discard your hand", r"exile your (?:hand|graveyard|library)")

KW_VALUE = {"Flying": 5, "Trample": 3, "Haste": 3, "Deathtouch": 4, "Lifelink": 2, "Vigilance": 1, "Menace": 2, "Double Strike": 6,
            "First Strike": 1, "Reach": 1, "Flash": 4, "Hexproof": 4, "Indestructible": 5, "Ward": 3, "Protection": 2, "Shroud": 2,
            "Unblockable": 4, "Persist": 3, "Undying": 3, "Prowess": 1, "Infect": 0, "Changeling": 1}
_KW_TEXT = _rx(*[r"(?:^|\n|, )%s\b" % k.lower() for k in KW_VALUE])


def _keywords(card):
    k = set(card.get("keywords") or [])
    x = (card.get("text") or "")
    for m in _KW_TEXT.finditer(x):
        k.add(m.group(0).strip(", \n").title())
    if re.search(r"can't be blocked", x, re.I):
        k.add("Unblockable")
    return k


def _cmc(card):
    """Mana value as it really plays: X spells cost more, evoke/delve/convoke/'costs less for each' cost less."""
    c = card.get("cmc") or 0
    x = (card.get("text") or "")
    if "{X}" in (card.get("mana_cost") or ""):
        c += 2
    m = re.search(r"\bevoke ((?:\{[^}]+\})+)", x, re.I)
    if m:
        c = min(c, len(re.findall(r"\{", m.group(1))) + 0.5)
    if re.search(r"costs? \{1\} less to cast for each|\bdelve\b|\bconvoke\b|\bimprovise\b|affinity for", x, re.I):
        c = max(1, c - (6 if re.search(r"less to cast for each", x, re.I) else 3 if re.search(r"\bdelve\b", x, re.I) else 1.5))
    return c


# ------------------------------------------------------------------------------------------------ ability parsing
_SYM = re.compile(r"\{([^}]+)\}")


def own_text(card):
    """Rules text with the card's own name replaced by '~', and planeswalker ultimates (-5 or worse) removed:
    you rarely get to use them, so they shouldn't make a card look like the best draw/removal spell."""
    x = card.get("text") or ""
    for n in sorted({card["name"], card["name"].split(" // ")[0], card["name"].split(",")[0]}, key=len, reverse=True):
        if n:
            x = x.replace(n, "~")
    if "Planeswalker" in (card.get("type_line") or ""):
        x = "\n".join(l for l in x.splitlines() if not re.match(r"^\s*\[?[−-](?:[5-9]|\d\d|X)\]?\s*:", l))
    return x


def mana_in(cost):
    n = 0
    for sym in _SYM.findall(cost):
        sym = sym.upper()
        if sym.isdigit():
            n += int(sym)
        elif sym == "X":
            n += 2
        elif sym not in ("T", "Q", "E"):
            n += 1
    return n


def ability(x, effect_rx):
    """Find the ability that produces an effect. Returns dict(act=extra mana per use, one_shot, repeatable,
    gated=has a threshold/condition, life=life paid) or None if the effect isn't in the text."""
    for line in x.splitlines():
        m = re.search(effect_rx, line, re.I)
        if not m:
            continue
        head = line[:m.start()]
        cost = ""
        if ":" in head and not re.search(r"\b(?:when|whenever|at the beginning)\b", head.split(":")[0], re.I):
            cost = head.rsplit(":", 1)[0]
        info = dict(act=mana_in(cost), one_shot=bool(re.search(r"sacrifice ~|sacrifice this|exile ~", cost, re.I)),
                    repeatable=bool(cost) or bool(re.search(r"\b(?:whenever|at the beginning of)\b", head, re.I)),
                    gated=bool(re.search(r"activate only|remove (?:\w+|x) [\w+/-]+ counters?|counters? on ~|if (?:you|~) (?:control|have)", line, re.I)),
                    life=0)
        lm = re.search(r"pay (\d+) life", cost, re.I)
        if lm:
            info["life"] = int(lm.group(1))
        if info["one_shot"]:
            info["repeatable"] = False
        return info
    return None


def _clamp(v):
    return max(0, min(100, round(v)))


# ------------------------------------------------------------------------------------------------ role scorers
_COMBAT_GATE = re.compile(r"(?:whenever|when)\b[^.]{0,70}(?:becomes blocked|deals combat damage|\battacks?\b|attack with|blocks)|\bbattalion\b|\braid\b", re.I)


def combat_gated(x, effect_rx):
    """True if the sentence that does the job only happens in combat ('whenever ~ deals combat damage to a player,
    draw a card'): it needs your creature to attack/connect first, so it's far less reliable than a plain effect."""
    for sent in re.split(r"(?<=[.\n])", x):
        if re.search(effect_rx, sent, re.I):
            return bool(_COMBAT_GATE.search(sent))
    return False


def score_ramp(c, x, notes):
    t = c["type_line"]
    cmc = _cmc(c)
    xl = x.lower()
    ab = ability(x, r"search your library for[^.]{0,40}land") or {}
    if ab.get("act"):
        cmc += ab["act"]; notes.append(f"{ab['act']} more mana to use")
    if re.search(r"search your library for[^.]{0,60}(?:lands?|forests?|islands?|plains|swamps?|mountains?)(?: cards?)?[^.]{0,80}onto the battlefield|put (?:a|up to \w+|two) lands? cards?[^.]{0,40}onto the battlefield", xl):
        q = 74 - 11 * (cmc - 2)
        if re.search(r"(?:two|up to two)[^.]{0,30}(?:basic )?(?:lands?|forests?|\w+ cards)", xl) and "hand" not in xl:
            q += 12; notes.append("puts 2 lands into play")
        elif re.search(r"into your hand|put the other into your hand|reveal", xl):
            q += 6; notes.append("land to play + land to hand")
        if "tapped" in xl:
            q -= 3
        else:
            notes.append("untapped land")
        notes.append("permanent land ramp")
        if re.search(r"sacrifice an? (?:\w+ )?land", xl):
            q -= 30; notes.append("swaps a land, no net ramp")
        if re.search(r"lands? cards? from your hand onto the battlefield", xl) and "search your library" not in xl:
            q -= 22; notes.append("needs lands in hand")
        if re.search(r"whenever (?:enchanted|equipped) creature deals combat damage|whenever [^.]{0,30}attacks", xl):
            q -= 18; notes.append("only on combat")
        if re.search(r"sacrifice ~|ordeal|\bcounters? on ~", xl) and not ab.get("act"):
            q -= 8
    elif re.search(r"(?:play|put) (?:an? )?additional lands?", xl):
        q = 62 - 8 * (cmc - 1); notes.append("extra land drops")
    elif re.search(r"\b(?:equipped|enchanted) (?:creature|land|permanent) (?:has|gets)|(?:lands|creatures) you control have", xl) and \
            not re.search(r"^\{t\}(?:, [^:]*)?: add", xl, re.M):
        q = 40 - 6 * max(0, cmc - 1); notes.append("grants a mana ability (fixing more than ramp)")
    elif re.search(r"\badd\b", xl) and not re.search(r"^\{t\}(?:, [^:]*)?: add|^\{t\}: add", xl, re.M) and \
            ("Artifact" in t or "Creature" in t or "Enchantment" in t):
        q = 44 - 6 * max(0, cmc - 2); notes.append("mana only on a trigger")
    elif re.search(r"\badd\b", xl) and ("Creature" in t):
        q = 66 - 9 * (cmc - 1)
        if re.search(r"any color|any type|commander's color identity|one mana of any", xl):
            q += 6; notes.append("mana dork, any color")
        else:
            notes.append("mana dork")
        q -= 4                                      # dies to every sweeper
    elif re.search(r"\badd\b", xl) and ("Artifact" in t or "Enchantment" in t or "Land" in t) and \
            (ability(x, r"\badd\b") or {}).get("one_shot"):
        q = 36 - 4 * cmc; notes.append("one-shot mana (sacrifices itself)")
    elif re.search(r"\badd\b", xl) and ("Artifact" in t or "Enchantment" in t or "Land" in t):
        m = re.findall(r"add ((?:\{[^}]+\})+)", x, re.I)
        made = max((len(re.findall(r"\{", s)) for s in m), default=1)
        if re.search(r"add (?:one mana|\{[^}]+\} or \{)", xl):
            made = max(made if m else 1, 1)
        if re.search(r"add (?:two|three) mana", xl):
            made = max(made, 2 if "two" in xl else 3)
        ab = ability(x, r"\badd\b") or {}
        if ab.get("gated") or ab.get("act"):
            made = min(made, 1); notes.append("needs setup or extra mana")
            q = 56 - 10 * max(0, cmc - 2) - 4 * ab.get("act", 0)
        else:
            q = 66 + 14 * (made - (max(cmc, 1) - 1))
        if re.search(r"any color|any type|commander's color identity|one mana of any", xl) or len(set(re.findall(r"\{([WUBRG])\}", x))) >= 2:
            q += 6; notes.append(f"rock, {made} mana, fixes colors")
        else:
            notes.append(f"rock, {made} mana")
        if re.search(r"^\{t\}, (?:tap an untapped|sacrifice a|pay \d+ life|discard)[^:]*: add", xl, re.M):
            q -= 15; notes.append("extra cost to tap for mana")
        if re.search(r"among (?:legendary|creatures|permanents)|for each|if you control|activate only", xl):
            q -= 20; notes.append("conditional mana")
        if re.search(r"doesn't untap during your untap step", xl):
            q -= 16; notes.append("one big burst, then stuck tapped")
        if re.search(r"spend this mana only|can't be spent to cast", xl):
            q -= 22; notes.append("restricted mana")
        if re.search(r"\bimprint\b|exile a [^.]{0,20}card from your hand|discard a land card", xl):
            q -= 18; notes.append("costs you a card")
        if re.search(r"\{c\}", xl) and not re.search(r"\{[wubrg]\}|any color", xl):
            q -= 3
        if re.search(r"enters(?: the battlefield)? tapped", xl):
            q -= 6; notes.append("enters tapped")
    elif re.search(r"create (?:a|an|one|two|three|x|that many) (?:tapped )?treasure", xl):
        rep = bool(REPEAT.search(x)) and not INSTANT_OR_SORCERY.search(c["type_line"])
        q = 50 - 7 * (cmc - 2) + (14 if rep else 0)
        notes.append("treasure" + (" engine" if rep else ""))
        if combat_gated(x, r"treasure"):
            q -= 16; notes.append("only on combat")
    elif re.search(r"\badd\b", xl):                 # rituals and one-shot mana
        q = 34 - 4 * (cmc - 1); notes.append("one-shot mana")
    elif re.search(r"costs? \{\d\} less", xl):
        q = 58 - 8 * (cmc - 2); notes.append("cost reducer")
    else:
        q = 45 - 6 * (cmc - 2)
    return q


INSTANT_OR_SORCERY = re.compile(r"\b(?:Instant|Sorcery)\b")


def score_draw(c, x, notes):
    xl = x.lower()
    cmc = _cmc(c)
    n = 0
    for sent in re.split(r"(?<=[.\n])", xl):
        others = re.search(r"\b(?:each player|each opponent|target opponent|that player|any number of target|its controller|their)\b[^.]*draws?", sent)
        for m in re.finditer(r"draws? (a|an|one|two|three|four|five|six|seven|x|that many|\d+) cards?", sent):
            if others and others.start() < m.start() and not re.search(r"\byou draw\b|^\s*draw", sent[m.start() - 5:m.end()]):
                continue
            n = max(n, _n(m.group(1)))
    ab = ability(x, r"\bdraw") or {}
    impulse = re.search(r"exile the top (\w+) cards?[^.]{0,80}(?:you may|until)[^.]{0,40}(?:play|cast)", xl)
    if impulse:
        n = max(n, _n(impulse.group(1)) * 0.8)
    if re.search(r"pile into your hand", xl):
        n = max(n, 2.5)
    if re.search(r"look at the top (\w+)[^.]{0,80}put (?:one|two|\w+) of them into your hand", xl):
        n = max(n, 1.3)
    n = n or 1
    if re.search(r"put (?:two|\w+) cards? from your hand on top", xl):
        n = max(1, n - 1.5)
    rep = bool(REPEAT.search(x)) and bool(re.search(r"(?:whenever|at the beginning of|\{t\})[^.]{0,120}(?:draw|exile the top)", xl)) \
        and not re.search(r"sacrifice (?:~|this \w+|it)[^.]{0,40}:[^.]{0,30}draw|\{t\}, sacrifice[^:]{0,40}: draw", xl)
    if ab.get("repeatable") and not ab.get("one_shot") and re.search(r"\bdraw", xl):
        rep = True
    if re.search(r"draws? an additional card", xl):
        rep, n = True, 1
    if re.search(r"(?:shuffle|discard) your hand[^.]{0,60}(?:then )?draw", xl):
        n *= 0.45; notes.append("refills (wheel)")
    if re.search(r"if you would lose the game|would lose the game|twelfth|when [^.]{0,40}(?:counter|counters) (?:is|are) put on|"
                 r"if you have no cards|remove (?:three|four|five|\w+) [\w-]+ counters|\binstead\b[^.]{0,30}draw|draw[^.]{0,40}\binstead\b", xl):
        n = min(n, 1.5); notes.append("hard to set up")
    if not rep:
        n = min(n, 4)                               # one-shot refills top out around 'draw four'
    if "Planeswalker" in c["type_line"] and n >= 1:
        rep = True
    if ab.get("act"):
        cmc += 0.6 * ab["act"]
    if rep:
        q = 70 + 6 * min(n, 2) - 6 * max(0, cmc - 3) - (8 if ab.get("gated") else 0)
        notes.append("repeatable draw engine" + (f" (costs {ab['act']} to use)" if ab.get("act") else ""))
    else:
        q = 48 + 13 * (n - 0.55 * cmc)
        notes.append(f"draws ~{n:g} for {cmc:g} MV")
    if INSTANT.search(c["type_line"]):
        q += 5
    if "Creature" in c["type_line"] and not rep:
        q += 6; notes.append("leaves a body")
    if re.search(r"then discard|discard (?:a|two) cards?", xl) and not re.search(r"draw (?:three|four|seven)", xl):
        q -= 6
    if SYMMETRIC.search(x):
        q -= 10; notes.append("opponents draw too")
    if combat_gated(x, r"\bdraw"):
        q -= 16; notes.append("only on combat")
    elif re.search(r"whenever you attack with (?:two|three|\w+) or more|if you control (?:two|three|\w+) or more \w+", xl):
        q -= 10; notes.append("needs a specific board")
    return q


_BROAD = [(r"target (?:nonland )?permanent(?! you control)", 16, "hits any permanent"), (r"target (?:artifact, creature,? or enchantment|creature,? or planeswalker|nonland, noncreature permanent|creature or enchantment|artifact or creature)", 14, "flexible targets"),
          (r"any target", 10, "any target"), (r"target creature", 6, "creatures"), (r"target (?:artifact|enchantment|planeswalker)(?: or (?:artifact|enchantment))?", 3, "noncreature only")]
_HOSER = re.compile(r"if (?:it|that \w+)(?:'s| is) (?:red|blue|black|white|green)|target (?:red|blue|black|white|green) (?:permanent|spell)", re.I)
_NARROW = _rx(r"with (?:mana value|power|toughness) \d+ or (?:less|greater)", r"tapped creature", r"attacking(?: or blocking)?", r"blocking creature",
              r"non(?:black|white|blue|red|green|artifact|human)", r"(?:black|white|blue|red|green) creature", r"creature with flying", r"that dealt damage",
              r"target token", r"multicolored", r"target creature an opponent controls with")


def score_removal(c, x, notes):
    xl = x.lower()
    cmc = _cmc(c)
    q = 50
    ab = ability(x, r"(?:destroy|exile|damage to|return target|fights?|gets -|sacrifices)") or {}
    if ab.get("act"):
        cmc += 0.8 * ab["act"]; notes.append(f"{ab['act']} more mana to activate")
    if ab.get("gated"):
        q -= 15; notes.append("needs setup")
    if ab.get("life", 0) >= 7:
        q -= 25; notes.append(f"pay {ab['life']} life")
    if ab.get("one_shot"):
        q -= 3
    for pat, val, why in _BROAD:
        if re.search(pat, xl):
            q += val; notes.append(why); break
    if re.search(r"exile target", xl):
        q += 9; notes.append("exiles")
    elif re.search(r"destroy target", xl):
        q += 5
    elif re.search(r"shuffles? (?:it|that permanent) into (?:its|their) (?:owner's )?library|on (?:the )?(?:top|bottom) of (?:its|their) owner's library", xl):
        q += 7; notes.append("tucks")
    elif re.search(r"return target[^.]{0,40}to (?:its|their) owner's hand", xl):
        q -= 14; notes.append("only bounces")
    m = re.search(r"deals? (\d+|x) damage to (?:target|any)", xl)
    if m and not re.search(r"(?:exile|destroy) target", xl):
        d = 3 if m.group(1) == "x" else int(m.group(1))
        q += (min(d, 7) - 4) * 3; notes.append(f"{m.group(1)} damage")
    if re.search(r"\bfights?\b|deals damage equal to its power", xl):
        q -= 7; notes.append("needs your creature")
    if _HOSER.search(xl):
        q -= 28; notes.append("only hits one color")
    elif _NARROW.search(xl):
        q -= 10; notes.append("restricted targets")
    if INSTANT.search(c["type_line"]) or "Flash" in _keywords(c):
        q += 8; notes.append("instant speed")
    elif re.search(r"\bSorcery\b", c["type_line"]):
        q -= 2
    else:
        q += 2                                      # permanent / creature that removes, often with upside
    gated = combat_gated(x, r"(?:destroy|exile|damage to|fights?|gets -)")
    if ab.get("repeatable") and not ab.get("one_shot"):
        q += 12 if not ab.get("act") else 6; notes.append("repeatable")
    if gated:
        q -= 18; notes.append("only on combat")
    if m and not re.search(r"(?:exile|destroy) target", xl) and m.group(1) == "1":
        q -= 6; notes.append("1 damage kills little")
    if re.search(r"(?:up to )?(?:two|three|x) target|each (?:creature|permanent) (?:an opponent controls|your opponents control)", xl):
        q += 6; notes.append("multiple targets")
    if re.search(r"its controller (?:creates|gains|draws|may search)|you lose|you sacrifice", xl):
        q -= 3
    if re.search(r"enchanted (?:creature|permanent) can't", xl):
        q -= 6; notes.append("aura-based (can be undone)")
    q -= 8 * max(0, cmc - 1.5)
    return q


def removal_reach(card):
    """What a removal card can answer: 3 any nonland permanent, 2 any creature, 1 some creatures (damage, restricted,
    combat-only), 0 noncreature only. A substitute for a staple must reach at least as far."""
    x = own_text(card).lower()
    if combat_gated(x, r"(?:destroy|exile|damage to)"):
        return 1
    if re.search(r"(?:destroy|exile) target (?:nonland )?permanent(?! you control)|shuffles? (?:it|that permanent) into|"
                 r"owner of target permanent", x):
        return 3
    if re.search(r"(?:destroy|exile) target (?:artifact, creature,? or enchantment|creature or planeswalker|creature)(?! you control| cards?)", x) \
            and not _NARROW.search(x) and not _HOSER.search(x):
        return 2
    if re.search(r"target creature(?! cards?)|any target|creature an opponent controls", x):
        return 1
    return 0


def score_counter(c, x, notes):
    xl = x.lower()
    cmc = _cmc(c)
    free = bool(re.search(r"rather than pay|without paying (?:its|this spell's) mana cost", xl))
    q = 62 - (3 if free else 9) * max(0, cmc - 2) + (5 if cmc <= 1 else 0)
    if re.search(r"counter target spell", xl):
        q += 10; notes.append("counters anything")
    elif re.search(r"counter target (?:noncreature|instant or sorcery|activated or triggered)", xl):
        q += 2
    else:
        q -= 6; notes.append("narrow counter")
    if re.search(r"unless its controller pays", xl):
        q -= 12; notes.append("soft counter")
    if re.search(r"rather than pay|without paying (?:its|this spell's) mana cost", xl):
        q += 10; notes.append("can be free")
    if re.search(r"draw a card|scry|create", xl):
        q += 4
    return q


def score_sweeper(c, x, notes):
    xl = x.lower()
    cmc = _cmc(c)
    q = 58 - 6 * (max(cmc, 2) - 4)
    if re.search(r"blocked by|target wall|attacking creatures|blocking creatures|that dealt damage|with flying|without flying|"
                 r"tapped creatures|creatures with (?:power|toughness|mana value) \d|cast this spell only|\b(?:red|blue|black|white|green) (?:creatures|permanents)", xl):
        q -= 30; notes.append("narrow wipe")
    if re.search(r"(?:creatures|permanents|nonland permanents) (?:your opponents control|you don't control)|each opponent sacrifices", xl):
        q += 26; notes.append("one-sided")
    if re.search(r"exile all", xl):
        q += 6; notes.append("exiles")
    if re.search(r"return (?:all|each)[^.]{0,60}owners?'?s? hands?", xl) and not re.search(r"you don't control|your opponents control|overload", xl):
        q -= 14; notes.append("only bounces")
    if re.search(r"(?:destroy|exile) all (?:nonland )?permanents|(?:destroy|exile) all nonland permanents", xl):
        q += 6; notes.append("hits everything")
    m = re.search(r"all creatures get -(\d+|x)/-", xl) or re.search(r"deals? (\d+|x) damage to each creature", xl)
    if m:
        d = 4 if m.group(1) == "x" else int(m.group(1))
        q -= max(0, 4 - d) * 5; notes.append(f"-{m.group(1)} / {m.group(1)} damage sweep")
    if re.search(r"can't be regenerated", xl):
        q += 2
    if MODAL.search(x) or re.search(r"(?:except|other than|choose a creature type)", xl):
        q += 5; notes.append("flexible")
    return q


def score_recursion(c, x, notes):
    xl = x.lower()
    cmc = _cmc(c)
    q = 52 - 6 * (cmc - 2)
    if re.search(r"from (?:your|a|any) graveyard (?:onto|to) the battlefield|return[^.]{0,60}graveyard to the battlefield|"
                 r"return enchanted creature card to the battlefield|onto the battlefield", xl):
        q += 14; notes.append("reanimates")
    else:
        q -= 4
    if re.search(r"from a graveyard|any graveyard", xl):
        q += 4
    if re.search(r"return (?:all|each|up to (?:two|three|x))", xl):
        q += 8; notes.append("returns several")
    if REPEAT.search(x) and re.search(r"(?:whenever|at the beginning|\{t\})[^.]{0,80}return", xl):
        q += 12; notes.append("repeatable")
    if "Creature" in c["type_line"]:
        q += 5; notes.append("leaves a body")
    return q


_PROT = _rx(r"(?:gains?|gain|have|has) (?:hexproof|indestructible|shroud|protection)", r"phase(?:s)? out", r"can't be the targets?",
            r"equipped creature has (?:hexproof|shroud|indestructible|protection)", r"\bward\b")


def score_protection(c, x, notes):
    xl = x.lower()
    cmc = _cmc(c)
    q = 60 - 8 * max(0, cmc - 2)
    if re.search(r"creatures you control|permanents you control|each creature you control", xl):
        q += 10; notes.append("protects your whole board")
    if re.search(r"indestructible", xl):
        q += 4
    if re.search(r"hexproof|shroud|phase", xl):
        q += 4
    if INSTANT.search(c["type_line"]):
        q += 6; notes.append("instant speed")
    if "Equipment" in c["type_line"]:
        q += 4; notes.append("permanent protection")
    return q


def score_tutor(c, x, notes):
    xl = x.lower()
    cmc = _cmc(c)
    q = 66 - 8 * max(0, cmc - 1)
    if re.search(r"search your library for a card(?! named)", xl):
        q += 10; notes.append("finds any card")
    else:
        q -= 4
    if re.search(r"on top of your library", xl):
        q -= 7
    if re.search(r"onto the battlefield", xl):
        q += 6
    if INSTANT.search(c["type_line"]):
        q += 4
    return q


SCORERS = dict(ramp=score_ramp, draw=score_draw, removal=score_removal, counter=score_counter, sweeper=score_sweeper,
               recursion=score_recursion, protection=score_protection, tutor=score_tutor)


def role_tags(card):
    """Base tags from tags.py plus 'protection' (computed here so the cached card file doesn't need it)."""
    tags = set(card.get("tags") or [])
    if "land" not in tags and _PROT.search(card.get("text") or ""):
        tags.add("protection")
    return tags


# ------------------------------------------------------------------------------------------------ creature body
def score_body(c, notes):
    p, t = num(c.get("power")), num(c.get("toughness"))
    cmc = _cmc(c)
    if p is None or t is None:
        return None
    cmc = card_cmc = c.get("cmc") or 0
    expected = 1.75 * cmc + 1.5                     # e.g. 2 MV -> 2/3, 4 MV -> 4/4.5, 6 MV -> 6/6 is "fair"
    q = 46 + 4 * ((p + t) - expected)
    kws = _keywords(c)
    kq = sum(KW_VALUE.get(k, 0) for k in kws)
    q += min(kq, 16)
    if kws & set(KW_VALUE):
        notes.append(", ".join(sorted(k.lower() for k in kws & set(KW_VALUE))[:3]))
    if (p + t) - expected >= 2:
        notes.append(f"big body ({p:g}/{t:g} for {cmc:g})")
    elif (p + t) - expected <= -3:
        notes.append(f"small body ({p:g}/{t:g} for {cmc:g})")
    return q


# ------------------------------------------------------------------------------------------------ public API
_cache = {}


def assess(card):
    """{'q': overall 0-100, 'roles': {role: 0-100}, 'notes': [...], 'best_role': str|None}."""
    key = card["name"]
    if key in _cache:
        return _cache[key]
    x = own_text(card)
    tags = role_tags(card)
    notes, roles = [], {}
    for r in ROLES:
        if r in tags:
            n = []
            roles[r] = _clamp(SCORERS[r](card, x, n))
            notes.append((roles[r], r, n))
    body_notes = []
    body = score_body(card, body_notes) if "Creature" in card["type_line"] else None

    # overall quality: best role, + a little for doing several jobs, + body for creatures
    if roles:
        best_role = max(roles, key=roles.get)
        q = roles[best_role] + 4 * (len(roles) - 1)
        if body is not None:
            q += max(-6, min(8, (body - 50) / 4))   # a role card that also has a decent body
    else:
        best_role = None
        q = body if body is not None else 50        # non-role card: judged on body, or neutral (synergy decides)
    # general modifiers that apply to anything
    gen = []
    if MODAL.search(x):
        q += 5; gen.append("modal")
    if FLEX.search(x):
        q += 2
    if CONDITIONAL.search(x):
        q -= 4; gen.append("conditional")
    m = LIFE_COST.search(x)
    if m and not roles.get("draw"):
        q -= min(8, 1.5 * (3 if m.group(1).lower() == "x" else int(m.group(1))))
    if SELF_HARM.search(x):
        q -= 7; gen.append("drawback: " + SELF_HARM.search(x).group(0).lower())
    if card.get("cmc", 0) >= 7 and not roles:
        q -= 6; gen.append(f"{card['cmc']:g} MV")
    rn = sorted(notes, key=lambda r: -r[0])
    text_notes = (rn[0][2] if rn else []) + body_notes + gen
    out = dict(q=_clamp(q), roles=roles, best_role=best_role, notes=text_notes[:4])
    _cache[key] = out
    return out


def role_quality(card, role):
    return assess(card)["roles"].get(role, 0)


def describe(card, role=None):
    a = assess(card)
    mv = f"{card.get('cmc', 0):g} MV"
    bits = [mv] + a["notes"]
    return ", ".join(dict.fromkeys(bits))
