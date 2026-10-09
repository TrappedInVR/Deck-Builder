"""Single source of truth for the dropdown menus and the matching rules behind them.

The workflow forms (.github/workflows/build.yml and suggest.yml) are GENERATED from this file by
`python src/make_workflows.py`, so the labels in the forms can never drift from what the code expects.

Everything here is plain regex/keyword matching over Scryfall text (name, type line, rules text and
flavor text). It is deliberately simple and free. It finds cards that FIT a theme; it does not
understand deep synergies, so treat results as a strong draft."""
import re

ANY = "Any (no preference)"

# ----------------------------------------------------------------------------------------------
# 1. Deck vibe: personality, NOT power level. Every vibe builds a legal Bracket 3 deck:
#    at most 3 Game Changers, no mass land denial, no chained extra turns.
#    The vibe decides how the deck treats the table (generous, chaotic, mean...) and which commanders
#    fit it naturally. Mean vibes pick commanders that are mean by nature.
# ----------------------------------------------------------------------------------------------
# quotas      = minimum counts of each role the builder tries to include
# slots       = how many cards of a "feel" (grouphug, chaos, stax, punish...) to reserve
# pop         = weight on EDHREC popularity (higher = more proven staples, less personality)
# budget      = USD for the 99's nonland cards (lands never count; see lands.py)
# avoid       = things this vibe never includes (same words you can type in the Avoid box)
# combo_max   = highest Commander Spellbook combo tag allowed (E/C/O = casual, P/S = Bracket 3). R is never allowed.
# combo_slots = how many complete combos the builder actively adds (0 = only keep ones that happen naturally)
# personality = which commander traits fit this vibe (see analyze.TRAITS): mean, stax, chaos, hug,
#               gentle, engine, power. Negative = commanders like that are a poor match.
BRACKET = 3
_B3 = dict(bracket=BRACKET, max_gc=3)
VIBES = {
    "Relaxed Casual": dict(_B3,
        combo_max="O", combo_slots=0,
        gc_target=0, max_extra_turns=0, max_tutors=0, budget=500, lands=38,
        quotas=dict(ramp=10, draw=8, removal=5, counter=1, sweeper=1, recursion=2), slots={}, pop=0.7,
        avoid={"stax", "extra turns", "tutors", "infect", "land destruction", "theft", "discard"},
        personality=dict(gentle=1.4, hug=0.4, engine=0.2, mean=-1.5, pressure=-0.4, stax=-2.0, chaos=-0.3),
        blurb="Build your board and enjoy it. Light interaction, no lockouts, games go long and everyone gets to play."),
    "Social Table: Fun for Everyone": dict(_B3,
        combo_max="O", combo_slots=0,
        gc_target=0, max_extra_turns=0, max_tutors=0, budget=500, lands=37,
        quotas=dict(ramp=9, draw=8, removal=4, counter=0, sweeper=1, recursion=2),
        slots=dict(grouphug=8, politics=4), pop=0.5,
        avoid={"stax", "extra turns", "tutors", "infect", "land destruction", "discard"},
        personality=dict(hug=2.2, gentle=0.6, chaos=0.4, mean=-1.2, stax=-2.0),
        blurb="Generous, symmetrical and political cards that give the whole table something to do."),
    "Competitive-Casual: Win and Have a Good Time": dict(_B3,
        combo_max="P", combo_slots=1,
        gc_target=2, max_extra_turns=1, max_tutors=2, budget=500, lands=37,
        quotas=dict(ramp=10, draw=9, removal=7, counter=3, sweeper=2, recursion=2), slots={}, pop=1.0,
        avoid={"stax", "infect", "land destruction"},
        personality=dict(power=0.9, engine=0.6, gentle=0.3, pressure=0.3, mean=0.2, stax=-0.8, chaos=-0.2), bias=0.3,
        blurb="A real plan to win with real interaction, but nothing that locks players out of the game."),
    "Chaos & Memes: Chaotic Fun": dict(_B3,
        combo_max="O", combo_slots=1,
        gc_target=1, max_extra_turns=1, max_tutors=0, budget=500, lands=37,
        quotas=dict(ramp=9, draw=7, removal=5, counter=1, sweeper=1, recursion=2), slots=dict(chaos=14), pop=0.5,
        avoid={"stax", "infect", "land destruction", "tutors"},
        personality=dict(chaos=2.6, hug=0.5, gentle=0.1, power=-0.3, stax=-1.0),
        blurb="Coin flips, swaps, wheels and wild effects. Unpredictable, but still a working deck."),
    "Table Threat: Scary to Play Against": dict(_B3,
        combo_max="S", combo_slots=1,
        gc_target=3, max_extra_turns=1, max_tutors=3, budget=500, lands=36,
        quotas=dict(ramp=11, draw=9, removal=8, counter=3, sweeper=2, recursion=2), slots=dict(punish=6), pop=1.2,
        avoid={"infect", "land destruction"},
        personality=dict(mean=1.6, pressure=0.7, power=1.0, engine=0.4, hug=-0.8, gentle=-0.3),
        blurb="Punishing and relentless: the commander makes opponents pay every turn, backed by heavy interaction."),
    "Hostile Control: Stax & Hate": dict(_B3,
        combo_max="S", combo_slots=1,
        gc_target=3, max_extra_turns=0, max_tutors=2, budget=500, lands=36,
        quotas=dict(ramp=10, draw=9, removal=8, counter=5, sweeper=3, recursion=1), slots=dict(stax=7, punish=4), pop=1.0,
        avoid={"infect", "land destruction"},
        personality=dict(stax=2.4, mean=1.5, power=0.3, hug=-1.2, gentle=-0.8),
        blurb="Taxes, restrictions, counterspells and sweepers that make everyone else's turn miserable. "
              "Still Bracket 3: no mass land denial, ever."),
    "Optimized Menace: Max-Power Bracket 3": dict(_B3,
        combo_max="S", combo_slots=2,
        gc_target=3, max_extra_turns=1, max_tutors=4, budget=500, lands=35,
        quotas=dict(ramp=12, draw=10, removal=8, counter=4, sweeper=2, recursion=2), slots=dict(fastmana=3), pop=1.5,
        avoid=set(),
        personality=dict(power=2.0, engine=1.0, mean=0.6, pressure=0.3, gentle=-0.2, chaos=-0.5),
        blurb="As strong as Bracket 3 allows: 3 Game Changers, efficient staples and tutors. "
              "Check two-card combos yourself (Commander Spellbook) to stay in Bracket 3."),
}
VIBE_LABELS = list(VIBES)
DEFAULT_VIBE = "Competitive-Casual: Win and Have a Good Time"
# Labels from older versions of the form still work (e.g. a saved workflow URL)
OLD_VIBE_NAMES = {
    "Relaxed Casual (Bracket 2)": "Relaxed Casual",
    "Social Table: Fun for Everyone (Bracket 2)": "Social Table: Fun for Everyone",
    "Competitive-Casual: Win and Have a Good Time (Bracket 3)": DEFAULT_VIBE,
    "Chaos & Memes: Chaotic Fun (Bracket 3)": "Chaos & Memes: Chaotic Fun",
    "Table Threat: Scary to Play Against (Bracket 3)": "Table Threat: Scary to Play Against",
    "Hostile Control: Stax & Hate (Bracket 4)": "Hostile Control: Stax & Hate",
    "Optimized Menace: High Power (Bracket 4)": "Optimized Menace: Max-Power Bracket 3",
}
# Vibes where the commander itself should be mean (auto-pick only offers commanders that fit)
MEAN_VIBES = {"Table Threat: Scary to Play Against", "Hostile Control: Stax & Hate"}

