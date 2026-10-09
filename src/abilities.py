"""Commander ability parser and interaction model.

Step 1 - PARSE: split the commander's rules text into abilities and classify each one:
    keyword    "Flying, deathtouch"
    triggered  "Whenever <EVENT>, <EFFECT>"        (also "When ..." and "At the beginning of ...")
    activated  "<COST>: <EFFECT>"                  (cost = mana, {T}, sacrifice X, discard, life, counters...)
    static     everything else ("Creatures you control get +1/+1", "Spells you cast cost {1} less")
  For each ability we record the EVENT it waits for, the COSTS it needs, and the OUTPUTS it produces.

Step 2 - INTERACT: turn each ability into the cards that make it better:
    enablers  make its EVENT happen           (trigger "whenever a creature you control dies" -> sac outlets, tokens)
    fuel      pay its COSTS                    (cost "{T}" -> untappers, haste; "sacrifice an artifact" -> treasure makers)
    payoffs   use what it OUTPUTS              (output "create tokens" -> anthems, sac outlets, token doublers)
    doublers  make its triggers happen twice   (Panharmonicon-style for ETB, Teysa-style for deaths, Strionic Resonator)
  Each becomes an "interaction plan": a set of card patterns scored with the same context filter as every other
  plan, so a card only counts if it genuinely does the thing for YOU.

Step 3 - STYLE: every ability/plan gets a style (gentle, engine, aggro, control, punish, hug, chaos), which the vibe
  uses to pick the lead game plan (analyze.choose_plans)."""
import re

