"""Rule-based role tagging (replaces an LLM so it costs $0 to run)."""
import re

MASS_LAND_DENIAL = re.compile(
    r"destroy all lands|each player sacrifices (?:all|a) lands?|sacrifice all lands|"
    r"lands? .{0,20}don't untap|"
    r"\b(?:armageddon|ravages of war|catastrophe|jokulhaups|devastation|obliterate|ruination|"
    r"winter orb|static orb|stasis)\b", re.I)
EXTRA_TURN = re.compile(r"takes? (?:an|two|three|\d+) extra turns?", re.I)

def tag(card):
    t, x = card["type_line"], (card.get("text") or "")
    xl = x.lower()
    tags = set()
    is_land = "Land" in t
    if is_land:
        tags.add("land")
    else:
        if re.search(r"add \{[wubrgc0-9]", xl) and ("Artifact" in t or "Creature" in t or "Enchantment" in t):
            tags.add("ramp")
        if re.search(r"search your library for (?:up to \w+ |a |an )?(?:basic )?(?:land|forest|island|plains|swamp|mountain)", xl) \
           or re.search(r"put (?:a|that|those|them)? ?.{0,30}land cards? .{0,40}onto the battlefield", xl):
            tags.add("ramp")
        if re.search(r"draw (?:a|two|three|four|x|that many|cards?)|draws? (?:a|two|three) cards?|"
                     r"you may draw|exile the top .{0,30}you may (?:play|cast)", xl):
            tags.add("draw")
        if re.search(r"(?:destroy|exile) target (?:\w+ ){0,3}(?:creature|permanent|artifact|enchantment|nonland)|"
                     r"return target .{0,30}to its owner's hand|deals? \w+ damage to target creature", xl):
            tags.add("removal")
        if re.search(r"(?:destroy|exile) all (?:\w+ ){0,3}(?:creatures|permanents|artifacts|enchantments)|"
                     r"all creatures get -", xl):
            tags.add("sweeper")
        if re.search(r"counter target", xl):
            tags.add("counter")
        if re.search(r"search your library for", xl) and "ramp" not in tags:
            tags.add("tutor")
        if re.search(r"return (?:target|up to) .{0,40}from your graveyard", xl):
            tags.add("recursion")
    if "Creature" in t:
        tags.add("creature")
    if MASS_LAND_DENIAL.search(x) or MASS_LAND_DENIAL.search(card["name"]):
        tags.add("mass_land_denial")
    if EXTRA_TURN.search(x):
        tags.add("extra_turn")
    return sorted(tags)
