"""Commander analysis: read the commander's rules text, mana cost, power/toughness and keywords, and turn
them into (1) weighted GAME PLANS the 99 should support, (2) deck-shape adjustments (ramp, draw,
protection, creature count), and (3) a PERSONALITY profile used to match commanders to deck vibes.

Everything is rule-based (regex over Scryfall text), so it costs $0 and runs in seconds. It reads what the
card SAYS; it can't discover combos nobody wrote a pattern for, so treat it as a strong, explainable draft."""
import re
from functools import lru_cache

import abilities as AB
import context as CX

# ------------------------------------------------------------------------------------------------
# Game plans. For each plan:
#   cmd      = [(regex, weight)] evidence in the COMMANDER's text that it wants this plan
#   feed     = regexes for cards that FEED the plan (enablers: make tokens, mill, sac outlets...)
#   pay      = regexes for cards that PAY OFF the plan (they get better when the plan is happening)
#   types    = regex over a card's type line that counts as feeding it (e.g. instants for spellslinger)
#   mech     = the mechanical-theme dropdown this plan lines up with (for "better fit" suggestions)
#   creatures= suggested creature count for decks built around this plan (None = no opinion)
#   hate     = regexes for cards that actively work AGAINST this plan (penalized)
# ------------------------------------------------------------------------------------------------
PLANS = {
    "Tokens / go wide": dict(
        cmd=[(r"create[^.]{0,60}tokens?", 2), (r"tokens? you control", 2), (r"whenever (?:a|one or more) (?:creature )?tokens?", 3),
             (r"\bpopulate\b", 3), (r"for each (?:other )?creature you control", 1.5), (r"creatures you control get \+", 1.5)],
        feed=[r"create (?:a|an|one|two|three|four|five|x|\d+|that many)[^.]{0,50}creature tokens?", r"\bpopulate\b", r"\bamass\b",
              r"\bfabricate\b", r"twice that many (?:of those )?tokens", r"create[^.]{0,30}tokens? (?:that are|that's) copies",
              r"tokens? (?:would be created )?under your control"],
        pay=[r"creatures you control get \+", r"for each creature you control", r"whenever (?:a|one or more) (?:creature )?tokens?",
             r"tokens? you control", r"\bconvoke\b"],
        mech="Tokens & Go-Wide", creatures=30,
        hate=[r"(?:destroy|exile) all (?:non\w+ )?creatures(?! your opponents)", r"all creatures get -\d"]),
    "+1/+1 counters": dict(
        cmd=[(r"\+1/\+1 counters?", 2.5), (r"\bproliferate\b", 2.5), (r"counters? on (?:it|them|each)", 1)],
        feed=[r"put (?:a|an|one|two|three|x|that many|\w+) \+1/\+1 counters?", r"\bproliferate\b", r"\bevolve\b", r"\bbolster\b",
              r"\badapt \d", r"\bmodular\b", r"\bundying\b", r"\boutlast\b", r"\bsupport \d", r"twice that many (?:\+1/\+1 )?counters",
              r"\+1/\+1 counters? would be put", r"that many plus one", r"\briot\b", r"enters? with (?:a|an additional|\w+) (?:additional )?\+1/\+1 counters?"],
        pay=[r"(?:with|has) (?:a|one or more) \+1/\+1 counters? on (?:it|them)", r"for each \+1/\+1 counter",
             r"whenever (?:one or more )?\+1/\+1 counters? (?:are|is) put"],
        mech="+1/+1 Counters", creatures=30),
    "Sacrifice / aristocrats": dict(
        cmd=[(r"\bsacrifices? (?:a|another|an|one or more)", 2.5), (r"whenever (?:a|another|one or more)[^.]{0,40}(?:creatures?|permanents?)[^.]{0,30}(?:dies|die|put into a graveyard)", 3),
             (r"whenever you sacrifice", 3), (r"\bexploit\b", 2)],
        feed=[r"sacrifice (?:a|another|an)(?: \w+)? (?:creature|artifact|permanent)(?:[^.]{0,20}):", r"\bsacrifice (?:a|another) creature\b",
              r"each player sacrifices (?:a|an|two) (?:creature|permanent)",
              r"create[^.]{0,50}creature tokens?", r"\bexploit\b", r"\bcasualty \d"],
        pay=[r"whenever [^.]{0,40}\bcreatures?\b[^.]{0,30}\b(?:dies|die)\b", r"whenever you sacrifice",
             r"each opponent loses \w+ life", r"\bblitz\b"],
        mech="Sacrifice & Aristocrats", creatures=30),
    "Graveyard / self-mill / reanimation": dict(
        cmd=[(r"from your graveyard", 2.5), (r"\bmill\b", 2.5), (r"(?:leave|leaves) your graveyard", 3), (r"cards? (?:in|into) your graveyard", 2), (r"put into your graveyard from", 2.5),
             (r"\bdelirium\b|\bthreshold\b|\bescape\b|\bunearth\b|\bdredge\b|\bflashback\b", 1.5), (r"creature cards? (?:in|from) (?:a|your) graveyard", 2)],
        feed=[r"\bmills?\b(?! target opponent)", r"put the top \w+ cards? of your library into your graveyard", r"\bdredge\b", r"\bsurveil\b",
              r"discard (?:a|two|any number of) cards?", r"\bself-mill\b", r"\bentomb\b", r"search your library for a card[^.]{0,30}into your graveyard"],
        pay=[r"from your graveyard", r"from a graveyard", r"creature cards? in (?:your|a) graveyard", r"return enchanted creature card to the battlefield",
             r"\bdelirium\b", r"\bthreshold\b", r"\bescape\b",
             r"\bflashback\b", r"\bunearth\b", r"\bembalm\b", r"\beternalize\b", r"\bdisturb\b", r"cards? in your graveyard"],
        mech="Graveyard & Reanimator",
        hate=[r"exile all (?:cards from all )?graveyards", r"(?:would be put into|would go to) (?:a|any) graveyard[^.]{0,40}exile it instead",
              r"each player shuffles (?:their|his or her) graveyard"]),
    "Spellslinger (instants & sorceries)": dict(
        cmd=[(r"instant (?:or|and) sorcery", 3), (r"noncreature spells?", 2.5), (r"\bmagecraft\b", 3), (r"copy (?:target|that) (?:instant|sorcery|spell)", 2.5),
             (r"whenever you cast (?:a|an|your|your first) (?:noncreature |instant |sorcery |)spell", 1.5), (r"\bstorm\b", 2),
             (r"search your library for (?:an? )?(?:instant|sorcery)", 3)],
        feed=[r"copy (?:target|that) (?:instant|sorcery|spell)", r"instant (?:and|or) sorcery spells you cast cost", r"\bstorm\b"],
        pay=[r"whenever you cast (?:an|a) (?:instant|sorcery|noncreature)", r"\bmagecraft\b", r"\bprowess\b", r"instant (?:and|or) sorcery cards? (?:in|from) your graveyard"],
        types=r"\b(?:Instant|Sorcery)\b", mech="Spellslinger", creatures=14),
    "Artifacts": dict(
        cmd=[(r"\bartifacts?\b(?! creature you control)", 2), (r"artifacts? you control", 2.5), (r"whenever (?:an?|another|one or more) (?:nontoken )?artifacts?", 3),
             (r"\bimprovise\b|\bmetalcraft\b|\baffinity for artifacts\b", 2), (r"search your library for (?:an? )?artifact", 3)],
        feed=[r"create[^.]{0,30}(?:artifact|treasure|clue|food|thopter|servo|construct)[^.]{0,10}tokens?", r"\bfabricate\b"],
        pay=[r"artifacts? you control", r"whenever (?:an?|another|one or more) artifacts?", r"\bimprovise\b", r"\bmetalcraft\b", r"\baffinity for artifacts\b"],
        types=r"\bArtifact\b", mech="Artifacts", creatures=24,
        hate=[r"destroy all artifacts", r"exile all artifacts"]),
    "Enchantments": dict(
        cmd=[(r"\benchantments?\b", 2), (r"whenever (?:an?|another) enchantment", 3), (r"\bconstellation\b", 3), (r"\baura\b", 1.5), (r"search your library for (?:an? )?enchantment", 3.5)],
        feed=[r"create[^.]{0,30}enchantment[^.]{0,10}token"],
        pay=[r"enchantments? you control", r"whenever (?:an?|another) enchantment", r"\bconstellation\b", r"\bbestow\b"],
        types=r"\bEnchantment\b", mech="Enchantress", creatures=20,
        hate=[r"destroy all enchantments", r"exile all enchantments"]),
    "Lifegain": dict(
        cmd=[(r"(?:you )?gain(?:s|ed)? (?:\w+ )?life", 2.5), (r"whenever you gain life", 3), (r"\blifelink\b", 1.5), (r"your life total", 1.5)],
        feed=[r"you gain \w+ life", r"gain \w+ life", r"\blifelink\b", r"\bextort\b"],
        pay=[r"whenever you gain life", r"if you(?:'ve)? gained life", r"life total(?: is)? (?:greater|higher)", r"for each 1 life you gained"],
        mech="Lifegain"),
    "Lands / landfall / ramp": dict(
        cmd=[(r"\blandfall\b", 3), (r"whenever a land (?:you control )?enters", 3), (r"lands? you control", 2), (r"(?:play|put) (?:an? )?additional lands?", 2.5),
             (r"land cards? (?:from|in) your graveyard", 2.5), (r"play lands? from", 2)],
        feed=[r"search your library for (?:up to \w+ )?(?:a |an |two )?(?:basic )?lands?", r"(?:play|put) (?:an? )?additional lands?",
              r"put (?:a|up to \w+) lands? cards? (?:from|onto)", r"return (?:target|up to \w+) land cards? from your graveyard", r"\bfetch"],
        pay=[r"\blandfall\b", r"whenever a land (?:you control )?enters", r"for each land you control", r"lands you control"],
        mech="Landfall & Lands Matter"),
    "Enter-the-battlefield / blink": dict(
        cmd=[(r"whenever (?:a|another|one or more)[^.]{0,40}enters(?: the battlefield)? under your control", 2.5),
             (r"exile (?:another |up to \w+ )?(?:target )?(?:\w+ )?creatures? you control[^.]{0,40}return", 3), (r"\bblink\b|\bflicker\b", 3),
             (r"enters the battlefield (?:or|and) (?:whenever it )?(?:attacks|dies)", 1)],
        feed=[r"exile (?:another |up to \w+ )?(?:target )?(?:\w+ )?(?:creature|permanent)s? you (?:control|own)[^.]{0,40}return (?:it|them|that card)",
              r"\bblink\b|\bflicker\b"],
        pay=[r"when (?:this creature|\w+(?:, [\w ]+)?) enters(?: the battlefield)?,", r"when [^.]{0,30} enters(?: the battlefield)?, (?:draw|create|destroy|exile|return|search|you gain|deal|target)"],
        mech="Blink & Value", creatures=30),
    "Voltron / equipment & auras": dict(
        cmd=[(r"equipped creature|equipment", 3), (r"enchanted creature|\bauras?\b", 2.5), (r"\battach(?:ed)?\b", 2.5), (r"commander damage", 2),
             (r"for each (?:aura|equipment)", 3), (r"search your library for (?:an? )?(?:aura|equipment)", 3)],
        feed=[r"\bequip\b", r"equipped creature (?:gets|has)", r"enchanted creature (?:gets|has)", r"\breconfigure\b", r"\bliving weapon\b"],
        pay=[r"for each (?:aura|equipment)", r"equipped creatures? you control", r"whenever (?:an? )?(?:aura|equipment) (?:enters|becomes attached)"],
        types=r"\b(?:Equipment|Aura)\b", mech="Equipment & Auras (Voltron)", creatures=20),
    "Combat / attack triggers": dict(
        cmd=[(r"whenever[^.]{0,40}\battacks?\b", 2.5), (r"deals combat damage to (?:a player|an opponent)", 3), (r"additional combat", 3),
             (r"attacking creatures?", 2), (r"\bmyriad\b|\bbattle cry\b|\bexalted\b|\bmelee\b", 2)],
        feed=[r"\bhaste\b", r"additional combat phase", r"can't be blocked", r"creatures you control (?:get \+\d+/\+\d+|gain|have)[^.]{0,30}(?:trample|haste|menace|flying|double strike)",
              r"\bdouble strike\b", r"untap all creatures", r"\bmenace\b"],
        pay=[r"whenever[^.]{0,40}\battacks?\b", r"deals combat damage to (?:a player|an opponent)", r"\bbattle cry\b", r"\bexalted\b", r"\bmyriad\b"],
        mech="Combat & Aggro", creatures=32),
    "Card draw engine / wheels": dict(
        cmd=[(r"whenever you draw", 3), (r"draws? (?:your )?(?:second|additional) card", 3), (r"each player draws", 2), (r"cards? in (?:your|their|each player's) hand", 1.5)],
        feed=[r"draws? (?:two|three|four|x|seven|that many) cards", r"each player (?:discards their hand|draws)", r"\bwheel\b"],
        pay=[r"whenever you draw", r"(?:second|additional) card each turn", r"for each card in your hand", r"no maximum hand size"],
        mech="Draw, Wheels & Discard"),
    "Discard / madness": dict(
        cmd=[(r"whenever you discard", 3), (r"\bdiscards?\b", 1.5), (r"\bmadness\b", 3), (r"\bcycl(?:e|ing)\b", 2)],
        feed=[r"discard (?:a|two|any number of|your) (?:cards?|hand)", r"\bcycling\b", r"\bconnive\b", r"\brummage\b"],
        pay=[r"whenever you discard", r"\bmadness\b", r"whenever you cycle", r"from your graveyard"],
        mech="Draw, Wheels & Discard"),
    "Treasure / clues / food": dict(
        cmd=[(r"\btreasures?\b", 3), (r"\bclues?\b", 3), (r"\bfood\b", 3), (r"\bblood tokens?\b", 3), (r"sacrifice (?:an? )?artifact", 2)],
        feed=[r"create (?:a|an|one|two|three|x|that many) (?:tapped )?(?:treasure|clue|food|blood|gold)", r"\binvestigate\b"],
        pay=[r"whenever you sacrifice (?:a|an|another) (?:treasure|clue|food|artifact)", r"sacrifice (?:an? )?(?:treasure|artifact|food|clue)[^.]{0,10}:"],
        mech="Treasure & Clues"),
    "Burn / drain the table": dict(
        cmd=[(r"(?:deals?|damage)[^.]{0,30}(?:to )?each opponent", 3), (r"each opponent loses", 3), (r"noncombat damage", 3),
             (r"whenever an opponent (?:loses life|is dealt damage)", 3), (r"deals? \w+ damage to (?:any target|target (?:player|opponent))", 1.5)],
        feed=[r"deals? \w+ damage to each opponent", r"each opponent loses \w+ life", r"deals? \w+ damage to (?:any target|target player|each player)",
              r"(?:that player|target opponent|target player|defending player) loses \w+ life",
              r"if a source you control would deal (?:noncombat )?damage[^.]{0,40}(?:double|plus)"],
        pay=[r"whenever an opponent (?:loses life|is dealt damage)", r"whenever a source you control deals noncombat damage"],
        mech="Group Slug & Burn"),
    "Big creatures / power matters": dict(
        cmd=[(r"power (?:\d+|x) or greater", 3), (r"greatest power", 3), (r"\bferocious\b", 3), (r"(?:total )?power[^.]{0,20}(?:among|of creatures)", 2),
             (r"creature spells? (?:you cast )?with (?:mana value|power) \d+ or greater", 3)],
        feed=[r"\btrample\b", r"\bfights?\b", r"\bmonstrosity\b", r"creature spells you cast cost \{\d\} less"],
        pay=[r"power (?:\d+|x) or greater", r"greatest power", r"\bferocious\b"],
        mech="Big Creatures & Stompy", creatures=34),
    "Cast from exile / impulse draw": dict(
        cmd=[(r"exile the top[^.]{0,60}(?:you may (?:play|cast)|until)", 3), (r"cast[^.]{0,30}from exile", 3), (r"cards? (?:you own )?in exile", 2.5),
             (r"\bforetell\b|\bplot\b|\bcascade\b|\bdiscover\b", 2.5)],
        feed=[r"exile the top[^.]{0,60}you may (?:play|cast)", r"\bcascade\b", r"\bdiscover \d", r"\bforetell\b", r"\bplot\b"],
        pay=[r"(?:play|cast)[^.]{0,30}from exile", r"whenever you cast a spell from exile"],
        mech="Draw, Wheels & Discard"),
    "Legends / historic": dict(
        cmd=[(r"\blegendary\b(?! creature you control gets)", 2), (r"\bhistoric\b", 3)],
        feed=[], pay=[r"\bhistoric\b", r"legendary (?:creatures?|spells?|permanents?) you control"],
        types=r"\bLegendary\b"),
    "Tap & untap abilities": dict(
        cmd=[(r"\buntap (?:target|another|each|all|up to)", 3), (r"becomes? tapped", 2), (r"tap an untapped", 2), (r"\binspired\b", 2)],
        feed=[r"\buntap (?:target|another|all|each|up to)", r"\bseedborn\b", r"(?:has|have|gains?) haste", r"as though (?:it|they) had haste"],
        pay=[r"\{t\}:[^.]{0,40}(?:draw|deal|create|add|destroy|exile|put)", r"whenever[^.]{0,30}becomes tapped"]),
    "Politics / goad / monarch": dict(
        cmd=[(r"\bgoad", 3), (r"\bmonarch\b", 3), (r"\bvote\b|will of the council|council's dilemma", 3), (r"target opponent (?:chooses|gains control|may)", 2)],
        feed=[r"\bgoad", r"\bmonarch\b", r"\bvote\b", r"will of the council", r"council's dilemma"],
        pay=[r"attacked this turn", r"attacking (?:one of )?your opponents", r"whenever a creature attacks one of your opponents"],
        mech="Group Hug & Politics"),
    "Steal & copy opponents' stuff": dict(
        cmd=[(r"gain control of", 3), (r"spells? (?:your opponents|an opponent) (?:own|control)", 3), (r"cards? (?:your opponents|an opponent) owns?", 3),
             (r"(?:from|of) (?:an opponent's|each opponent's|target opponent's) library", 2)],
        feed=[r"gain control of (?:target|that|each|all)", r"exile the top[^.]{0,30}(?:each|target) opponent's library[^.]{0,60}(?:cast|play)",
              r"cast (?:it|that card|them) without paying"],
        pay=[r"you don't own", r"(?:spells|cards) you don't own"]),
    "Mill your opponents": dict(
        cmd=[(r"(?:target|each) (?:player|opponent) mills", 3), (r"cards? in (?:an opponent's|each opponent's|their) graveyards?", 2), (r"opponent's library", 1.5)],
        feed=[r"(?:target|each) (?:player|opponent) mills", r"target player puts the top \w+ cards", r"each opponent mills"],
        pay=[r"cards? in (?:each opponent's|an opponent's|target opponent's) graveyard", r"no cards in (?:their|his or her) library"]),
    "Dice & coin flips": dict(
        cmd=[(r"roll (?:a|one or more|\w+) d\d+|\broll\b", 3), (r"flip (?:a|\w+) coins?", 3)],
        feed=[r"roll (?:a|two|one or more|\w+) d\d+", r"flip (?:a|\w+) coins?"],
        pay=[r"whenever you roll", r"whenever you (?:win|lose) a (?:coin )?flip", r"roll (?:an )?additional", r"reroll"]),
    "Vehicles": dict(
        cmd=[(r"\bvehicles?\b", 3), (r"\bcrew(?:s|ed)?\b", 3)],
        feed=[r"\bcrew \d"], pay=[r"vehicles? you control", r"whenever (?:a|another) vehicle", r"becomes crewed"], types=r"\bVehicle\b"),
    "Poison / proliferate": dict(
        cmd=[(r"\binfect\b|\btoxic\b|poison counters?", 3), (r"\bproliferate\b", 1.5)],
        feed=[r"\binfect\b", r"\btoxic \d", r"\bpoisonous\b", r"\bproliferate\b"], pay=[r"poison counters?"]),
    "Superfriends (planeswalkers)": dict(
        cmd=[(r"\bplaneswalkers?\b", 2.5), (r"loyalty (?:abilities|counters?)", 3)],
        feed=[r"\bproliferate\b", r"loyalty abilities[^.]{0,30}additional"], pay=[r"planeswalkers? you control"], types=r"\bPlaneswalker\b", creatures=22),
    "Clones & copies": dict(
        cmd=[(r"(?:token that's a |becomes a )?copy of (?:target|another|a|that)", 2.5), (r"\bpopulate\b", 1.5)],
        feed=[r"(?:enter|enters)(?: the battlefield)? as a copy of", r"create a token that's a copy of", r"becomes a copy of", r"\bmyriad\b"], pay=[]),
    "Big spells / X costs": dict(
        cmd=[(r"mana value (?:5|6|7|8) or greater", 3), (r"greatest mana value", 2), (r"spells? with mana value \d+ or greater", 2.5), (r"\{x\}", 1)],
        feed=[r"add (?:\{c\}\{c\}|\{c\}\{c\}\{c\}|x mana|that much mana|an amount of)", r"costs? \{\d\} less to cast"],
        pay=[r"\{x\}"]),
    "Flash / opponents' turns": dict(
        cmd=[(r"\bflash\b", 1.5), (r"during (?:each|an) opponent's turn", 3), (r"whenever you cast a spell during an opponent's turn", 3)],
        feed=[r"\bflash\b", r"as though (?:it|they) had flash"], pay=[r"during (?:each|an) opponent's turn"], types=r"\bInstant\b"),
    "Creature spells matter": dict(
        cmd=[(r"whenever you cast a creature spell", 3), (r"creature spells you cast", 2.5)],
        feed=[r"creature spells you cast cost", r"you may cast creature spells as though"],
        pay=[r"whenever you cast a creature spell"], types=r"\bCreature\b", creatures=36),
}