# ----------------------------------------------------------------------------------------------
# 2. Colors (exact color identity). Letters are always in WUBRG order.
# ----------------------------------------------------------------------------------------------
_COLOR_NAMES = [
    ("Mono-White", "W"), ("Mono-Blue", "U"), ("Mono-Black", "B"), ("Mono-Red", "R"), ("Mono-Green", "G"),
    ("Azorius", "WU"), ("Dimir", "UB"), ("Rakdos", "BR"), ("Gruul", "RG"), ("Selesnya", "WG"),
    ("Orzhov", "WB"), ("Izzet", "UR"), ("Golgari", "BG"), ("Boros", "WR"), ("Simic", "UG"),
    ("Bant", "WUG"), ("Esper", "WUB"), ("Grixis", "UBR"), ("Jund", "BRG"), ("Naya", "WRG"),
    ("Abzan", "WBG"), ("Jeskai", "WUR"), ("Sultai", "UBG"), ("Mardu", "WBR"), ("Temur", "URG"),
    ("Yore-Tiller (no green)", "WUBR"), ("Glint-Eye (no white)", "UBRG"), ("Dune-Brood (no blue)", "WBRG"),
    ("Ink-Treader (no black)", "WURG"), ("Witch-Maw (no red)", "WUBG"),
    ("Five-Color", "WUBRG"), ("Colorless", "C"),
]
COLOR_LABELS = [ANY] + [f"{n} ({l})" for n, l in _COLOR_NAMES]
# Random mode skips Any and Colorless (colorless decks are weak and not "functional but weird").
RANDOM_COLOR_LABELS = [f"{n} ({l})" for n, l in _COLOR_NAMES if l != "C"]
_COLOR_WEIGHTS = {1: 3, 2: 5, 3: 5, 4: 1, 5: 1}      # favor two and three color decks


