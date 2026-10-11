"""Land base: as few tapped lands as possible, all the real dual lands, none of the broken ability lands.

House rules (from the playgroup):
  * Dual-type lands are ALWAYS included when they fit the colors: original duals, shocks, fetches, and the
    other untapped (or usually-untapped) dual cycles. Land prices never count against the deck budget.
  * Never include the most powerful ability lands (Gaea's Cradle, Ancient Tomb, Cabal Coffers, Strip Mine...).
  * The only truly expensive lands allowed are the original duals: any other land that isn't a mana-fixing
    dual and costs more than UTILITY_PRICE_CAP is skipped.
  * Tapped lands are a last resort (only tri-lands in 3+ color decks, and only a few)."""
import re

UTILITY_PRICE_CAP = 15.0
MAX_TAPPED = 3

# The "most powerful ability lands": never included, whatever the colors or budget.
POWER_LANDS = {n.lower() for n in """
Gaea's Cradle|Serra's Sanctum|Tolarian Academy|Cabal Coffers|Cabal Stronghold|Ancient Tomb|Mishra's Workshop|
The Tabernacle at Pendrell Vale|Glacial Chasm|Field of the Dead|Urza's Saga|Strip Mine|Wasteland|Dark Depths|
Thespian's Stage|Maze of Ith|Kor Haven|Urborg, Tomb of Yawgmoth|Yavimaya, Cradle of Growth|Nykthos, Shrine to Nyx|
Itlimoc, Cradle of the Sun|Boseiju, Who Endures|Otawara, Soaring City|Eiganjo, Seat of the Empire|Sokenzan, Crucible of Defiance|
Takenuma, Abandoned Mire|Emeria, the Sky Ruin|Rishadan Port|Hall of the Bandit Lord|Cavern of Souls|Mutavault|Gemstone Caverns|
City of Traitors|Lotus Field|Vault of Whispers|Tolaria West|Inventors' Fair|Phyrexian Tower|High Market|Homeward Path|
Volrath's Stronghold|Academy Ruins|Crystal Vein|Temple of the False God|Shivan Gorge|Kessig Wolf Run|Sacred Peaks|
Lake of the Dead|Grim Backwoods|Dryad Arbor|Elvish Hexhunter|Bazaar of Baghdad|Library of Alexandria|Karakas|
Hall of Heliod's Generosity
""".replace("\n", "").split("|") if n.strip()}

BASIC_TYPES = {"Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G"}
_TYPE_RX = re.compile(r"\b(Plains|Island|Swamp|Mountain|Forest)\b")

# tier = how good the land is for the "fewest tapped lands" goal (0 best)
TIER_NAMES = {0: "original dual", 1: "untapped dual", 2: "usually untapped dual", 3: "any-color land",
              4: "tri-land (enters tapped)", 5: "tapped dual", 6: "utility land"}


def _norm(card):
    x = card.get("text") or ""
    for n in sorted({card["name"], card["name"].split(" // ")[0], card["name"].split(",")[0]}, key=len, reverse=True):
        if n:
            x = x.replace(n, "~")
    return x