_RX = {}
for _name, _p in PLANS.items():
    _RX[_name] = dict(
        cmd=[(re.compile(r, re.I), w) for r, w in _p["cmd"]],
        feed=re.compile("|".join(_p["feed"]), re.I) if _p.get("feed") else None,
        pay=re.compile("|".join(_p["pay"]), re.I) if _p.get("pay") else None,
        types=re.compile(_p["types"]) if _p.get("types") else None,
        hate=re.compile("|".join(_p["hate"]), re.I) if _p.get("hate") else None)

EVASION = {"Flying", "Trample", "Menace", "Shadow", "Fear", "Intimidate", "Skulk", "Horsemanship"}
_UNBLOCKABLE = re.compile(r"can't be blocked", re.I)
_PROTECT_KW = re.compile(r"\b(?:hexproof|indestructible|shroud|ward)\b", re.I)
_ACT_COST = re.compile(r"^(?:\{[^}]+\})+[^:\n]{0,40}:", re.M)
_REPEAT_DRAW = re.compile(r"(?:whenever|at the beginning of)[^.]{0,80}draw (?:a|one|two|that many|cards?)|\{t\}[^:]{0,20}:[^.]{0,30}draw", re.I)
_REPEAT_RAMP = re.compile(r"\badd \{|(?:whenever|at the beginning of)[^.]{0,60}(?:treasure|search your library for (?:a|up to \w+) (?:basic )?land)|"
                          r"(?:play|put) (?:an? )?additional land", re.I)