# ---------------------------------------------------------------------------------------------------------------------
# What a trigger EVENT is, and which cards make that event happen (enablers)
# ---------------------------------------------------------------------------------------------------------------------
EVENTS = [
    # key, regex over the trigger clause, human label, enabler card patterns, enabler type-line pattern
    ("cast_instant_sorcery", r"cast (?:an? )?(?:instant|sorcery|noncreature)", "you cast instants/sorceries",
     [r"\bcopy target (?:instant|sorcery)", r"instant (?:and|or) sorcery spells you cast cost"], r"\b(?:Instant|Sorcery)\b"),
    ("cast_creature", r"cast (?:an? )?creature spell", "you cast creature spells",
     [r"creature spells you cast cost \{\d\} less"], r"\bCreature\b"),
    ("cast_artifact", r"cast (?:an? )?artifact spell", "you cast artifact spells", [r"artifact spells you cast cost"], r"\bArtifact\b"),
    ("cast_enchantment", r"cast (?:an? )?enchantment spell", "you cast enchantment spells", [], r"\bEnchantment\b"),
    ("cast_spell", r"cast (?:a|your (?:first|second)) spell", "you cast spells", [r"costs? \{\d\} less to cast", r"\bstorm\b"], r"\b(?:Instant|Sorcery)\b"),
    ("creature_dies", r"creatures?[^,]{0,45}\b(?:dies|die|is put into a graveyard from the battlefield)", "creatures die",
     [r"sacrifice (?:a|another) creature(?:[^:.]{0,20})?:", r"create (?:a|an|one|two|three|x|that many)[^.]{0,40}creature tokens?",
      r"\bexploit\b", r"\bcasualty\b", r"each player sacrifices", r"\bblitz\b"], None),
    ("sacrifice", r"you sacrifice", "you sacrifice things",
     [r"sacrifice (?:a|another|an)(?: \w+)? (?:creature|artifact|permanent)(?:[^:.]{0,20})?:", r"create[^.]{0,40}(?:treasure|clue|food|creature) tokens?"], None),
    ("token_enters", r"tokens?[^,]{0,30}(?:enters?|you create)|you create (?:a|one or more) tokens?", "tokens are created",
     [r"create (?:a|an|one|two|three|x|that many)[^.]{0,50}tokens?", r"\bpopulate\b", r"twice that many (?:of those )?tokens"], None),
    ("creature_enters", r"creatures?[^,]{0,45}\benters?\b", "creatures enter",
     [r"create (?:a|an|one|two|three|x|that many)[^.]{0,50}creature tokens?",
      r"exile (?:another |up to \w+ )?(?:target )?(?:\w+ )?creatures? you (?:control|own)[^.]{0,40}return",
      r"put (?:a|an|target|up to \w+) creature cards?[^.]{0,40}onto the battlefield"], None),
    ("artifact_enters", r"artifacts?[^,]{0,30}\benters?\b", "artifacts enter",
     [r"create[^.]{0,30}(?:treasure|clue|food|thopter|servo|construct|artifact)[^.]{0,10}tokens?", r"\bfabricate\b"], r"\bArtifact\b"),
    ("enchantment_enters", r"enchantments?[^,]{0,30}\benters?\b|\bconstellation\b", "enchantments enter", [], r"\bEnchantment\b"),
    ("land_enters", r"lands?[^,]{0,30}\benters?\b|\blandfall\b", "lands enter",
     [r"(?:play|put) (?:an? )?additional lands?", r"search your library for[^.]{0,40}lands?[^.]{0,60}onto the battlefield",
      r"return (?:target|up to \w+) land cards? from your graveyard", r"put (?:a|up to \w+) lands? cards?[^.]{0,40}onto the battlefield"], None),
    ("attacks", r"\battacks?\b", "it/your creatures attack",
     [r"(?:creatures you control|other creatures you control|each creature you control|target creature|equipped creature|enchanted creature|creatures)[^.]{0,30}(?:have|gain|gains|has) haste", r"additional combat phase", r"(?:target creature|creatures you control|equipped creature|enchanted creature)[^.]{0,30}can't be blocked", r"untap all (?:creatures|attacking)",
      r"creatures you control (?:get \+\d+/\+\d+ and )?gain (?:trample|flying|menace)"], None),
    ("combat_damage", r"deals combat damage to (?:a player|an opponent|one or more players)", "combat damage to players",
     [r"(?:target creature|creatures you control|equipped creature|enchanted creature)[^.]{0,30}(?:can't be blocked|(?:has|have|gains?) (?:flying|menace|trample|shadow|double strike))",
      r"\bninjutsu\b", r"additional combat phase"], None),
    ("draw", r"you draw", "you draw cards",
     [r"draws? (?:two|three|x|that many|\w+) cards", r"draw (?:a|an additional) card", r"each player draws"], None),
    ("discard", r"you discard|discards? (?:a|one or more) cards?", "you discard",
     [r"discard (?:a|two|any number of) cards?", r"\bcycling\b", r"\bconnive\b", r"\brummage\b", r"then discard"], None),
    ("gain_life", r"you gain life", "you gain life", [r"you gain \w+ life", r"\blifelink\b", r"\bextort\b"], None),
    ("opponent_loses_life", r"opponents? (?:loses?|is dealt|are dealt)", "opponents lose life",
     [r"each opponent loses \w+ life", r"deals? \w+ damage to each opponent", r"(?:that player|target opponent) loses \w+ life"], None),
    ("counters_put", r"counters? (?:is|are) put|put (?:one or more )?\+1/\+1 counters", "counters are placed",
     [r"put (?:a|an|one|two|three|x|that many|\w+) \+1/\+1 counters?", r"\bproliferate\b", r"twice that many[^.]{0,20}counters",
      r"that many plus one"], None),
    ("to_graveyard", r"(?:put into|enters?) your graveyard|into your graveyard from", "cards go to your graveyard",
     [r"\bmills?\b", r"\bsurveil\b", r"discard (?:a|two) cards?", r"put the top \w+ cards? of your library into your graveyard"], None),
    ("leaves_graveyard", r"leaves? your graveyard|cards? leave your graveyard", "cards leave your graveyard",
     [r"from your graveyard", r"\bescape\b", r"\bflashback\b", r"\bdelve\b", r"\bunearth\b"], None),
    ("becomes_tapped", r"becomes tapped|you tap", "it becomes tapped", [r"untap (?:target|another|all|each|up to)"], None),
    ("cycle", r"\bcycle\b|\bcycling\b", "you cycle", [r"\bcycling\b"], None),
    ("leaves_battlefield", r"leaves the battlefield", "it leaves the battlefield", [r"exile[^.]{0,40}return (?:it|that card|them)"], None),
]
_EVENT_RX = [(k, re.compile(r, re.I), lbl, [re.compile(x, re.I) for x in en], re.compile(t) if t else None)
             for k, r, lbl, en, t in EVENTS]