def parse_colors(label):
    """'Simic (UG)' -> {'U','G'}; 'Colorless (C)' -> set(); Any/blank -> None (no filter)."""
    if not label or label.strip() == ANY:
        return None
    m = re.search(r"\(([WUBRGC]+)\)\s*$", label)
    if not m:
        raise SystemExit(f"Unrecognized colors option: {label!r}")
    return set() if m.group(1) == "C" else set(m.group(1))


def random_color_label(rng):
    labels = RANDOM_COLOR_LABELS
    weights = [_COLOR_WEIGHTS[len(parse_colors(l))] for l in labels]
    return rng.choices(labels, weights=weights, k=1)[0]


# ----------------------------------------------------------------------------------------------
# 3. Mechanical themes. text = regexes over rules text; types = regexes over the type line.
# ----------------------------------------------------------------------------------------------
_MECH = {
    "+1/+1 Counters": dict(text=[r"\+1/\+1 counter", r"\bproliferate\b", r"\bevolve\b", r"\bbolster\b",
                                 r"\badapt\b", r"\bsupport \d", r"\bmentor\b", r"\bmodular\b", r"\bundying\b"]),
    "Spellslinger": dict(text=[r"instant or sorcery", r"\bmagecraft\b", r"\bprowess\b",
                               r"copy target (?:instant|sorcery)", r"whenever you cast a noncreature",
                               r"\bstorm\b", r"\bflashback\b", r"\bcast (?:an? )?(?:instant|sorcery)"],
                         types=[r"\b(?:Instant|Sorcery)\b"]),
    "Tokens & Go-Wide": dict(text=[r"create (?:a|an|one|two|three|four|x|\d+|that many)[^.]{0,40}token",
                                   r"\bpopulate\b", r"tokens? you control", r"creatures you control get \+",
                                   r"\bamass\b", r"\bfabricate\b", r"\bconvoke\b"]),
    "Sacrifice & Aristocrats": dict(text=[r"sacrifice (?:a|another|an|two|x)[^.]{0,30}(?:creature|artifact|permanent)",
                                          r"whenever (?:a|another)[^.]{0,30}creature[^.]{0,20}dies",
                                          r"whenever you sacrifice", r"\bexploit\b", r"each opponent loses"]),
    "Graveyard & Reanimator": dict(text=[r"from your graveyard", r"\bmill\b", r"\bdelirium\b", r"\bunearth\b",
                                         r"\bescape\b", r"\bdredge\b", r"\bembalm\b", r"\bdisturb\b",
                                         r"creature card from a graveyard onto the battlefield"]),
    "Artifacts": dict(text=[r"artifacts? you control", r"\baffinity for artifacts\b", r"\bimprovise\b",
                            r"whenever an artifact", r"\bmetalcraft\b", r"\bfabricate\b"],
                      types=[r"\bArtifact\b"]),
    "Enchantress": dict(text=[r"enchantments? you control", r"\bconstellation\b", r"whenever an enchantment",
                              r"\bbestow\b", r"\bsaga\b"],
                        types=[r"\bEnchantment\b"]),
    "Lifegain": dict(text=[r"gains? \w+ life", r"\blifelink\b", r"whenever you gain life", r"\bextort\b"]),
    "Landfall & Lands Matter": dict(text=[r"\blandfall\b", r"whenever a land enters", r"additional land",
                                          r"put (?:a|up to \w+) land cards? (?:from|onto)",
                                          r"search your library for (?:a|up to \w+) (?:basic )?land",
                                          r"lands you control"]),
    "Blink & Value": dict(text=[r"exile (?:up to \w+ )?(?:another )?(?:target )?(?:nontoken )?creatures? you control[^.]{0,40}return",
                                r"\bblink\b", r"\bflicker\b", r"whenever (?:a|another)[^.]{0,30}enters the battlefield under your control",
                                r"\bsoulbond\b"]),
    "Equipment & Auras (Voltron)": dict(text=[r"\bequipped creature\b", r"\benchanted creature\b", r"\battach\b", r"\bequip\b"],
                                        types=[r"\bEquipment\b", r"\bAura\b"]),
    "Treasure & Clues": dict(text=[r"\btreasure\b", r"\bclue\b", r"\bfood\b", r"\bblood token\b", r"\bincubate\b"]),
    "Group Slug & Burn": dict(text=[r"deals? \w+ damage to each opponent", r"each opponent loses \w+ life",
                                    r"deals? \w+ damage to each (?:other )?(?:creature|player|opponent)",
                                    r"whenever an opponent[^.]{0,30}(?:takes|is dealt) damage"]),
    "Group Hug & Politics": dict(text=[r"each player (?:may )?(?:draws?|gains?|puts?|searches|untaps)",
                                       r"\bmonarch\b", r"\bgoad\b", r"\bvote\b", r"will of the council",
                                       r"council's dilemma", r"target opponent (?:chooses|gains|draws)", r"\bdonate\b"]),
    "Combat & Aggro": dict(text=[r"whenever [^.]{0,40}attacks", r"\bhaste\b", r"\bdouble strike\b",
                                 r"additional combat phase", r"\bbattle cry\b", r"\bmyriad\b", r"\bexalted\b", r"\bmenace\b"]),
    "Draw, Wheels & Discard": dict(text=[r"each player discards (?:their|all) hand", r"\bwheel\b", r"whenever you draw",
                                         r"draw (?:two|three|x|that many) cards", r"\bmadness\b", r"\bcycling\b"]),
    "Big Creatures & Stompy": dict(text=[r"power (?:\d+|x) or greater", r"\btrample\b", r"\bfights?\b",
                                         r"\bmonstrosity\b", r"greatest power"]),
}
MECH_LABELS = [ANY] + list(_MECH)
# Typical creature counts differ by plan; a Spellslinger deck with 38 creatures is not a Spellslinger deck.
MECH_MAX_CREATURES = {"Spellslinger": 16, "Enchantress": 20, "Equipment & Auras (Voltron)": 22,
                      "Artifacts": 26, "Group Hug & Politics": 24, "Draw, Wheels & Discard": 24}