_REPEAT_REMOVAL = re.compile(r"(?:whenever|at the beginning of|\{t\}[^:]{0,20}:)[^.]{0,80}(?:destroy|exile) target|"
                             r"(?:whenever|\{t\}[^:]{0,20}:)[^.]{0,60}deals? \w+ damage to (?:any target|target creature)", re.I)
_SELF_ANCHORED = re.compile(r"whenever (?:~|this creature|it|\w+(?:, [\w' -]+)?) (?:attacks|deals combat damage|becomes tapped)|"
                            r"\{t\}[^:]{0,15}:|equipped|enchanted creature|commander damage", re.I)


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


NOT_CREATURE_TYPES = {"Equipment", "Vehicle", "Aura", "Food", "Treasure", "Clue", "Forest", "Island", "Plains", "Swamp", "Mountain",
                      "Saga", "Shrine", "Gate", "Cave", "Desert", "Lair", "Locus", "Urza's", "Power-Plant", "Tower", "Mine",
                      "Sphere", "Fortification", "Contraption", "Attraction", "Blood", "Gold", "Powerstone", "Incubator", "Map",
                      "Junk", "Class", "Case", "Room", "Role", "Rune", "Background", "Cartouche", "Curse", "Shard", "Town", "Omen"}


ALL_TYPES = set()          # filled the first time creature_types() runs (used by cared_types)