# trigger doublers by event (plus the generic ones that copy/duplicate any triggered ability)
DOUBLERS = {
    "creature_enters": [r"(?:entering|enters)[^.]{0,60}triggers? an additional time"],
    "artifact_enters": [r"(?:entering|enters)[^.]{0,60}triggers? an additional time"],
    "creature_dies": [r"(?:dying|dies)[^.]{0,60}triggers? an additional time"],
    "attacks": [r"(?:attacking|attacks)[^.]{0,60}triggers? an additional time", r"additional combat phase"],
    "token_enters": [r"twice that many (?:of those )?tokens", r"tokens? would be created"],
    "counters_put": [r"twice that many[^.]{0,20}counters", r"that many plus one", r"\bproliferate\b"],
}
GENERIC_DOUBLER = [r"copy target (?:activated or )?triggered ability", r"triggers? an additional time"]
ACTIVATED_DOUBLER = [r"ability[^.]{0,40}is activated[^.]{0,60}copy", r"copy target activated"]

# ---------------------------------------------------------------------------------------------------------------------
# Activated-ability COSTS, and which cards pay them (fuel)
# ---------------------------------------------------------------------------------------------------------------------
COSTS = [
    ("tap", r"\{T\}", "it taps", [r"untap (?:target|another|all|each|up to)", r"ability[^.]{0,40}is activated[^.]{0,60}copy", r"(?:creatures you control|other creatures you control|each creature you control|target creature|equipped creature|enchanted creature|creatures)[^.]{0,30}(?:have|gain|gains|has) haste", r"as though (?:it|they) had haste"]),
    ("sac_creature", r"sacrifice (?:a|an|another|x|two|three)?[^:,]{0,15}creatures?", "sacrificing creatures",
     [r"create (?:a|an|one|two|three|x|that many)[^.]{0,50}creature tokens?", r"\bembalm\b", r"return[^.]{0,40}creature card[^.]{0,30}to the battlefield"]),
    ("sac_artifact", r"sacrifice (?:a|an|another|x|two|three)?[^:,]{0,15}artifacts?", "sacrificing artifacts",
     [r"create[^.]{0,30}(?:treasure|clue|food|thopter|servo|construct|artifact)[^.]{0,10}tokens?", r"\bfabricate\b", r"\binvestigate\b"]),
    ("sac_land", r"sacrifice (?:a|an|another)?[^:,]{0,10}lands?", "sacrificing lands",
     [r"return (?:target|up to \w+|all) land cards? from your graveyard", r"(?:play|put) (?:an? )?additional lands?"]),
    ("sac_any", r"sacrifice (?:a|an|another) (?:permanent|nonland permanent)", "sacrificing permanents",
     [r"create (?:a|an|one|two|three|x)[^.]{0,50}tokens?"]),
    ("discard", r"discard (?:a|two|x|your hand)", "discarding cards", [r"draws? (?:two|three|a) cards?", r"\bmadness\b", r"\bflashback\b"]),
    ("life", r"pay (?:\d+|x) life", "paying life", [r"you gain \w+ life", r"\blifelink\b"]),
    ("remove_counters", r"remove (?:a|an|one|two|three|x|\w+) [\w+/-]+ counters?", "removing counters",
     [r"\bproliferate\b", r"put (?:a|an|one|two|x|\w+) [\w+/-]+ counters?"]),
    ("exile_graveyard", r"exile (?:a|an|two|three|x|\w+)?[^:,]{0,20}cards? from your graveyard", "exiling cards from your graveyard",
     [r"\bmills?\b", r"\bsurveil\b", r"put the top \w+ cards? of your library into your graveyard"]),
    ("tap_creatures", r"tap (?:an|one|two|three|five|ten|x|\w+) untapped (?:\w+ )?(?:creatures?|\w+s)", "tapping your creatures",
     [r"create (?:a|an|one|two|three|x|that many)[^.]{0,50}creature tokens?", r"untap all creatures you control"]),
    ("energy", r"pay \{E\}", "paying energy", [r"you get \{E\}"]),
]
_COST_RX = [(k, re.compile(r, re.I), lbl, [re.compile(x, re.I) for x in fu]) for k, r, lbl, fu in COSTS]