DEFAULT_MAX_CREATURES, TRIBAL_MAX_CREATURES = 30, 40

# ----------------------------------------------------------------------------------------------
# 4. Narrative themes: judged by creature types, words in the card NAME, and words in rules/flavor text.
#    Subtype hit = 3 points, name word = 2, text/flavor word = 1. A card "fits" at 2+ points.
# ----------------------------------------------------------------------------------------------
_NARR = {
    "Pirates & the High Seas": dict(types=["Pirate", "Kraken", "Octopus", "Crab", "Fish", "Leviathan", "Serpent", "Siren", "Merfolk"],
                                    words=["sea", "ship", "tide", "treasure", "ocean", "reef", "sail", "captain", "plunder", "cove", "wave", "harbor"]),
    "Undead & Gothic Horror": dict(types=["Zombie", "Skeleton", "Vampire", "Spirit", "Ghoul", "Wraith", "Horror", "Specter"],
                                   words=["crypt", "grave", "tomb", "bone", "undead", "haunt", "curse", "coffin", "blood", "ghost", "dread"]),
    "Dragons & Fire": dict(types=["Dragon", "Drake", "Wyrm", "Phoenix", "Elemental"],
                           words=["flame", "ember", "fire", "inferno", "scale", "hoard", "blaze", "burn", "molten", "smoke"]),
    "Enchanted Forest & Nature": dict(types=["Elf", "Druid", "Treefolk", "Dryad", "Fungus", "Plant", "Saproling", "Squirrel", "Ent"],
                                      words=["forest", "grove", "bloom", "root", "wild", "thorn", "moss", "mushroom", "seed", "vine", "woodland"]),
    "Arcane Academia (Wizards & Scholars)": dict(types=["Wizard", "Sphinx", "Homunculus", "Scholar"],
                                                 words=["study", "library", "tome", "lecture", "scholar", "arcane", "spell", "professor", "lesson", "research"]),
    "Knights & Chivalry": dict(types=["Knight", "Soldier", "Paladin", "Noble"],
                               words=["oath", "vow", "crusade", "sword", "shield", "honor", "banner", "valor", "lance", "king", "queen", "throne"]),
    "Angels & Divine Order": dict(types=["Angel", "Archon", "Cleric", "Monk"],
                                  words=["divine", "holy", "sacred", "heaven", "light", "blessing", "sanctuary", "radiant", "prayer", "celestial"]),
    "Demons & Infernal Pacts": dict(types=["Demon", "Devil", "Imp", "Horror"],
                                    words=["pact", "infernal", "hell", "bargain", "contract", "damned", "sin", "abyss", "tormen*", "wicked"]),
    "Fae Courts & Trickery": dict(types=["Faerie", "Elf", "Kithkin", "Changeling", "Sprite"],
                                  words=["fae", "glimmer", "mischief", "trick", "dream", "wish", "gloaming", "whisper", "moonlight"]),
    "Heists & Intrigue (Rogues & Spies)": dict(types=["Rogue", "Assassin", "Ninja", "Spy", "Thief"],
                                               words=["heist", "thief", "shadow", "dagger", "spy", "cloak", "vault", "steal", "smuggler", "secret"]),
    "Primal Wilds (Dinosaurs & Beasts)": dict(types=["Dinosaur", "Beast", "Wurm", "Ape", "Wolf", "Bear", "Elephant", "Rhino", "Cat", "Boar"],
                                              words=["primal", "savage", "stampede", "fang", "claw", "jungle", "ancient", "raptor", "predator", "pack"]),
    "Machines & Artifice": dict(types=["Construct", "Golem", "Thopter", "Servo", "Robot", "Artificer", "Myr", "Vehicle", "Gnome"],
                                words=["gear", "forge", "clockwork", "automaton", "circuit", "engine", "workshop", "mechan*", "invent*", "cog"]),
    "Cosmic Horror & the Void": dict(types=["Eldrazi", "Horror", "Nightmare", "Leviathan", "Octopus"],
                                     words=["void", "abyss", "madness", "eldritch", "unspeakable", "dread", "whisper", "nightmare", "hungry", "stars"]),
    "Insect Swarm & Hive": dict(types=["Insect", "Spider", "Scorpion", "Wasp", "Beetle", "Ant"],
                                words=["hive", "swarm", "web", "queen", "chitin", "colony", "nest", "buzz", "sting"]),
    "War Hosts (Goblins, Orcs & Warriors)": dict(types=["Goblin", "Orc", "Warrior", "Barbarian", "Ogre", "Giant", "Berserker"],
                                                 words=["raid", "horde", "war", "rampage", "clan", "battle", "siege", "smash", "bloodthirst"]),
}
NARR_LABELS = [ANY] + list(_NARR)