def creature_types(idx_cards):
    """All creature subtypes that appear in the card pool (used to spot 'Dragons you control' etc.)."""
    out = set()
    for c in idx_cards:
        tl = c.get("type_line") or ""
        if "Creature" in tl and "—" in tl:
            for half in tl.split("//"):
                if "—" in half and "Creature" in half:
                    out.update(w for w in half.split("—", 1)[1].split() if w[:1].isupper())
    out -= NOT_CREATURE_TYPES
    ALL_TYPES.update(out)
    return out


def _self_name_free(cmd):
    """Rules text with the card's own name replaced by '~' so 'Whenever Gorm attacks' reads as a self-trigger."""
    x = cmd.get("text") or ""
    for n in sorted({cmd["name"], cmd["name"].split(" // ")[0], cmd["name"].split(",")[0]}, key=len, reverse=True):
        if n:
            x = x.replace(n, "~")
    return x


# ---------------------------------------------------------------------------------------------------------------
# Commander role: is the commander's OWN ability how the deck wins (finisher), or is it an engine that needs
# separate win conditions, or a value piece with no engine at all?
_N = r"(?:\d+|x|that much|half (?:their|his or her) life)"
FINISH = [  # (label, pattern) read on the commander's text, and on cards when looking for win conditions
    ("drains the table", r"\beach opponent loses (?:\d+|x|that much|life equal|half)"),
    ("drains the table", r"\btarget opponent loses (?:[3-9]|\d\d|x|that much|life equal|half)"),
    ("damages every opponent", r"deals? (?:\d+|x|that much|damage equal to [^.]{0,40}) (?:damage )?to each opponent"),
    ("damages every opponent", r"deals? (?:[3-9]|\d\d|x|that much) damage to (?:target|that) (?:player|opponent)"),
    ("wins the game outright", r"\byou win the game\b"),
    ("takes extra combats", r"\badditional combat phase\b"),
    ("poisons opponents", r"\binfect\b|\btoxic \d|\bpoison counters?\b"),
    ("pumps the whole team (overrun)", r"creatures you control (?:get|gain) \+(?:[2-9]|x)/\+(?:\d|x)[^.]{0,40}(?:trample|until end of turn)|"
                                       r"creatures you control gain trample and get \+(?:[2-9]|x)"),
    ("doubles damage", r"deals? double that damage|\bdouble (?:the )?damage\b|deals twice that much damage"),
]
_FINISH_RX = [(lbl, re.compile(r, re.I)) for lbl, r in FINISH]
_CARD_POISON = re.compile(r"\binfect\b|\btoxic \d", re.I)
_WORDS = dict(one=1, two=2, three=3, four=4, five=5, six=6, seven=7, eight=8, nine=9, ten=10, eleven=11, twelve=12,
              thirteen=13, fourteen=14, fifteen=15, twenty=20, thirty=30, forty=40, fifty=50, hundred=100)
_REQ = [re.compile(r"\btap (\w+) untapped (\w+?)(?: creatures)? you control", re.I),
        re.compile(r"\bif you control (\w+) or more (\w+)", re.I),
        re.compile(r"\b(\w+) or more (\w+?)(?: creatures)? you control", re.I),
        re.compile(r"\bsacrifice (\w+) (\w+)", re.I)]


def _count(word):
    w = word.lower()
    return int(w) if w.isdigit() else _WORDS.get(w)


def _noun_type(noun):
    """'Elves' -> 'Elf', 'creatures' -> 'creature', 'artifacts' -> 'artifact'; None if not a type."""
    n = noun.strip().lower()
    for cand in (n, n[:-1], n[:-2], n[:-3] + "f", n[:-3] + "y", n[:-3]):
        if not cand:
            continue
        for t in ALL_TYPES:
            if t.lower() == cand:
                return t
        if cand in ("creature", "artifact", "enchantment", "land", "permanent", "token", "planeswalker"):
            return cand
    return None


def _negated(text, start):
    return bool(CX._NEG_BEFORE.search(text[:start][-40:].lower()))


def commander_role(cmd, routes=()):
    """finisher / engine / value, with the ability that wins and any numeric requirement it has.
    finisher: the commander's own ability ends the game (drain, burn the table, 'you win', extra combats, infect,
              team pump, damage doubling) or it is a big evasive body (commander damage)
    engine:   it has a game plan but needs other cards to actually close the game
    value:    no engine in its text"""
    x = _self_name_free(cmd)
    how, lines, req = [], [], None
    for line in x.split("\n"):
        for lbl, rx in _FINISH_RX:
            m = rx.search(line)
            if m and not _negated(line, m.start()) and lbl not in how:
                how.append(lbl)
                if line not in lines:
                    lines.append(line)
    for line in lines:
        for rx in _REQ:
            m = rx.search(line)
            if m and _count(m.group(1)) and _noun_type(m.group(2)):
                req = dict(count=_count(m.group(1)), type=_noun_type(m.group(2)), noun=m.group(2), text=m.group(0))
                break
        if req:
            break
    names = [r["name"] for r in routes]
    if "Voltron / equipment & auras" in names[:2]:
        how.append("commander damage (big evasive body)")
    if how:
        role = "finisher"
        note = (f"Its own ability is a win condition ({', '.join(how)}), so the deck is built to ENABLE it: extra cards "
                f"that trigger, fuel and protect it, and fewer stand-alone win conditions.")
        if req:
            note += f" It needs {req['count']} {req['noun']} ('{req['text']}'), so the deck runs {req['count'] + 4}+ of them."
    elif routes:
        role, note = "engine", ("It is an engine, not a finisher: it generates value, so the deck adds dedicated win conditions "
                                "(team pumps, drains, extra combats, game-ending spells) to turn that value into a win.")
    else:
        role, note = "value", "It has no engine of its own, so the deck leans on its themes and carries extra win conditions."
    return dict(role=role, how=how, lines=lines, requires=req, note=note)


