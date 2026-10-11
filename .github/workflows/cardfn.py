"""What each card DOES and what it WANTS, so cards can be judged against the rest of the deck, not just the commander.

Every card is read with the same ability parser used for commanders (abilities.py) and turned into two sets of
"resources" from one shared vocabulary:

  produces   what it puts into the game for the deck
             a creature card -> a body, a creature entering, a creature spell cast, an attacker
             "create two 1/1 tokens" -> bodies, creatures entering, tokens created
             a sacrifice outlet -> creatures dying; "you gain 3 life" -> life gained; "draw a card" -> cards drawn
  wants      what makes it work
             "whenever a creature enters under your control" -> creatures entering
             "sacrifice a creature: ..." -> bodies to sacrifice;  "{T}: ..." on a creature -> untap effects
             "whenever you gain life" -> life gained

Deck synergy (deck_links): a card is linked to the deck when the deck produces what it wants, or wants what it
produces. Example in a token deck: Impact Tremors wants "creatures entering", and every token maker produces it;
a sacrifice outlet wants "bodies" and produces "creatures dying", which Blood Artist wants. A card that links to
nothing else in the deck is a stand-alone card; it needs to be a staple to keep its slot (build_deck.optimize).
Triggers about OPPONENTS' actions ("whenever an opponent casts...") are not wants: the deck can't feed them."""
import re

import abilities as AB

_CACHE = {}

TYPE_PRODUCES = [
    ("Creature", {"body", "creature_enters", "cast_creature", "cast_spell", "attacks"}),
    ("Instant", {"cast_instant_sorcery", "cast_spell"}),
    ("Sorcery", {"cast_instant_sorcery", "cast_spell"}),
    ("Artifact", {"artifact_enters", "cast_artifact", "cast_spell"}),
    ("Enchantment", {"enchantment_enters", "cast_enchantment", "cast_spell"}),
    ("Planeswalker", {"cast_spell"}),
]
OUTPUT_PRODUCES = {
    "plus_counters": {"counters_put"}, "proliferate": {"counters_put"}, "draw": {"draw"}, "mill_self": {"to_graveyard"},
    "lifegain": {"gain_life"}, "drain": {"opponent_loses_life"}, "reanimate": {"creature_enters", "leaves_graveyard", "body"},
    "reanimate2": {"leaves_graveyard"}, "treasure": {"token_enters", "artifact_enters", "artifact_fodder"},
    "untap": {"untap"}, "extra_combat": {"attacks", "combat_damage"}, "haste_evasion": {"combat_damage"},
    "needs_fodder": {"creature_dies", "sacrifice"},
}
COST_WANTS = {
    "tap": {"untap"}, "sac_creature": {"body"}, "sac_artifact": {"artifact_fodder"}, "sac_any": {"body", "artifact_fodder"},
    "discard": {"draw"}, "life": {"gain_life"}, "remove_counters": {"counters_put"}, "exile_graveyard": {"to_graveyard"},
    "tap_creatures": {"body"}, "plus_counters": {"counters_put"},
}
COST_PRODUCES = {"sac_creature": {"creature_dies", "sacrifice"}, "sac_artifact": {"sacrifice"}, "sac_any": {"sacrifice", "creature_dies"},
                 "discard": {"discard", "to_graveyard"}}
EVENT_ALIASES = {"cast_type": "cast_spell", "token_enters": "token_enters"}
LABELS = dict(body="bodies", creature_enters="creatures entering", cast_creature="creature spells", cast_spell="spells cast",
              attacks="attackers", cast_instant_sorcery="instants/sorceries", artifact_enters="artifacts entering",
              cast_artifact="artifact spells", enchantment_enters="enchantments entering", cast_enchantment="enchantment spells",
              counters_put="+1/+1 counters", draw="card draw", to_graveyard="cards in your graveyard", gain_life="lifegain",
              opponent_loses_life="life loss for opponents", leaves_graveyard="graveyard recursion", token_enters="tokens",
              artifact_fodder="artifacts to sacrifice", untap="untap effects", combat_damage="combat damage",
              creature_dies="creatures dying", sacrifice="sacrifices", discard="discards", land_enters="lands entering")