# ----------------------------------------------------------------------------------------------
# 5. "Feel" tags used by the vibes (slots) and by the Avoid list. Computed at build time from text,
#    so the cached card file never needs to change.
# ----------------------------------------------------------------------------------------------
_VT = {
    "stax": [r"cost[s]? \{\d+\} more", r"(?:players?|opponents?|each opponent)[^.]{0,30}can't (?:cast|search|activate)",
             r"can't cast (?:more than|spells)", r"can't draw more than",
             r"(?:artifacts|lands|permanents)[^.]{0,30}(?:don't|doesn't) untap",
             r"creatures your opponents control enter(?: the battlefield)? tapped", r"nonbasic lands? (?:are|have)"],
    "chaos": [r"flip a coin", r"roll (?:a|two|\w+) d\d+", r"\brandom(?:ly)?\b", r"at random",
              r"each player (?:shuffles|discards (?:their|all) hand)", r"exchange (?:control|life|hands|cards)",
              r"gain control of target", r"\bgoad\b", r"\bdonate\b", r"\bswap\b", r"\bchaos\b"],
    "grouphug": [r"each player (?:may )?(?:draws?|gains?|puts?|searches|untaps|creates?)",
                 r"draws? an additional card", r"each opponent (?:may )?(?:draws?|searches|creates?)",
                 r"all players", r"play an additional land"],
    "politics": [r"\bmonarch\b", r"\bvot(?:e|ing)\b", r"will of the council", r"council's dilemma",
                 r"target opponent (?:chooses|gains control|may)", r"\bgoad\b", r"\bdonate\b",
                 r"each opponent (?:may|chooses)"],
    "infect": [r"\binfect\b", r"\btoxic \d", r"\bpoisonous\b", r"poison counter"],
    "landdestruction": [r"destroy target (?:nonbasic )?land", r"exile target (?:nonbasic )?land",
                        r"target player sacrifices a land"],
    "theft": [r"gain control of (?:target|that|each|all)"],
    "discard": [r"(?:target (?:player|opponent)|each opponent) discards"],
    "punish": [r"each opponent (?:loses|sacrifices|discards)", r"(?:opponents|your opponents) can't", r"whenever an opponent (?:casts|draws|activates|searches)[^.]{0,60}(?:loses|damage|you)",
               r"deals? \w+ damage to each opponent", r"target opponent (?:loses|sacrifices|discards)", r"creatures your opponents control (?:get -|enter tapped)",
               r"spells your opponents cast cost"],
}
_VT_RX = {k: re.compile("|".join(v), re.I) for k, v in _VT.items()}
_ADDMANA = re.compile(r"add \{", re.I)