def is_wincon(card):
    """A card that ends games on its own or with a board: drains, table burn, 'you win', extra combats, overruns,
    damage doublers. Poison only counts in a poison deck (handled by the plan)."""
    x = card.get("text") or ""
    for lbl, rx in _FINISH_RX:
        if lbl == "poisons opponents":
            continue
        m = rx.search(x)
        if m and not _negated(x, m.start()):
            return lbl
    return ""


_TYPE_RX = {}


def _type_rx(all_types):
    key = frozenset(all_types)
    if key not in _TYPE_RX:
        forms = {}
        for t in all_types:
            fs = [t, t + "s", t + "es"]
            if t.endswith("f"):
                fs.append(t[:-1] + "ves")
            if t.endswith("y"):
                fs.append(t[:-1] + "ies")
            for f in fs:
                forms[f] = t
        rx = re.compile(r"\b(%s)\b" % "|".join(sorted(map(re.escape, forms), key=len, reverse=True)))
        _TYPE_RX[key] = (rx, forms)
    return _TYPE_RX[key]


def typal_refs(cmd, all_types):
    """Creature types the commander's text talks about (its own types included), e.g. 'other Dragons you control'.
    Types that only appear in tokens it CREATES don't count (Talrand makes Drakes; it doesn't care about Drakes)."""
    x = re.sub(r"create[^.]*?tokens?", " ", _self_name_free(cmd), flags=re.I)
    rx, forms = _type_rx(all_types)
    return sorted({forms[m] for m in rx.findall(x)})


# Each strategy's style. The vibe's style weights (options.VIBES[...]["styles"]) decide which strategy leads.
PLAN_STYLE = {
    "Tokens / go wide": "gentle", "+1/+1 counters": "gentle", "Sacrifice / aristocrats": "punish",
    "Graveyard / self-mill / reanimation": "engine", "Spellslinger (instants & sorceries)": "engine", "Artifacts": "engine",
    "Enchantments": "gentle", "Lifegain": "gentle", "Lands / landfall / ramp": "gentle", "Enter-the-battlefield / blink": "engine",
    "Voltron / equipment & auras": "aggro", "Combat / attack triggers": "aggro", "Card draw engine / wheels": "chaos",
    "Discard / madness": "chaos", "Treasure / clues / food": "engine", "Burn / drain the table": "punish",
    "Big creatures / power matters": "aggro", "Cast from exile / impulse draw": "chaos", "Legends / historic": "gentle",
    "Tap & untap abilities": "engine", "Politics / goad / monarch": "hug", "Steal & copy opponents' stuff": "chaos",
    "Mill your opponents": "control", "Dice & coin flips": "chaos", "Vehicles": "aggro", "Poison / proliferate": "punish",
    "Superfriends (planeswalkers)": "control", "Clones & copies": "engine", "Big spells / X costs": "engine",
    "Flash / opponents' turns": "control", "Creature spells matter": "gentle",
}


def _vibe_shift(weight, style, styles):
    """The vibe re-weights plans by style, but never erases what the commander obviously does (floor 40%)."""
    aff = (styles or {}).get(style or "", 0.0)
    return round(max(weight * 0.4, weight * (1 + 0.6 * aff)), 2)


_CARE = [r"{T}s? you control", r"\bother {T}s?\b", r"\bwhenever (?:a|an|another|one or more|each) (?:\w+ )?{T}s?\b",
         r"\b{T} spells?\b", r"\b{T} (?:creature )?cards?\b", r"\bnumber of {T}s\b", r"\beach {T}\b",
         r"\bsacrifice (?:a|an|another|\w+) {T}s?\b", r"\btarget {T}\b", r"\buntapped {T}s\b", r"\b{T}s get\b",
         r"\b{T}s? (?:and|or) [A-Z]", r"\bchosen type\b", r"\bthat's an? {T}\b", r"creatures? (?:that are|that's) {T}s?\b"]


def cared_types(cmd, all_types=None):
    """Creature types this commander SUPPORTS, strongest first: its own type or a different one (Kykar -> Spirit,
    Kaalia -> Angel/Demon/Dragon, Tolsimir -> Wolf). Types only named in tokens it creates don't count, nor 'non-X'."""
    types = all_types or ALL_TYPES
    if not types:
        return []
    x = re.sub(r"create[^.]*?tokens?", " ", _self_name_free(cmd), flags=re.I)
    x = re.sub(r"\bnon-?[A-Z][a-z]+", " ", x)
    rx, forms = _type_rx(frozenset(types))
    own = set(re.findall(r"[A-Z][a-z]+", (cmd.get("type_line") or "").split("—", 1)[1])) if "—" in (cmd.get("type_line") or "") else set()
    score = {}
    for m in rx.finditer(x):
        t = forms[m.group(1)]
        around = x[max(0, m.start() - 45):m.end() + 60]
        word = re.escape(m.group(1))
        if any(re.search(p.replace("{T}s?", word + r"s?").replace("{T}s", word).replace("{T}", word), around, re.I) for p in _CARE) \
                or re.search(r"(?:%s)(?:,| or)[^.]{0,40}(?:creature )?(?:card|spell)" % word, around) \
                or re.search(r"(?:, | or |, or )(?:an? )?%s\b" % word, around):
            score[t] = score.get(t, 0) + 1
    return sorted(score, key=lambda t: (-score[t], t not in own, t))