# ---------------------------------------------------------------------------------------------------------------------
# What an ability OUTPUTS, which cards pay that off, and the style of the output
# ---------------------------------------------------------------------------------------------------------------------
OUTPUTS = [
    ("tokens", r"create[^.]{0,60}tokens?", "makes tokens", "gentle",
     [r"creatures you control get \+", r"for each creature you control", r"sacrifice (?:a|another) creature(?:[^:.]{0,20})?:",
      r"twice that many (?:of those )?tokens", r"\bpopulate\b", r"\bconvoke\b"]),
    ("plus_counters", r"\+1/\+1 counters?", "places +1/+1 counters", "gentle",
     [r"\bproliferate\b", r"twice that many[^.]{0,20}counters", r"that many plus one", r"(?:with|has) (?:a|one or more) \+1/\+1 counters? on"]),
    ("draw", r"\bdraws? (?:a|an|one|two|three|x|that many|\w+) cards?", "draws cards", "engine",
     [r"whenever you draw", r"no maximum hand size", r"for each card in your hand"]),
    ("mill_self", r"\bmills?\b(?! target opponent)|put the top[^.]{0,30}into your graveyard", "fills your graveyard", "engine",
     [r"from your graveyard", r"cards? in your graveyard", r"\bdelirium\b|\bthreshold\b|\bescape\b|\bflashback\b|\bdelve\b"]),
    ("lifegain", r"\bgains? (?:\w+ )?life\b", "gains life", "gentle",
     [r"whenever you gain life", r"if you(?:'ve)? gained life", r"pay (?:\d+|x) life"]),
    ("drain", r"each opponent loses|deals? \w+ damage to each opponent|loses \w+ life", "drains/burns opponents", "punish",
     [r"if a source you control would deal (?:noncombat )?damage[^.]{0,40}(?:double|plus)", r"whenever an opponent loses life"]),
    ("damage", r"deals? (?:\w+|damage equal)[^.]{0,20} damage to (?:any target|target creature|target player|each)", "deals damage", "punish",
     [r"would deal (?:noncombat )?damage[^.]{0,40}(?:double|plus|instead)", r"\bdeathtouch\b"]),
    ("reanimate", r"return[^.]{0,60}from (?:your|a) graveyard to the battlefield|put[^.]{0,40}from (?:your|a) graveyard onto the battlefield",
     "brings creatures back", "engine",
     [r"\bmills?\b", r"\bentomb\b", r"discard (?:a|two) cards?", r"when [^.]{0,30} enters(?: the battlefield)?, "]),
    ("treasure", r"\btreasures?\b|\bclues?\b|\bfood\b", "makes Treasures/Clues/Food", "engine",
     [r"sacrifice (?:an? )?(?:artifact|treasure|clue|food)[^.]{0,10}:", r"whenever you sacrifice (?:a|an|another) (?:artifact|treasure)", r"artifacts? you control"]),
    ("mana", r"\badd \{|\badd (?:one|two|three|x) mana", "makes mana", "engine",
     [r"\{x\}", r"costs? \{\d+\} more|\{\d+\}\{[wubrg]\}, \{t\}:"]),
    ("untap", r"\buntap\b", "untaps things", "engine", [r"^\{t\}[^:]{0,30}:", r"\{t\}: add"]),
    ("copy", r"\bcopy\b|\bcopies\b", "copies things", "engine", [r"when [^.]{0,30} enters(?: the battlefield)?, ", r"\binstant\b|\bsorcery\b"]),
    ("free_cast", r"without paying (?:its|their) mana cost|cast[^.]{0,30}from exile", "casts spells for free / from exile", "chaos",
     [r"\bcascade\b", r"mana value (?:\d+|x) or greater"]),
    ("extra_combat", r"additional combat", "takes extra combats", "aggro", [r"whenever[^.]{0,40}\battacks?\b", r"\bvigilance\b"]),
    ("pump", r"get \+\d+/\+\d+|gets \+\d+/\+\d+|double (?:its|the) power", "pumps creatures", "aggro",
     [r"\btrample\b", r"can't be blocked", r"double strike"]),
    ("removal", r"(?:destroy|exile) target|deals? \w+ damage to target creature|fights?", "removes things", "control",
     [r"\buntap\b", r"\bproliferate\b"]),
    ("tax", r"costs? \{\d\} more|can't (?:cast|attack|block|untap|activate)|don't untap|skip", "restricts opponents", "control", []),
    ("steal", r"gain control of|you don't own|opponent's library", "steals from opponents", "chaos", [r"sacrifice (?:a|another) (?:creature|permanent)"]),
    ("hug", r"each player (?:may )?(?:draws?|gains?|creates?|puts?)|target opponent (?:draws|creates|gains)|\bmonarch\b|\bvote\b",
     "gives every player something", "hug", []),
    ("random", r"\brandom|flip a coin|\broll\b|exchange", "is random/chaotic", "chaos", []),
    ("goad", r"\bgoad", "forces opponents to fight each other", "chaos", [r"\bgoad"]),
    ("haste_evasion", r"\bhaste\b|can't be blocked|\bflying\b|\bmenace\b|\btrample\b", "is evasive/fast", "aggro", []),
    ("needs_fodder", r"sacrifice (?:a|an|another|two) (?:other )?(?:permanent|creature|artifact|nonland permanent)", "needs things to sacrifice", "engine",
     [r"create (?:a|an|one|two|three|x|that many)[^.]{0,50}tokens?", r"\binvestigate\b", r"\bfabricate\b"]),
    ("proliferate", r"\bproliferate\b", "proliferates", "engine",
     [r"put (?:a|an|one|two|three|x|\w+) [\w+/-]+ counters?", r"\btoxic \d|\binfect\b", r"\+1/\+1 counters?"]),
    ("reanimate2", r"return (?:it|that card|target creature card|those cards) to the battlefield|creature card in your graveyard", "brings creatures back", "engine",
     [r"\bmills?\b", r"\bentomb\b", r"sacrifice (?:a|another) creature(?:[^:.]{0,20})?:", r"when [^.]{0,30} enters(?: the battlefield)?, "]),
]
TUTOR_TYPES = {"artifact": r"\bArtifact\b", "enchantment": r"\bEnchantment\b", "creature": r"\bCreature\b", "instant": r"\bInstant\b",
               "sorcery": r"\bSorcery\b", "equipment": r"\bEquipment\b", "aura": r"\bAura\b", "land": r"\bLand\b",
               "planeswalker": r"\bPlaneswalker\b", "legendary": r"\bLegendary\b"}