AVOID_WORDS = {   # keyword -> where to look: ("t", base tag from tags.py) or ("v", feel tag above)
    "stax": ("v", "stax"), "extra turns": ("t", "extra_turn"), "tutors": ("t", "tutor"),
    "counterspells": ("t", "counter"), "wipes": ("t", "sweeper"), "infect": ("v", "infect"),
    "land destruction": ("v", "landdestruction"), "theft": ("v", "theft"), "discard": ("v", "discard"),
    "fast mana": ("v", "fastmana"), "combos": ("x", "combos"),
}
_AVOID_ALIASES = {
    "board wipes": "wipes", "wipe": "wipes", "wraths": "wipes", "wrath": "wipes", "sweepers": "wipes",
    "counters": "counterspells", "counterspell": "counterspells", "counter": "counterspells",
    "tutor": "tutors", "extra turn": "extra turns", "poison": "infect", "toxic": "infect",
    "ld": "land destruction", "steal": "theft", "threaten": "theft", "control magic": "theft",
    "hand disruption": "discard", "tax": "stax", "rocks": "fast mana", "moxen": "fast mana",
    "combo": "combos", "infinite combos": "combos", "infinites": "combos", "infinite": "combos",
}


def parse_avoid(text):
    """'wipes, counterspells; extra turn' -> ({'wipes','counterspells','extra turns'}, [unknown words])."""
    found, unknown = set(), []
    for w in re.split(r"[;,]", text or ""):
        w = w.strip().lower()
        if not w:
            continue
        w = _AVOID_ALIASES.get(w, w)
        if w in AVOID_WORDS:
            found.add(w)
        elif w not in unknown:
            unknown.append(w)
    return found, unknown