def analyze(cmd, all_types=frozenset(), vibe=None):
    """Return the commander's game plan profile.

    plans:    [{name, weight, why}] strongest first (weight ~1..10)
    adjust:   role quota changes, e.g. {'ramp': +2, 'draw': -2, 'protection': 3}
    creatures: suggested creature count (None = let the theme/vibe decide)
    notes:    plain-English observations (stats, mana, keywords)"""
    x = _self_name_free(cmd)
    xl = x.lower()
    plans = {}
    for name, rx in _RX.items():
        w, why = 0.0, []
        for r, wt in rx["cmd"]:
            m = r.search(x)
            if m:
                cw, _ = CX.judge(x, m.start(), m.end(), name)
                if cw <= 0 and not CX._NEG_BEFORE.search(x[:m.start()][-40:].lower()):
                    cw = 0.4               # e.g. "whenever a creature an opponent controls dies": related, but weaker
                if cw > 0:
                    w += wt * cw
                    why.append(m.group(0).strip())
        if w:
            plans[name] = [w, why]

    notes, adjust = [], {}
    cmc = cmd.get("cmc") or 0
    pw, tg = num(cmd.get("power")), num(cmd.get("toughness"))
    kws = set(cmd.get("keywords") or [])
    kws |= {k.title() for k in ("flying", "trample", "menace", "lifelink", "deathtouch", "haste", "vigilance", "double strike",
                                "first strike", "hexproof", "indestructible", "ward") if re.search(r"(?:^|\n|, )%s\b" % k, xl)}
    evasive = bool(kws & EVASION) or bool(_UNBLOCKABLE.search(x))

    # --- stats-driven plans
    is_creature = "Creature" in (cmd.get("type_line") or "")
    if is_creature and cmc <= 6 and ((pw >= 5 and evasive) or (pw >= 3 and "Double Strike" in kws)
                                      or (pw >= 4 and _UNBLOCKABLE.search(x))):
        plans.setdefault("Voltron / equipment & auras", [0, []])
        plans["Voltron / equipment & auras"][0] += 2 + (1 if pw >= 6 else 0)
        plans["Voltron / equipment & auras"][1].append(f"{int(pw)}/{int(tg)} with {', '.join(sorted(kws & (EVASION | {'Double Strike'})) or ['evasion'])}")
        notes.append(f"Hits hard on its own ({int(pw)} power, evasive): commander damage is a real win route.")
    if "Lifelink" in kws and "Lifegain" not in plans:
        plans.setdefault("Lifegain", [0, []])
        plans["Lifegain"][0] += 1.5
        plans["Lifegain"][1].append("lifelink")
    if "Deathtouch" in kws and re.search(r"deals? \w+ damage|fights?", xl):
        notes.append("Deathtouch plus damage/fight text: every point of damage it deals is removal.")

    # --- what the commander already does for you (so the 99 needs less of it)
    if _REPEAT_DRAW.search(x):
        adjust["draw"] = adjust.get("draw", 0) - 2
        notes.append("Draws cards by itself, so the deck runs a little less card draw.")
    if _REPEAT_RAMP.search(x):
        adjust["ramp"] = adjust.get("ramp", 0) - 2
        notes.append("Makes mana or lands by itself, so the deck runs a little less ramp.")
    if _REPEAT_REMOVAL.search(x):
        adjust["removal"] = adjust.get("removal", 0) - 1
        notes.append("Is repeatable removal on its own.")

    # --- mana
    if cmc >= 6:
        adjust["ramp"] = adjust.get("ramp", 0) + 2
        notes.append(f"Costs {int(cmc)} mana: extra ramp so it lands on time.")
    elif cmc >= 5:
        adjust["ramp"] = adjust.get("ramp", 0) + 1
    elif cmc <= 2 and is_creature:
        notes.append(f"Only {int(cmc)} mana: comes down early, so cheap support cards matter more than big ramp.")
        adjust["ramp"] = adjust.get("ramp", 0) - 1
    if is_creature and re.search(r"^[^:\n]{0,30}\{T\}[^:\n]{0,30}:", x, re.M):
        plans.setdefault("Tap & untap abilities", [0, []])
        plans["Tap & untap abilities"][0] += 2.5
        plans["Tap & untap abilities"][1].append("has a {T} ability (untap effects double it)")
    acts = _ACT_COST.findall(x)
    if acts:
        notes.append("Has a mana-hungry activated ability: extra mana turns into extra value.")
        adjust["ramp"] = adjust.get("ramp", 0) + 1

    # --- does the plan run THROUGH the commander? then protect it
    anchored = bool(_SELF_ANCHORED.search(x)) or "Voltron / equipment & auras" in plans
    if anchored and is_creature:
        prot = 4 if "Voltron / equipment & auras" in plans else 3
        if _PROTECT_KW.search(xl) or kws & {"Hexproof", "Indestructible", "Ward"}:
            prot -= 1
            notes.append("Has built-in protection, so it needs fewer protection spells.")
        elif tg <= 2:
            prot += 1
            notes.append(f"Fragile ({int(tg)} toughness) and the deck runs through it: extra protection.")
        adjust["protection"] = prot

    # --- creature count suggestion: average of what the top plans want, weighted
    ordered = sorted(([n, w, why] for n, (w, why) in plans.items()), key=lambda r: -r[1])
    if ordered:
        top_w = ordered[0][1]
        ordered = [r for r in ordered if r[1] >= max(1.5, top_w * 0.25)]   # drop faint, incidental matches
    wants = [(PLANS[n].get("creatures"), w) for n, w, _ in ordered[:3] if PLANS[n].get("creatures")]
    creatures = round(sum(c * w for c, w in wants) / sum(w for _, w in wants)) if wants else None
    if any(n.startswith("Spellslinger") for n, _, _ in ordered[:1]):
        adjust["counter"] = adjust.get("counter", 0) + 1

    if not ordered and is_creature and evasive and pw >= 3:
        ordered = [["Combat / attack triggers", 2.0, [f"{int(pw)}-power evasive body"]]]
    styles = (vibe or {}).get("styles")
    routes = [dict(name=n, base=round(min(w, 10), 1), style=PLAN_STYLE.get(n), why=sorted(set(why), key=len)[:3]) for n, w, why in ordered[:8]]
    for r in routes:
        r["weight"] = _vibe_shift(r["base"], r["style"], styles)
    # ONE recognizable plan beats the vibe: if the commander only does one thing (or one plan is 1.8x the next),
    # that plan leads whatever the vibe; the vibe only re-orders the supporting plans.
    by_base = sorted(routes, key=lambda r: -r["base"])
    dominant = by_base[0] if by_base and (len(by_base) == 1 or by_base[0]["base"] >= 1.8 * by_base[1]["base"]) else None
    if dominant:
        others = [r["weight"] for r in routes if r is not dominant]
        dominant["weight"] = round(max(dominant["weight"], dominant["base"], (max(others) + 0.1) if others else 0), 2)
        dominant["clear"] = True
    routes.sort(key=lambda r: -r["weight"])
    out = [dict(name=r["name"], weight=r["weight"], why=r["why"], style=r["style"]) for r in routes[:6]]
    lead_note = ""
    if dominant:
        pref = max(styles, key=styles.get) if styles else None
        if pref and pref != dominant.get("style") and styles.get(dominant.get("style") or "", 0) < styles[pref]:
            lead_note = (f"This commander has one clear game plan, {dominant['name']}, so it leads even though your vibe "
                         f"prefers {pref} plans; the vibe shapes the supporting cards instead.")
        else:
            lead_note = f"This commander has one clear game plan, {dominant['name']}, and the deck is built around it."
    elif routes and styles:
        natural = max(routes, key=lambda r: r["base"])
        if natural["name"] != routes[0]["name"]:
            lead_note = (f"This commander can be played several ways. Its most obvious plan is {natural['name']}, "
                         f"but your vibe leads with {routes[0]['name']} ({routes[0]['style']}); the others stay in as support.")
        else:
            lead_note = f"Your vibe agrees with the commander's most obvious plan: {routes[0]['name']}."

    # ability-by-ability interactions (abilities.py): enablers / fuel / payoffs / doublers for each ability
    parsed = AB.parse(cmd)
    inter = AB.interactions(parsed)
    for p in inter:
        p["base"] = p["weight"]
        p["weight"] = _vibe_shift(p["weight"], p["style"], styles)
    role = commander_role(cmd, routes)
    if role["role"] == "finisher":
        fin = [ln.lower() for ln in role["lines"]]
        for p in inter:
            # interactions that come from the winning ability: enable it harder (bigger weight, more cards)
            # only cards that MAKE the winning ability happen (trigger it, pay its costs) get the finisher boost;
            # cards that merely use what it produces (lifegain payoffs) are nice, not essential
            if p["kind"] in ("enabler", "fuel", "keyword") and (any(w.rstrip(".").lower()[:40] in ln for w in p["why"] for ln in fin) or not fin):
                p["weight"] = round(min(p["weight"] * 1.6, 10), 2)
                p["finisher"] = True
        if is_creature:
            adjust["protection"] = min(adjust.get("protection", 0) + 2, 6)
            notes.append("Its ability wins the game, so it gets extra protection: losing it means losing the win condition.")
    inter.sort(key=lambda p: -p["weight"])
    # which of your cards actually COUNT for it ("creatures you control with power 2 or less", "with defender"...)
    import conditions as CN
    conds = CN.rules(cmd, role["lines"])
    if conds:
        notes.append("Only some cards count for it: " + "; ".join(CN.describe(r) for r in conds) +
                     ". Cards that count (or make tokens that do) are favored; cards that push yours out of the count are avoided.")
        if CN.counts_creatures(conds):
            creatures = max(creatures or 0, 30)
    # the strategic game plan: what multiplies it, what it needs, what beats it (strategy.py)
    import strategy as ST
    strat = ST.plan(cmd, role, conds, parsed, routes)
    return dict(plans=out, routes=routes, lead_note=lead_note, interactions=inter[:8], role=role, conditions=conds,
                strategy=strat,
                abilities=[dict(kind=a["kind"], text=a["text"], event=a["event"][1] if a["event"] else None,
                                costs=[c[1] for c in a["costs"]], outputs=[o[1] for o in a["outputs"]], style=a["style"])
                           for a in parsed],
                adjust=adjust, creatures=creatures, notes=notes,
                typal=typal_refs(cmd, all_types) if all_types else [], evasive=evasive, power=pw, toughness=tg, cmc=cmc)