KEYWORD_PLANS = {   # keywords that ask for specific support
    "Ninjutsu": ("Keyword: ninjutsu wants unblocked attackers", "aggro", [r"can't be blocked", r"\bflying\b", r"\bmenace\b", r"\bshadow\b"], r"\bCreature\b"),
    "Lifelink": ("Keyword: lifelink pays off lifegain", "gentle", [r"whenever you gain life"], None),
    "Deathtouch": ("Keyword: deathtouch loves fight/ping effects", "control", [r"\bfights?\b", r"deals damage equal to its power"], None),
    "Infect": ("Keyword: infect wants pump + proliferate", "punish", [r"\bproliferate\b", r"gets \+\d+/\+\d+"], None),
    "Eminence": ("Keyword: eminence works from the command zone", "engine", [], None),
}
_OUT_RX = [(k, re.compile(r, re.I), lbl, style, [re.compile(x, re.I) for x in pay]) for k, r, lbl, style, pay in OUTPUTS]

KEYWORD_STYLES = {"Flying": "aggro", "Trample": "aggro", "Haste": "aggro", "Double strike": "aggro", "Menace": "aggro",
                  "Deathtouch": "control", "Lifelink": "gentle", "Vigilance": "gentle", "Hexproof": "engine", "Ward": "engine",
                  "Indestructible": "engine", "Flash": "control", "Partner": "engine", "Infect": "punish", "Toxic": "punish"}

EVENT_STYLE = {"cast_instant_sorcery": "engine", "cast_creature": "gentle", "creature_dies": "punish", "sacrifice": "punish",
               "token_enters": "gentle", "creature_enters": "engine", "land_enters": "gentle", "attacks": "aggro",
               "combat_damage": "aggro", "draw": "engine", "discard": "chaos", "gain_life": "gentle", "opponent_loses_life": "punish",
               "counters_put": "gentle", "to_graveyard": "engine", "leaves_graveyard": "engine", "becomes_tapped": "engine",
               "artifact_enters": "engine", "enchantment_enters": "gentle", "cast_spell": "engine", "cycle": "engine",
               "cast_artifact": "engine", "cast_enchantment": "gentle", "leaves_battlefield": "engine"}