_CREATURE_TOKEN = re.compile(r"create[^.]{0,80}creature tokens?", re.I)
_OTHER_TOKEN = re.compile(r"create[^.]{0,40}(?:treasure|clue|food|blood|map|gold|powerstone|incubator)[^.]{0,10}tokens?", re.I)
_LAND_DROP = re.compile(r"(?:search your library for[^.]{0,40}lands?[^.]{0,60}onto the battlefield|play an additional land|put a land card[^.]{0,40}onto the battlefield)", re.I)
_OPP_TRIGGER = re.compile(r"\b(?:an opponent|opponents|each opponent|a player|target opponent)\b", re.I)


def functions(card):
    """{'produces': set, 'wants': set} for one card (cached by name)."""
    key = card["name"]
    if key in _CACHE:
        return _CACHE[key]
    tl = card.get("type_line") or ""
    text = card.get("text") or ""
    prod, want = set(), set()
    for t, res in TYPE_PRODUCES:
        if re.search(r"\b%s\b" % t, tl.split("//")[0]):
            prod |= res
    if _CREATURE_TOKEN.search(text):
        prod |= {"body", "creature_enters", "token_enters"}
    if _OTHER_TOKEN.search(text):
        prod |= {"token_enters", "artifact_enters", "artifact_fodder"}
    if _LAND_DROP.search(text):
        prod.add("land_enters")
    try:
        parsed = AB.parse(card)
    except Exception:
        parsed = []
    for ab in parsed:
        if ab["event"]:
            k = ab["event"][0].split(":")[0]
            k = EVENT_ALIASES.get(k, k)
            clause = ab["text"].split(",")[0]
            if not _OPP_TRIGGER.search(clause):
                want.add(k)
        outs = {o[0] for o in ab["outputs"]}
        for k, _ in ab["costs"]:
            if k == "tap" and outs <= {"mana"}:
                continue                       # a mana rock's {T} isn't an engine that wants untapping
            want |= COST_WANTS.get(k, set())
            prod |= COST_PRODUCES.get(k, set())
        for o in ab["outputs"]:
            k = o[0]
            if k == "needs_fodder":
                want.add("body")
            prod |= OUTPUT_PRODUCES.get(k, set())
    # a creature with a {T} ability wants untap effects only if it's an engine (already covered by the 'tap' cost)
    prod |= set(card.get("_k_produces") or [])        # Scryfall Tagger tags + your overrides (knowledge.py)
    want |= set(card.get("_k_wants") or [])
    out = dict(produces=prod, wants=want)
    _CACHE[key] = out
    return out


def _sat(n, full=5):
    return min(n, full) / full


class DeckWeb:
    """Counts of who produces / wants each resource in the current deck, for fast link scores while swapping cards."""

    def __init__(self, cards):
        self.prod, self.want = {}, {}
        for c in cards:
            self.add(c)

    def add(self, c, sign=1):
        f = functions(c)
        for r in f["produces"]:
            self.prod[r] = self.prod.get(r, 0) + sign
        for r in f["wants"]:
            self.want[r] = self.want.get(r, 0) + sign

    def remove(self, c):
        self.add(c, -1)

    def links(self, c, inside=True):
        """(score, reasons) for card c against the deck. inside=True: c is already counted in the web."""
        f = functions(c)
        own = 1 if inside else 0
        score, reasons = 0.0, []
        for r in f["wants"]:
            n = self.prod.get(r, 0) - (own if r in f["produces"] else 0)
            if n > 0:
                score += 1.0 * _sat(n)
                reasons.append((1.0 * _sat(n), f"fed by {n} cards ({LABELS.get(r, r)})"))
        for r in f["produces"]:
            n = self.want.get(r, 0) - (own if r in f["wants"] else 0)
            if n > 0:
                score += 0.7 * _sat(n, 3)
                reasons.append((0.7 * _sat(n, 3), f"feeds {n} cards ({LABELS.get(r, r)})"))
        reasons.sort(key=lambda x: -x[0])
        return score, [r for _, r in reasons[:2]]