# ------------------------------------------------------------------------------------------------
# Per-card plan matching (cached once per card; the commander only changes the weights)
# ------------------------------------------------------------------------------------------------
_card_cache = {}


def card_hits(card):
    """{plan: strength 0..3} for every plan this card feeds or pays off, plus {plan: True} hate flags."""
    key = card["name"]
    if key in _card_cache:
        return _card_cache[key]
    x = _self_name_free(card)
    tl = card.get("type_line") or ""
    hits, hate, filtered = {}, set(), {}
    for name, rx in _RX.items():
        s, why = 0.0, []
        if rx["feed"]:
            v, f = CX.weigh_matches(rx["feed"], x, name)      # fast keyword pass, then the context filter
            s += v
            why += f
        if rx["pay"]:
            v, f = CX.weigh_matches(rx["pay"], x, name)
            s += v * 1.2
            why += f
        if rx["types"] and rx["types"].search(tl):
            s += 1
        if s:
            hits[name] = min(s, 3)
        if why:
            filtered[name] = why
        if rx["hate"] and rx["hate"].search(x):
            hate.add(name)
    _card_cache[key] = (hits, hate)
    _filtered_cache[key] = filtered
    return hits, hate


_filtered_cache = {}


def filtered_hits(card):
    """{plan: ["'keyword': reason", ...]} for hits the context filter discounted (for reports and tests)."""
    card_hits(card)
    return _filtered_cache.get(card["name"], {})


_dyn_rx = {}
_dyn_cache = {}


def _compiled(patterns):
    key = tuple(patterns)
    if key not in _dyn_rx:
        _dyn_rx[key] = re.compile("|".join(patterns), re.I) if patterns else None
    return _dyn_rx[key]


def interaction_hits(card, p):
    """Strength 0..3 that this card enables / fuels / pays off one of the commander's specific abilities.
    Same context filter as everything else: the card must do the thing for YOU."""
    key = (card["name"], p["name"], tuple(p.get("feed") or ()), tuple(p.get("pay") or ()), p.get("types"))
    if key in _dyn_cache:
        return _dyn_cache[key]
    x = _self_name_free(card)
    ctx_plan = "Burn / drain the table" if p.get("style") == "punish" and "drain" in p["name"] else p["name"]
    s = 0.0
    fr = _compiled(p.get("feed") or [])
    if fr:
        s += CX.weigh_matches(fr, x, ctx_plan)[0]
    pr = _compiled(p.get("pay") or [])
    if pr:
        s += CX.weigh_matches(pr, x, ctx_plan)[0] * 1.2
    if p.get("types") and re.search(p["types"], card.get("type_line") or ""):
        s += 1
    _dyn_cache[key] = min(s, 3)
    return _dyn_cache[key]


def plan_hits(card, plan):
    """Strength for either a strategy plan (by name) or an ability interaction plan (has its own patterns)."""
    if "feed" in plan or "pay" in plan:
        return interaction_hits(card, plan)
    return card_hits(card)[0].get(plan["name"], 0)


SYN_CAP = 80.0     # high enough that a card covering several needs still ranks above one covering a single need
NEED_NAMES = dict(counts="counts for it", enabler="triggers its abilities", fuel="pays its costs", payoff="uses what it makes",
                  plan="fits its game plan", typal="a type it names")


def synergy(card, profile, tribe_words=()):
    """How much this card helps THIS commander: its strategies AND its specific abilities.
    Returns (score 0..~60, reason or '').

    Needs model: every way a card can help the commander is a different NEED (it counts for the commander's
    condition, triggers its abilities, pays its costs, uses what it makes, fits its game plan, is a type it names).
    A card that covers SEVERAL needs at once is worth more than the sum of single-purpose cards (it saves slots),
    so the total is multiplied by 1 + 0.25 per extra need (max x1.75). The needs covered are stored on the card
    (card['_needs']) for the report and for compounding with deck roles (build_deck.prepare)."""
    hits, hate = card_hits(card)
    s, best, best_v = 0.0, "", 0.0
    needs = set()
    for p in profile["plans"]:
        h = hits.get(p["name"])
        if h:
            v = h * p["weight"] * 2.0
            s += v
            if h >= 1:
                needs.add("plan")
            if v > best_v:
                best, best_v = p["name"], v
        if p["name"] in hate:
            s -= 6 * p["weight"]
    for p in profile.get("interactions", []):
        h = interaction_hits(card, p)
        if h:
            v = h * p["weight"] * 1.6
            s += v
            if h >= 1:
                needs.add({"keyword": "enabler"}.get(p["kind"], p["kind"]))
            if v > best_v:
                best, best_v = p["name"], v
    # creature types the commander cares about (beyond the main tribe system)
    tl = card.get("type_line") or ""
    sub = tl.split("—", 1)[1] if "—" in tl else ""
    for t in tribe_words:
        if re.search(r"\b%s\b" % re.escape(t), sub) or ("Creature" in tl and "changeling" in (card.get("text") or "").lower()):
            s += 12
            needs.add("typal")
            if best_v < 12:
                best, best_v = f"{t} (named by your commander)", 12
            break
    if profile.get("conditions"):
        import conditions as CN
        d, why = CN.score(card, profile["conditions"])
        s += d
        if d > 0:
            needs.add("counts")
            if d >= best_v:
                best, best_v = f"{why}", d
        elif d < 0 and s <= 0:
            best = f"doesn't fit your commander: {why}"
    card["_avoid"] = ""
    if profile.get("strategy"):
        import strategy as ST
        d, met, bad = ST.judge(card, profile["strategy"])
        s += d
        needs.update(met)
        if met and d >= best_v:
            best, best_v = ST.LABELS.get(met[0], met[0]), d
        if bad:
            card["_avoid"] = bad
            best = f"works against the game plan: {bad}"
    if s > 0 and len(needs) >= 2:
        s *= min(1 + 0.25 * (len(needs) - 1), 1.75)
    card["_needs"] = sorted(needs)
    return min(s, SYN_CAP), best