def _norm(cmd):
    x = cmd.get("text") or ""
    for n in sorted({cmd["name"], cmd["name"].split(" // ")[0], cmd["name"].split(",")[0]}, key=len, reverse=True):
        if n:
            x = x.replace(n, "~")
    return x


class _Trig:
    def __init__(self, clause, effect):
        self._g = (None, clause, effect)

    def group(self, i):
        return self._g[i]


def _split_trigger(line):
    """'Whenever you cast an Aura, Equipment, or Vehicle spell, draw a card.' -> ('you cast an Aura, Equipment, or Vehicle spell',
    'draw a card.'). Commas inside a list ('Aura, Equipment, or ...') don't end the trigger condition."""
    m = re.match(r"^(?:whenever|when|at the beginning of|at)\b", line, re.I)
    if not m:
        return None
    rest = line[m.end():]
    for cm in re.finditer(r",\s*", rest):
        after = rest[cm.end():]
        if re.match(r"(?:or|and)\b", after) or re.match(r"[A-Z][a-z]+(?:,| or\b| and\b| spells?\b| cards?\b| creatures?\b)", after):
            continue                       # still inside a list of types
        return _Trig(rest[:cm.start()], after)
    return None


def parse(cmd):
    """Split the commander into abilities. Returns a list of dicts:
       kind (keyword/triggered/activated/static), text, event (key+label), costs [(key,label)], outputs [(key,label,style)]."""
    out = []
    lines = [l.strip() for l in _norm(cmd).split("\n") if l.strip()]
    for line in lines:
        # keyword lines: "Flying, vigilance" / "Partner" / "Ward {2}"
        if re.fullmatch(r"(?:[A-Z][a-z]+(?: [a-z]+)?(?: \{[^}]+\}| \d+)?)(?:, [a-z]+(?: [a-z]+)?(?: \{[^}]+\}| \d+)?)*", line):
            for kw in [k.strip() for k in line.split(",")]:
                name = kw.split(" {")[0].split(" ")[0].capitalize() if kw else ""
                name = {"Double": "Double strike", "First": "First strike"}.get(name, name)
                out.append(dict(kind="keyword", text=kw, event=None, costs=[], outputs=[], style=KEYWORD_STYLES.get(name)))
            continue
        kwc = re.fullmatch(r"([A-Z][a-z]+(?: [a-z]+)?)(?: (?:\{[^}]+\})+|\s+\d+)?", line)
        if kwc:
            name = kwc.group(1).split(" ")[0].capitalize()
            out.append(dict(kind="keyword", text=line, event=None, costs=[], outputs=[], style=KEYWORD_STYLES.get(name, "aggro" if name == "Ninjutsu" else None)))
            continue
        if line.lower().startswith("eminence"):
            line = re.sub(r"^eminence\s*[—-]\s*", "", line, flags=re.I)
        ab = dict(kind="static", text=line, event=None, costs=[], outputs=[], style=None)
        trig = _split_trigger(line)
        act = None if trig else re.match(r"^([^:\"]{1,90}):\s*(.*)$", line)
        effect = line
        if trig:
            ab["kind"] = "triggered"
            clause, effect = trig.group(1), trig.group(2)
            for k, rx, lbl, _, _ in _EVENT_RX:
                if rx.search(clause):
                    ab["event"] = (k, lbl)
                    break
            # any other "cast a <Type> spell" (Aura/Equipment/Vehicle, Dragon, Vampire, legendary...): the type IS the enabler
            ts = re.search(r"cast (?:an? |another |your first )?((?:[A-Za-z-]+(?:, | or |, or ))*[A-Za-z-]+) spells?", clause, re.I)
            if ts and (not ab["event"] or ab["event"][0] == "cast_spell"):
                words = [w for w in re.split(r", or |, | or ", ts.group(1)) if w.lower() not in ("a", "an", "another", "spell")]
                if words and not all(w.lower() in ("instant", "sorcery", "noncreature", "creature", "artifact", "enchantment") for w in words):
                    ab["event"] = ("cast_type:" + "|".join(w.capitalize() for w in words), "you cast " + "/".join(words) + " spells")
        elif act and re.search(r"\{|\bsacrifice\b|\bdiscard\b|\bpay\b|\btap\b|\bremove\b|\bexile\b|^\[?[+−-]\d", act.group(1), re.I):
            ab["kind"] = "activated"
            cost, effect = act.group(1), act.group(2)
            for k, rx, lbl, _ in _COST_RX:
                if rx.search(cost):
                    ab["costs"].append((k, lbl))
            mana = len(re.findall(r"\{(?:\d+|[WUBRGCX])\}", cost))
            if mana >= 3 or "{X}" in cost:
                ab["costs"].append(("mana", "a lot of mana"))
        for k, rx, lbl, style, _ in _OUT_RX:
            if rx.search(effect) and not (k == "reanimate2" and any(o[0] == "reanimate" for o in ab["outputs"])):
                ab["outputs"].append((k, lbl, style))
        tm = re.search(r"search your library for (?:an? |up to \w+ )?(artifact|enchantment|creature|instant|sorcery|equipment|aura|land|planeswalker|legendary)", effect, re.I)
        if tm:
            ab["outputs"].append(("tutor:" + tm.group(1).lower(), f"finds {tm.group(1).lower()} cards", "engine"))
        ab["style"] = (ab["outputs"][0][2] if ab["outputs"] else None) or (EVENT_STYLE.get(ab["event"][0]) if ab["event"] else None)
        out.append(ab)
    return out