def classify(card, ident):
    """Return (tier, kind, colors_made) for a nonbasic land, or None if it shouldn't be considered."""
    name = card["name"].lower()
    if name in POWER_LANDS or card.get("game_changer"):
        return None
    tl = card.get("type_line") or ""
    x = _norm(card)
    xl = x.lower()
    front_types = set(_TYPE_RX.findall(tl.split("//")[0]))
    ident = set(ident)
    # colors it makes just by tapping ({T}: Add ...), plus basic land types
    cols = {BASIC_TYPES[t] for t in front_types}
    for m in re.finditer(r"^\{T\}(?:, Pay 1 life)?: Add ([^.\n]*)", x, re.M | re.I):
        cols |= set(re.findall(r"\{([WUBRG])\}", m.group(1)))
    if " // " in card["name"] and "Land" in tl:          # pathways / MDFC lands: both faces count
        cols |= set(re.findall(r"\{([WUBRG])\}", x))
    anycolor = bool(re.search(r"^\{T\}(?:, Pay 1 life)?: Add one mana of any (?:color|type)", x, re.M | re.I))
    if anycolor:   # only "clean" any-color lands: nothing else in the text except a pain clause
        clauses = [c.strip() for c in re.split(r"(?<=\.)\s+|\n", x) if c.strip()]
        rest = [c for c in clauses if not re.fullmatch(
            r"\{T\}(?:, Pay 1 life)?: Add (?:one mana of any (?:color|type)[^.]*|\{C\})\.|Whenever ~ becomes tapped, it deals 1 damage to you\.", c, re.I)]
        anycolor = not rest
    fetch = re.search(r"search your library for an? (\w+)(?:,| or) (?:an? )?(\w+)(?: or (\w+))? card", x, re.I)
    fetch_types = {BASIC_TYPES[t.title()] for t in (fetch.groups() if fetch else ()) if t and t.title() in BASIC_TYPES}
    useful = cols & ident
    # any "enters tapped..." wording counts (incl. "enters tapped with two depletion counters"), except "...tapped unless"
    tapped = bool(re.search(r"enters(?: the battlefield)? tapped(?! unless)", x)) or bool(re.search(r"doesn't untap during your untap step", x))
    strings = bool(re.search(r"spend this mana only|activate only|sacrifice ~ unless|sacrifice ~ at|sacrifice it unless|"
                             r"return a land you control to its owner's hand|can't be cast|an opponent creates", xl))
    cond_untapped = bool(re.search(r"enters(?: the battlefield)? tapped unless|you may pay 2 life\. if you don't", xl))
    if front_types and not (cols <= ident):
        return None                                   # an off-color dual (e.g. Underground Sea in Simic)

    if len(ident) >= 2:
        # fetches: real ones (pay 1 life, untapped) find your duals/shocks too, so one matching type is enough
        if fetch_types & ident and "sacrifice ~" in xl and "pay 1 life" in xl and "tapped" not in xl:
            return 1, "fetch land", fetch_types & ident
        if re.search(r"search your library for a basic land card[^.]{0,40}onto the battlefield", xl) and "pay 1 life" in xl \
                and "tapped" not in xl:
            return 2, "fetch land", ident
        if anycolor and not tapped and not strings:
            if "commander's color identity" in xl:
                return 0, "Command Tower", ident
            if re.search(r"deals 1 damage to you|pay 1 life|lose 1 life", xl):
                return 2, "any-color land (pain)", ident
            return 1, "any-color land", ident
        if len(useful) >= 2 and not strings:
            if len(front_types) == 2 and not re.search(r"\benters\b|pay|damage|sacrifice", xl):
                return 0, TIER_NAMES[0], useful
            if re.search(r"you may pay 2 life", xl):
                return 1, "shock land", useful
            if re.search(r"unless you have two or more opponents", xl):
                return 1, "bond land (untapped in Commander)", useful
            if " // " in card["name"] and not tapped:
                return 1, "pathway", useful
            if re.search(r"deals 1 damage to you|\{t\}, pay 1 life: add", xl):
                return 2, "pain/horizon land", useful
            if cond_untapped:
                return 2, "check/fast/slow land", useful
            if tapped and len(useful) >= 3:
                return 4, TIER_NAMES[4], useful
            if tapped:
                return 5, TIER_NAMES[5], useful
            return 2, "untapped dual", useful
        if re.search(r"\{[wubrg]/[wubrg]\}, \{t\}: add", xl) and len(set(re.findall(r"\{([WUBRG])\}", x)) & ident) >= 2:
            return 2, "filter land", ident
    # everything else is a utility land (never an any-color land: in a one-color deck it's just a worse basic)
    if anycolor or re.search(r"add one mana of any color|commander's color identity", xl):
        return None
    if tapped or strings or re.search(r"onto the battlefield tapped", xl) or front_types:
        return None
    price = _price(card)
    if price > UTILITY_PRICE_CAP:
        return None
    if useful or not cols or re.search(r"\{c\}", xl):
        return 6, TIER_NAMES[6], useful
    return None


def _price(c):
    try:
        return float(c.get("usd") or 0)
    except ValueError:
        return 0.0


KIND_ORDER = {"Command Tower": 0, "original dual": 0, "shock land": 1, "fetch land": 2, "bond land (untapped in Commander)": 3, "pathway": 3,
              "any-color land": 4, "check/fast/slow land": 5, "pain/horizon land": 6, "any-color land (pain)": 6,
              "filter land": 7, "untapped dual": 8}


def nonbasic_target(ncolors, lands):
    base = {0: 6, 1: 5, 2: 14, 3: 20, 4: 23, 5: 26}[min(ncolors, 5)]
    return min(base, lands - 8)


def choose(pool, ident, lands, synergy=lambda c: 0, popularity=lambda c: 0, max_nonbasic=None):
    """Pick the nonbasic lands. Returns (chosen [(card, kind)], report dict)."""
    ident = set(ident)
    rows = []
    for c in pool:
        if "Land" not in (c.get("type_line") or "").split("//")[0] or "Basic" in c["type_line"]:   # front face must be a land
            continue
        if re.search(r"\bCreature\b", c["type_line"].split("//")[0]):
            continue
        k = classify(c, ident)
        if k is None:
            continue
        tier, kind, useful = k
        rows.append((tier, KIND_ORDER.get(kind, 9), -len(useful), -min(synergy(c), 20) - popularity(c), c["name"], c, kind))
    rows.sort(key=lambda r: r[:5])
    target = nonbasic_target(len(ident), lands) if max_nonbasic is None else min(max_nonbasic, lands - 8)
    chosen, tapped, utility = [], 0, 0
    seen_faces = set()
    anyc = 0
    for tier, _, _, _, _, c, kind in rows:
        if len(chosen) >= target:
            break
        if kind.startswith("any-color"):
            if anyc >= 3:
                continue
            anyc += 1
        if tier in (4, 5):
            if tapped >= MAX_TAPPED or tier == 5:
                continue
            tapped += 1
        if tier == 6:
            # utility lands: only a few, and only ones that do something for this deck
            if utility >= (4 if len(ident) <= 1 else 2) or (synergy(c) <= 0 and popularity(c) < 12):
                continue
            utility += 1
        faces = {c["name"].lower()} | {f.strip().lower() for f in c["name"].split(" // ")}
        if faces & seen_faces:
            continue                       # pathways & co.: one physical card, never twice
        seen_faces |= faces
        chosen.append((c, kind))
    kinds = {}
    for c, kind in chosen:
        kinds[kind] = kinds.get(kind, 0) + 1
    return chosen, dict(kinds=kinds, tapped=tapped, candidates=len(rows))