# ------------------------------------------------------------------------------------------------
# Commander personality -> which vibe it naturally is
# ------------------------------------------------------------------------------------------------
TRAITS = {
    "mean": [r"each opponent (?:loses|sacrifices|discards|mills|exiles)", r"(?:an|each|target) opponent (?:sacrifices|discards)", r"(?:opponents|players) can't",
             r"\bgain control of\b", r"under your control[^.]{0,40}(?:opponent|you don't own)|(?:opponent|you don't own)[^.]{0,60}under your control",
             r"(?:destroy|exile) (?:target|each|all) (?:\w+ )?(?:creatures?|permanents?|artifacts?|enchantments?)(?: (?:an opponent|your opponents) controls?)?",
             r"counter target", r"deals? \w+ damage to (?:each opponent|each player|target player|target opponent|any target)",
             r"(?:target|each) (?:player|opponent) (?:discards|sacrifices|loses)", r"opponents? (?:each )?lose(?:s)? \w+ life",
             r"whenever an opponent (?:casts|draws|activates|searches|sacrifices|discards|loses)", r"spells (?:your )?opponents cast cost",
             r"\btap target\b", r"(?:creatures|permanents) (?:your opponents control|an opponent controls)", r"-\d/-\d|-1/-1 counter",
             r"can't (?:attack|block|untap|gain life)", r"\bskips?\b", r"cards? (?:your opponents|an opponent) owns?"],
    "stax": [r"cost[s]? \{\d+\} more", r"(?:players?|opponents?|each opponent)[^.]{0,30}can't (?:cast|search|activate|draw|gain)",
             r"can't cast (?:more than|spells)", r"(?:don't|doesn't) untap", r"(?:opponents?|your opponents)[^.]{0,30}enter(?:s)?(?: the battlefield)? tapped",
             r"each player can't", r"\bskip", r"nonbasic lands", r"can't (?:attack you|be cast)", r"(?:only|no more than) one"],
    "chaos": [r"flip a coin", r"\broll\b", r"\brandom", r"exchange", r"\bcascade\b", r"\bgoad", r"gain control", r"each player shuffles",
              r"discards? (?:their|that) hand", r"\bswap\b", r"chaos", r"cast (?:it|that card|them) without paying", r"you don't own",
              r"top card of (?:each|target) (?:player's|opponent's) library"],
    "hug": [r"each player (?:may )?(?:draws?|gains?|puts?|creates?|searches|untaps)", r"each opponent (?:may )?(?:draws?|gains?|creates?)",
            r"\bmonarch\b", r"\bvote\b|will of the council|council's dilemma", r"target (?:opponent|player) (?:draws|gains|creates|may)", r"\bdonate\b",
            r"gains? control of (?:target|that) (?:\w+ )?(?:you control|permanent you own)"],
    "gentle": [r"gain \w+ life", r"create[^.]{0,40}token", r"\+1/\+1 counter", r"\blandfall\b", r"whenever a land", r"enchantments? you control",
               r"search your library for a basic land", r"you control get \+"],
    "pressure": [r"whenever[^.]{0,40}\battacks?\b", r"combat damage to (?:a player|an opponent|one or more players)", r"additional combat",
                 r"\bdouble strike\b", r"\bhaste\b", r"onto the battlefield (?:tapped and )?attacking", r"\bmenace\b|can't be blocked",
                 r"creatures you control get \+\d"],
    "engine": [r"draw (?:a|two|that many) cards?", r"\buntap\b", r"copy", r"additional combat", r"costs? \{\d\} less", r"extra turn",
               r"without paying its mana cost", r"\badd \{", r"double"],
}
_TRX = {k: [re.compile(r, re.I) for r in v] for k, v in TRAITS.items()}


@lru_cache(maxsize=None)
def _traits_for(name, text, rank):
    t = {k: min(sum(1 for r in rx if r.search(text)) / 1.5, 1.0) for k, rx in _TRX.items()}
    # popularity = proven power. EDHREC rank 1-300 -> ~1.0, 3000+ -> ~0.
    t["power"] = 0.0 if not rank else max(0.0, min(1.0, 1.15 - 0.35 * __import__("math").log10(rank + 1)))
    return t


def traits(cmd):
    return _traits_for(cmd["name"], _self_name_free(cmd), cmd.get("rank") or 0)


def vibe_fit(cmd, vibes):
    """{vibe_label: 0..1} how naturally this commander plays each vibe, plus the best one."""
    t = traits(cmd)
    raw = {label: sum(w * t.get(k, 0) for k, w in v["personality"].items()) + v.get("bias", 0) for label, v in vibes.items()}
    lo, hi = min(raw.values()), max(raw.values())
    span = (hi - lo) or 1.0
    fit = {k: round((r - lo) / span, 3) for k, r in raw.items()}
    best = max(raw, key=raw.get)
    return fit, best, raw


def fit_label(fit_value, is_best):
    if is_best or fit_value >= 0.8:
        return "natural fit"
    if fit_value >= 0.45:
        return "workable"
    return "stretch"


# Mechanical-theme dropdown label -> the analyzer plan(s) that define it. Themes are matched with the SAME
# context-aware patterns as the commander's plans (so "target player sacrifices" isn't an aristocrats card).
MECH_TO_PLANS = {
    "+1/+1 Counters": ["+1/+1 counters"],
    "Tokens & Go-Wide": ["Tokens / go wide"],
    "Sacrifice & Aristocrats": ["Sacrifice / aristocrats"],
    "Graveyard & Reanimator": ["Graveyard / self-mill / reanimation"],
    "Spellslinger": ["Spellslinger (instants & sorceries)"],
    "Artifacts": ["Artifacts"],
    "Enchantress": ["Enchantments"],
    "Equipment & Auras (Voltron)": ["Voltron / equipment & auras"],
    "Lifegain": ["Lifegain"],
    "Landfall & Lands Matter": ["Lands / landfall / ramp"],
    "Blink & Enter-the-Battlefield": ["Enter-the-battlefield / blink"],
    "Treasure, Clues & Food": ["Treasure / clues / food"],
    "Combat & Aggro": ["Combat / attack triggers"],
    "Big Creatures & Stompy": ["Big creatures / power matters"],
    "Group Slug & Burn": ["Burn / drain the table"],
    "Group Hug & Politics": ["Politics / goad / monarch"],
    "Card Draw & Wheels": ["Card draw engine / wheels"],
    "Discard & Madness": ["Discard / madness"],
    "Cast from Exile & Impulse Draw": ["Cast from exile / impulse draw"],
    "Steal Your Opponents' Stuff": ["Steal & copy opponents' stuff"],
    "Mill Your Opponents": ["Mill your opponents"],
    "Poison & Proliferate": ["Poison / proliferate"],
    "Superfriends (Planeswalkers)": ["Superfriends (planeswalkers)"],
    "Vehicles": ["Vehicles"],
    "Clones & Copies": ["Clones & copies"],
    "Big Spells & X Costs": ["Big spells / X costs"],
    "Flash & Instant-Speed": ["Flash / opponents' turns"],
    "Legends Matter": ["Legends / historic"],
    "Tap & Untap Engines": ["Tap & untap abilities"],
    "Dice & Coin Flips": ["Dice & coin flips"],
}


def plan_for_theme(mech):
    """The game plan that matches a mechanical-theme dropdown (used when the commander has no engine of its own)."""
    return (MECH_TO_PLANS.get(mech) or [None])[0]


def theme_strength(card, mech):
    """0..3: how strongly a card fits a mechanical theme (context-filtered, same rules as commander plans)."""
    hits = card_hits(card)[0]
    return max((hits.get(p, 0) for p in MECH_TO_PLANS.get(mech, [])), default=0)