def interactions(abilities):
    """Turn parsed abilities into interaction plans: [{name, weight, style, why, feed:[regex], types, pay:[regex]}]."""
    plans = []
    for ab in abilities:
        if ab["kind"] == "keyword":
            kname = ab["text"].split(" {")[0].split(" ")[0].capitalize()
            if kname in KEYWORD_PLANS:
                nm, style, pats, types = KEYWORD_PLANS[kname]
                if pats or types:
                    plans.append(dict(name=nm, weight=2.0, style=style, why=[ab["text"]], feed=pats, types=types, pay=[], kind="keyword"))
            continue
        base = 3.0 if ab["kind"] in ("triggered", "activated") else 2.0
        short = ab["text"] if len(ab["text"]) <= 70 else ab["text"][:67] + "..."
        if ab["event"] and ab["event"][0].startswith("cast_type:"):
            k, lbl = ab["event"]
            types = k.split(":", 1)[1]
            plans.append(dict(name=f"Trigger: {lbl}", weight=base, style="engine", why=[short], feed=[],
                              types=r"\b(?:%s)\b" % types, pay=[], kind="enabler"))
        elif ab["event"]:
            k, lbl = ab["event"]
            spec = next(e for e in _EVENT_RX if e[0] == k)
            feed = [r.pattern for r in spec[3]] + DOUBLERS.get(k, []) + GENERIC_DOUBLER
            plans.append(dict(name=f"Trigger: {lbl}", weight=base, style=EVENT_STYLE.get(k, ab["style"]), why=[short],
                              feed=feed, types=spec[4].pattern if spec[4] else None, pay=[], kind="enabler"))
        for k, lbl in ab["costs"]:
            spec = next((c for c in _COST_RX if c[0] == k), None)
            if spec:
                plans.append(dict(name=f"Fuel: {lbl}", weight=base * 0.8, style="engine", why=[short],
                                  feed=[r.pattern for r in spec[3]], types=None, pay=[], kind="fuel"))
        for k, lbl, style in ab["outputs"]:
            if k.startswith("tutor:"):
                t = k.split(":", 1)[1]
                plans.append(dict(name=f"Payoff: it {lbl}", weight=base * 0.9, style="engine", why=[short], feed=[],
                                  types=TUTOR_TYPES.get(t), pay=[], kind="payoff"))
                continue
            spec = next(o for o in _OUT_RX if o[0] == k)
            if spec[4]:
                plans.append(dict(name=f"Payoff: it {lbl}", weight=base * 0.7, style=style, why=[short],
                                  feed=[], types=None, pay=[r.pattern for r in spec[4]], kind="payoff"))
    # merge duplicates (two abilities wanting the same thing make it more important)
    merged = {}
    for p in plans:
        if p["name"] in merged:
            m = merged[p["name"]]
            m["weight"] = min(m["weight"] + p["weight"] * 0.6, 8)
            m["why"] = (m["why"] + p["why"])[:3]
        else:
            merged[p["name"]] = p
    for p in merged.values():
        p["weight"] = round(p["weight"], 1)
    return sorted(merged.values(), key=lambda p: -p["weight"])
