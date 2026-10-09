"""Rule-based role tagging (replaces an LLM so it costs $0 to run).
Tags: land, ramp, draw, removal, sweeper, counter, tutor, recursion, creature, mass_land_denial, extra_turn.
Recomputed every time cards are loaded, so improving these rules never needs a fresh download."""
import re

MASS_LAND_DENIAL = re.compile(
    r"destroy all lands|each player sacrifices (?:all|a) lands?|sacrifice all lands|"
    r"lands? .{0,20}don't untap|"
    r"\b(?:armageddon|ravages of war|catastrophe|jokulhaups|devastation|obliterate|ruination|"
    r"winter orb|static orb|stasis)\b", re.I)
EXTRA_TURN = re.compile(r"takes? (?:an|two|three|\d+) extra turns?", re.I)

_LANDWORD = r"(?:basic )?(?:land|forest|island|plains|swamp|mountain)s?(?: cards?)?"
RAMP_PERM = re.compile(r"\badd (?:\{[wubrgc0-9x]|one mana|two mana|three mana|x mana|mana of|an amount|that much)", re.I)
RAMP_LAND = re.compile(r"search your library for (?:up to \w+ |a |an |two )?" + _LANDWORD + r"[^.]{0,80}onto the battlefield|"
                       r"put (?:a|up to \w+|two|that|those|them)? ?.{0,30}land cards? .{0,40}onto the battlefield|"
                       r"(?:you may )?play (?:an? )?additional lands?|create (?:a|an|two|x|that many) (?:tapped )?treasure", re.I)
DRAW = re.compile(r"draw (?:a|two|three|four|five|seven|x|that many|\d+|cards?)|draws? (?:a|an|two|three) (?:additional )?cards?|you may draw|"
                  r"exile the top .{0,40}(?:you may|until).{0,40}(?:play|cast)|"
                  r"(?:reveal|look at) the top (?:two|three|four|five|six|seven|x|\w+) cards?[^.]{0,80}(?:into your hand|in your hand)|"
                  r"draws? an additional card|put (?:one|that) pile into your hand", re.I)
REMOVAL = re.compile(
    r"(?:destroy|exile) (?:another |up to (?:one|two|three) )?target (?:\w+[ ,]+){0,4}?(?:creature|permanent|artifact|enchantment|planeswalker|nonland)|"
    r"return target (?:\w+ ){0,3}(?:creature|permanent|nonland)[^.]{0,30}to its owner's hand|"
    r"deals? (?:\d+|x|damage equal[^.]{0,30}) damage to (?:target (?:\w+ )?creature|any target|target (?:attacking|blocking))|"
    r"target (?:creature|permanent)'s owner shuffles|owner of target (?:\w+ )?(?:creature|permanent) shuffles|shuffles? (?:it|target \w+) into (?:its|their) owner's library|"
    r"(?:put|puts) target (?:\w+ ){0,2}(?:creature|permanent|nonland permanent) (?:on (?:the )?(?:top|bottom)|into) (?:of )?its owner's library|"
    r"\bfights? (?:target|up to one target|another target)|"
    r"enchanted (?:creature|permanent) (?:can't attack or block|can't attack, block|loses all abilities)|"
    r"target creature gets -\d+/-\d+|"
    r"target (?:player|opponent) sacrifices (?:a|an) (?:creature|attacking creature|nonland permanent|permanent)|"
    r"each opponent sacrifices (?:a|an) (?:creature|nonland permanent|permanent)|"
    r"gain control of target (?:creature|permanent|artifact)", re.I)
SWEEPER = re.compile(
    r"(?:destroy|exile) (?:all|each) (?:\w+ ){0,3}(?:creatures|permanents|artifacts|enchantments|nonland permanents)|"
    r"all creatures get -|deals? (?:\d+|x) damage to each (?:creature|other creature|non\w+ creature)|"
    r"return (?:all|each) (?:nonland )?(?:creatures|permanents|nonland permanents)[^.]{0,30}to (?:their|its) owners?'s? hands?|"
    r"each (?:player|opponent) sacrifices (?:all|x|two|three) (?:creatures|permanents)|"
    r"\boverload \{", re.I)
RECURSION = re.compile(
    r"return (?:target|up to \w+|another target|all|each|a|an) .{0,60}from (?:your|a|any) graveyards? to (?:your hand|the battlefield)|"
    r"return (?:target|up to \w+|another target) [^.]{0,60}card[^.]{0,30}from your graveyard|"
    r"put (?:target|up to \w+|a|an) [^.]{0,40}card from (?:a|your|any) graveyard onto the battlefield|"
    r"return enchanted creature card to the battlefield|puts? all cards they exiled this way onto the battlefield|returns? all creature cards[^.]{0,40}(?:graveyard|battlefield)|"
    r"you may cast [^.]{0,40}from your graveyard|cast target [^.]{0,30}card from your graveyard", re.I)


def tag(card):
    t, x = card["type_line"], (card.get("text") or "")
    tags = set()
    # artifact lands (Darksteel Citadel, Great Furnace...) ARE lands; land creatures (Dryad Arbor) and MDFC spells aren't
    if "Land" in t.split("//")[0] and not re.search(r"\b(?:Creature|Instant|Sorcery)\b", t.split("//")[0]):
        tags.add("land")
    else:
        if RAMP_PERM.search(x) and re.search(r"\b(?:Artifact|Creature|Enchantment|Planeswalker)\b", t):
            tags.add("ramp")
        if RAMP_LAND.search(x):
            tags.add("ramp")
        if DRAW.search(x):
            tags.add("draw")
        if any(not re.match(r"[^.]{0,25}\byou (?:own|control)\b", x[m.end():m.end() + 30])
               and not re.match(r"[^.]{0,40}\.?\s*(?:then )?return (?:it|that card|those cards|them) to the battlefield", x[m.end():m.end() + 80], re.I)
               for m in REMOVAL.finditer(x)):
            tags.add("removal")
        if SWEEPER.search(x):
            tags.add("sweeper")
        if re.search(r"counter target [^.]{0,45}?(?:spell|ability)", x, re.I):
            tags.add("counter")
        if re.search(r"search your library for", x, re.I) and "ramp" not in tags:
            tags.add("tutor")
        if RECURSION.search(x):
            tags.add("recursion")
    if "Creature" in t:
        tags.add("creature")
    if MASS_LAND_DENIAL.search(x) or MASS_LAND_DENIAL.search(card["name"]):
        tags.add("mass_land_denial")
    if EXTRA_TURN.search(x):
        tags.add("extra_turn")
    return sorted(tags)
