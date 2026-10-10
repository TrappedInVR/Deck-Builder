"""Single source of truth for the dropdown menus and the matching rules behind them.

The workflow forms (.github/workflows/build.yml and suggest.yml) are GENERATED from this file by
`python src/make_workflows.py`, so the labels in the forms can never drift from what the code expects.

Everything here is plain regex/keyword matching over Scryfall text (name, type line, rules text and
flavor text). It is deliberately simple and free. It finds cards that FIT a theme; it does not
understand deep synergies, so treat results as a strong draft."""
import re

import analyze as A
import context as CX

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
# budget      = USD for the 99's nonland cards when you leave Budget blank (lands never count; see lands.py)
# budget_stretch = extra USD this vibe may spend above that default, ONLY on cards worth it (Game Changers, staples,
#               strong high-synergy cards). Never above $1000 in total. A budget you type is a hard cap.
# avoid       = things this vibe never includes (same words you can type in the Avoid box)
# combo_max   = highest Commander Spellbook combo tag allowed (E/C/O = casual, P/S = Bracket 3). R is never allowed.
# combo_slots = how many complete combos the builder actively adds (0 = only keep ones that happen naturally)
# styles      = which kind of game plan this vibe leads with when a commander can be played several ways
#               (gentle, engine, aggro, control, punish, hug, chaos). It shifts emphasis; it never ignores the commander.
# personality = which commander traits fit this vibe (see analyze.TRAITS): mean, stax, chaos, hug,
#               gentle, engine, power. Negative = commanders like that are a poor match.
BRACKET = 3
_B3 = dict(bracket=BRACKET, max_gc=3)
VIBES = {
    "Relaxed Casual": dict(_B3,
        styles=dict(gentle=1.0, engine=0.4, hug=0.3, aggro=0.1, punish=-0.6, control=-0.6, chaos=-0.2),
        combo_max="O", combo_slots=0,
        gc_target=0, max_extra_turns=0, max_tutors=0, budget=500, budget_stretch=0, lands=38,
        quotas=dict(ramp=10, draw=8, removal=5, counter=1, sweeper=1, recursion=2), slots={}, pop=0.7,
        avoid={"stax", "extra turns", "tutors", "infect", "land destruction", "theft", "discard"},
        personality=dict(gentle=1.4, hug=0.4, engine=0.2, mean=-1.5, pressure=-0.4, stax=-2.0, chaos=-0.3),
        blurb="Build your board and enjoy it. Light interaction, no lockouts, games go long and everyone gets to play."),
    "Social Table: Fun for Everyone": dict(_B3,
        styles=dict(hug=1.0, gentle=0.6, chaos=0.3, engine=0.2, punish=-0.6, control=-0.7),
        combo_max="O", combo_slots=0,
        gc_target=0, max_extra_turns=0, max_tutors=0, budget=500, budget_stretch=0, lands=37,
        quotas=dict(ramp=9, draw=8, removal=4, counter=0, sweeper=1, recursion=2),
        slots=dict(grouphug=8, politics=4), pop=0.5,
        avoid={"stax", "extra turns", "tutors", "infect", "land destruction", "discard"},
        personality=dict(hug=2.2, gentle=0.6, chaos=0.4, mean=-1.2, stax=-2.0),
        blurb="Generous, symmetrical and political cards that give the whole table something to do."),
    "Competitive-Casual: Win and Have a Good Time": dict(_B3,
        styles=dict(engine=0.5, aggro=0.4, gentle=0.3, control=0.3, punish=0.3, chaos=-0.1),
        combo_max="P", combo_slots=1,
        gc_target=2, max_extra_turns=1, max_tutors=2, budget=500, budget_stretch=250, lands=37,
        quotas=dict(ramp=10, draw=9, removal=7, counter=3, sweeper=2, recursion=2), slots={}, pop=1.0,
        avoid={"stax", "infect", "land destruction"},
        personality=dict(power=0.9, engine=0.6, gentle=0.3, pressure=0.3, mean=0.2, stax=-0.8, chaos=-0.2), bias=0.3,
        blurb="A real plan to win with real interaction, but nothing that locks players out of the game."),
    "Chaos & Memes: Chaotic Fun": dict(_B3,
        styles=dict(chaos=1.0, hug=0.4, aggro=0.3, engine=0.1, control=-0.4),
        combo_max="O", combo_slots=1,
        gc_target=1, max_extra_turns=1, max_tutors=0, budget=500, budget_stretch=100, lands=37,
        quotas=dict(ramp=9, draw=7, removal=5, counter=1, sweeper=1, recursion=2), slots=dict(chaos=14), pop=0.5,
        avoid={"stax", "infect", "land destruction", "tutors"},
        personality=dict(chaos=2.6, hug=0.5, gentle=0.1, power=-0.3, stax=-1.0),
        blurb="Coin flips, swaps, wheels and wild effects. Unpredictable, but still a working deck."),
    "Table Threat: Scary to Play Against": dict(_B3,
        styles=dict(punish=1.0, aggro=0.7, engine=0.4, control=0.3, hug=-0.5, gentle=-0.2),
        combo_max="S", combo_slots=1,
        gc_target=3, max_extra_turns=1, max_tutors=3, budget=500, budget_stretch=400, lands=36,
        quotas=dict(ramp=11, draw=9, removal=8, counter=3, sweeper=2, recursion=2), slots=dict(punish=6), pop=1.2,
        avoid={"infect", "land destruction"},
        personality=dict(mean=1.6, pressure=0.7, power=1.0, engine=0.4, hug=-0.8, gentle=-0.3),
        blurb="Punishing and relentless: the commander makes opponents pay every turn, backed by heavy interaction."),
    "Hostile Control: Stax & Hate": dict(_B3,
        styles=dict(control=1.0, punish=0.8, engine=0.3, hug=-0.7, gentle=-0.4, chaos=-0.3),
        combo_max="S", combo_slots=1,
        gc_target=3, max_extra_turns=0, max_tutors=2, budget=500, budget_stretch=400, lands=36,
        quotas=dict(ramp=10, draw=9, removal=8, counter=5, sweeper=3, recursion=1), slots=dict(stax=7, punish=4), pop=1.0,
        avoid={"infect", "land destruction"},
        personality=dict(stax=2.4, mean=1.5, power=0.3, hug=-1.2, gentle=-0.8),
        blurb="Taxes, restrictions, counterspells and sweepers that make everyone else's turn miserable. "
              "Still Bracket 3: no mass land denial, ever."),
    "Optimized Menace: Max-Power Bracket 3": dict(_B3,
        styles=dict(engine=1.0, aggro=0.5, control=0.5, punish=0.4, chaos=-0.4, hug=-0.4),
        combo_max="S", combo_slots=2,
        gc_target=3, max_extra_turns=1, max_tutors=4, budget=500, budget_stretch=500, lands=35,
        quotas=dict(ramp=12, draw=10, removal=8, counter=4, sweeper=2, recursion=2), slots=dict(fastmana=3), pop=1.5,
        avoid=set(),
        personality=dict(power=2.0, engine=1.0, mean=0.6, pressure=0.3, gentle=-0.2, chaos=-0.5),
        blurb="As strong as Bracket 3 allows: 3 Game Changers, efficient staples and tutors. "
              "Known early two-card wins are swapped out automatically to stay in Bracket 3."),
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
# Mechanical themes are defined by the analyzer's game plans (analyze.MECH_TO_PLANS), so a theme card is judged
# with the same context filter as the commander: it has to genuinely do the thing for you.
MECH_LABELS = [ANY] + list(A.MECH_TO_PLANS)
# Typical creature counts differ by plan; a Spellslinger deck with 38 creatures is not a Spellslinger deck.
MECH_MAX_CREATURES = {"Spellslinger": 16, "Enchantress": 20, "Equipment & Auras (Voltron)": 22, "Superfriends (Planeswalkers)": 20,
                      "Card Draw & Wheels": 24, "Flash & Instant-Speed": 20, "Big Spells & X Costs": 22, "Vehicles": 24,
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
_NARR.update({
    "Wild West Outlaws": dict(types=["Mercenary", "Rogue", "Warlock", "Assassin", "Lizard", "Coyote"],
                              words=["outlaw", "bounty", "saloon", "desert", "sheriff", "gunslinger", "posse", "frontier", "canyon",
                                     "train", "duel", "showdown", "gold", "mesa"]),
    "Samurai & Ninjas": dict(types=["Samurai", "Ninja", "Kami", "Fox", "Moonfolk", "Monk"],
                             words=["katana", "shogun", "dojo", "honor", "shrine", "kami", "blade", "lantern", "cherry", "petal",
                                    "temple", "neon"]),
    "Frost & Winter": dict(types=["Yeti", "Wolf", "Giant", "Bear"], supertypes=["Snow"],
                           words=["ice", "frost", "snow", "winter", "frozen", "glacier", "cold", "rime", "blizzard", "tundra",
                                  "chill", "hoarfrost"]),
    "Myths & Gods": dict(types=["God", "Demigod", "Hydra", "Satyr", "Centaur", "Minotaur", "Nymph", "Siren"],
                         words=["oracle", "hero", "nyx", "temple", "myth", "titan", "olympus", "pantheon", "omen", "prophecy",
                                "divine", "fate"]),
    "Sun, Moon & Stars": dict(types=["Archon", "Sphinx"],
                              words=["sun", "moon", "star", "eclipse", "dawn", "dusk", "celestial", "night", "sky", "constellation",
                                     "comet", "lunar", "solar", "astral"]),
})
# Words that are ALSO game terms: in RULES text they describe mechanics, not flavor, so only the card's name and
# flavor text count for them (e.g. "spell" for wizards, "treasure" for pirates, "blood" for vampires).
GAME_TERMS = {"spell", "treasure", "blood", "battle", "war", "burn", "fire", "copy", "wish", "lesson", "study", "light",
              "curse", "shadow", "wild", "root", "seed", "night", "sky", "fate", "omen", "temple", "train", "gold", "dream",
              "secret", "vault", "engine", "hero", "pack", "wave", "tide", "web", "nest", "trick", "blessing"}
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
    """0..3: how strongly a card fits a mechanical theme (context-filtered analyzer plans)."""
    if not label or label == ANY:
        return 0
    key = ("m", card["name"], label)
    if key not in _cache:
        _cache[key] = A.theme_strength(card, label)
    return _cache[key]


def narr_score(card, label):
    """Narrative fit (flavor, not mechanics):
         creature type in the theme  3 each (max 2 types)    supertype (e.g. Snow)  2
         theme word in the card NAME 2 each (max 2)           theme word in FLAVOR text 1.5 each (max 2)
         theme word in RULES text    1 each (max 2), but never for words that are also game terms (spell, treasure...)
       A card fits at 2+."""
    if not label or label == ANY:
        return 0
    key = ("n", card["name"], label)
    if key in _cache:
        return _cache[key]
    spec = _NARR[label]
    types_rx, words_rx = _NARR_RX[label]
    tl = card.get("type_line") or ""
    subtypes = tl.split("—", 1)[1] if "—" in tl else ""
    s = 3 * min(len(set(types_rx.findall(subtypes))), 2)
    if any(st in tl.split("—")[0] for st in spec.get("supertypes", [])):
        s += 2
    s += 2 * min(len({m.group(0).lower() for m in words_rx.finditer(card["name"])}), 2)
    s += 1.5 * min(len({m.group(0).lower() for m in words_rx.finditer(card.get("flavor") or "")}), 2)
    rules = {m.group(0).lower() for m in words_rx.finditer(card.get("text") or "")}
    rules = {w for w in rules if w.rstrip("s") not in GAME_TERMS and w not in GAME_TERMS}
    s += min(len(rules), 2)
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
OLD_MECH_NAMES = {"Blink & Value": "Blink & Enter-the-Battlefield", "Treasure & Clues": "Treasure, Clues & Food",
                  "Draw, Wheels & Discard": "Card Draw & Wheels"}


# ---------------------------------------------------------------------------------------------------------------
SLOT_NAMES = dict(grouphug="group-hug cards (everyone draws/gains)", politics="politics (monarch, vote, goad)",
                  chaos="chaos cards (coin flips, swaps, random)", punish="punisher cards (opponents lose life/sacrifice/discard)",
                  stax="stax pieces (taxes, 'can't' effects)", fastmana="fast mana")
COMBO_NAMES = dict(E="none", C="casual/late-game only", O="up to Oddball", P="up to Powerful (late, multi-card)",
                   S="up to Spicy (strong, but never early two-card wins)")


def vibe_criteria_md():
    """The vibe table for the README (python -c 'import options; print(options.vibe_criteria_md())')."""
    def top(d, n=3, pos=True):
        items = sorted(((k, v) for k, v in d.items() if (v > 0) == pos and v), key=lambda kv: -abs(kv[1]))
        return ", ".join(k for k, _ in items[:n]) or "-"
    rows = ["| Vibe | What it feels like | Commanders it picks | Plans it leads with | Game Changers | Tutors / extra turns | "
            "Combos | Ramp / draw / removal / counters / wipes / recursion | Signature cards | Never includes | Lands | Budget |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, v in VIBES.items():
        q = v["quotas"]
        slots = "; ".join(f"{n} {SLOT_NAMES.get(s, s)}" for s, n in v["slots"].items()) or "-"
        combos = "none" if not v["combo_slots"] else f"{v['combo_slots']} built in ({COMBO_NAMES[v['combo_max']]})"
        rows.append(f"| **{k}** | {v['blurb']} | {top(v['personality'])} (not {top(v['personality'], 2, False)}) | "
                    f"{top(v['styles'])} (not {top(v['styles'], 2, False)}) | aims for {v['gc_target']} (max 3) | "
                    f"{v['max_tutors']} / {v['max_extra_turns']} | {combos} | "
                    f"{q['ramp']} / {q['draw']} / {q['removal']} / {q['counter']} / {q['sweeper']} / {q['recursion']} | {slots} | "
                    f"{', '.join(sorted(v['avoid'])) or '-'} | {v['lands']} | "
                    f"${v['budget']}" + (f" (+${v['budget_stretch']} for cards worth it)" if v.get("budget_stretch") else "") + " |")
    return "\n".join(rows)