_cache = {}


def vibe_tags(card):
    """Set of feel tags for a card (stax, chaos, grouphug, politics, infect, ..., fastmana)."""
    key = ("v", card["name"])
    if key in _cache:
        return _cache[key]
    x = card.get("text") or ""
    tags = {k for k, rx in _VT_RX.items() if rx.search(x)}
    t = card.get("type_line") or ""
    if "Land" not in t and "Artifact" in t and card.get("cmc", 9) <= 2 and _ADDMANA.search(x):
        tags.add("fastmana")
    _cache[key] = tags
    return tags


def avoided(card, avoid):
    """True if this card falls into something the chosen vibe / Avoid box rules out."""
    if not avoid:
        return False
    tags, vt = set(card["tags"]), None
    for word in avoid:
        kind, tag = AVOID_WORDS[word]
        if kind == "x":
            continue
        if kind == "t":
            if tag in tags:
                return True
        else:
            vt = vt if vt is not None else vibe_tags(card)
            if tag in vt:
                return True
    return False


def _compile_mech():
    out = {}
    for label, spec in _MECH.items():
        out[label] = (re.compile("|".join(spec.get("text", [])), re.I) if spec.get("text") else None,
                      re.compile("|".join(spec["types"])) if spec.get("types") else None)
    return out


_MECH_RX = _compile_mech()
def _word_rx(words):
    """Whole words with an optional plural; a trailing * means 'any word starting with this stem'."""
    parts = []
    for w in words:
        parts.append(re.escape(w[:-1]) + r"\w*" if w.endswith("*") else re.escape(w) + r"(?:s|es)?")
    return re.compile(r"\b(?:%s)\b" % "|".join(parts), re.I)


_NARR_RX = {
    label: (re.compile(r"\b(?:%s)\b" % "|".join(map(re.escape, s["types"]))), _word_rx(s["words"]))
    for label, s in _NARR.items()
}


def mech_score(card, label):
    """How many distinct mechanical hooks of this theme the card has (type match counts as 1)."""
    if not label or label == ANY:
        return 0
    key = ("m", card["name"], label)
    if key in _cache:
        return _cache[key]
    trx, tyrx = _MECH_RX[label]
    x = card.get("text") or ""
    n = 0
    if trx:
        n += len(set(m.group(0).lower() for m in trx.finditer(x)))
    if tyrx and tyrx.search(card.get("type_line") or ""):
        n += 1
    _cache[key] = min(n, 4)
    return _cache[key]


def narr_score(card, label):
    """Narrative fit: 3 per creature-type hit, 2 for a name word, 1 for a rules/flavor word (capped)."""
    if not label or label == ANY:
        return 0
    key = ("n", card["name"], label)
    if key in _cache:
        return _cache[key]
    types_rx, words_rx = _NARR_RX[label]
    tl = card.get("type_line") or ""
    subtypes = tl.split("—", 1)[1] if "—" in tl else ""
    s = 3 * min(len(set(types_rx.findall(subtypes))), 2)
    s += 2 * min(len(set(m.group(0).lower() for m in words_rx.finditer(card["name"]))), 2)
    body = (card.get("text") or "") + " " + (card.get("flavor") or "")
    s += min(len(set(m.group(0).lower() for m in words_rx.finditer(body))), 3)
    _cache[key] = s
    return s


def explain(card, mech, narr):
    """Short human reasons why a card fits the chosen themes (used in the commander list)."""
    out = []
    if mech and mech != ANY and mech_score(card, mech):
        out.append(f"{mech}: {mech_score(card, mech)} hook(s)")
    if narr and narr != ANY and narr_score(card, narr) >= 2:
        out.append(f"{narr}: fit {narr_score(card, narr)}")
    return out


PICK_MODES = ["Best match", "Random from top 10 matches"]
