"""How fast can this deck win? Bracket 3 decks should win around turns 6-8, not earlier.

The builder asks this BEFORE adding power cards (Game Changers, tutors, fast mana, combo pieces): if a card would
let the deck plausibly win before turn 6, it is left out.

The estimate is rule-based and deliberately simple (no full game simulation):
  mana by turn t   = land drops + ramp already cast (cheap ramp counts sooner, fast mana counts from turn 1)
  combo win turn   = first turn where (a) the deck has the mana the combo needs (Commander Spellbook's
                     'mana value needed', or the pieces' total mana value) and (b) in at least 1 game in 10
                     that every piece has been drawn or tutored by then. The commander is always available.
  extra turns      = each extra-turn spell beyond the first makes games shorter by about half a turn
Board-based wins (attacking, draining) are assumed to take until about turn 7, which is what Bracket 3 expects."""

import re
from math import comb

MIN_WIN_TURN = 6          # Bracket 3: games shouldn't be won before this turn
BOARD_WIN_TURN = 7        # a synergy deck winning through the board
DECK = 99


_ADD = re.compile(r"\badd (?:((?:\{[^}]+\})+)|(one|two|three) mana)", re.I)
_N = dict(one=1, two=2, three=3)


def _is_fast(c):
    """Fast mana: a non-land, non-creature card that makes MORE mana than it costs (Sol Ring, Moxen, Mana Crypt,
    Dark Ritual...). Mind Stone or Arcane Signet (cost 2, make 1) are normal ramp, not fast mana."""
    t = c.get("type_line") or ""
    if "Land" in t or "Creature" in t:
        return False
    m = _ADD.search(c.get("text") or "")
    if not m:
        return False
    cmc = c.get("cmc") or 0
    text = c.get("text") or ""
    line_start = text.rfind("\n", 0, m.start()) + 1
    cost = text[line_start:m.start()].split(":")[0] if ":" in text[line_start:m.start()] else ""
    if re.search(r"\{(?:\d+|[WUBRGCX])\}", cost):
        return False                       # the mana is behind another mana payment (Mossfire Egg, Kaleidostone)
    made = m.group(1).count("{") if m.group(1) else _N[m.group(2).lower()]
    return cmc <= 2 and made > cmc


def mana_curve(cards, turns=10):
    """Expected mana available on each turn 1..turns (index 0 = turn 1)."""
    fast = sum(_is_fast(c) for c in cards)
    cheap = sum(1 for c in cards if "ramp" in c["tags"] and "land" not in c["tags"] and not _is_fast(c) and (c.get("cmc") or 0) <= 2)
    mid = sum(1 for c in cards if "ramp" in c["tags"] and "land" not in c["tags"] and 3 <= (c.get("cmc") or 0) <= 4)
    out = []
    for t in range(1, turns + 1):
        seen = (7 + t - 1) / DECK                      # share of the deck seen by turn t
        lands = min(t, 1 + 0.93 * (t - 1))              # an occasional missed land drop
        ramp = 1.5 * fast * seen + cheap * ((7 + t - 3) / DECK if t >= 3 else 0) + mid * ((7 + t - 5) / DECK if t >= 5 else 0)
        out.append(lands + ramp)
    return out


def _p_at_least(k, outs, draws, deck=DECK):
    """Hypergeometric: chance of drawing at least k of `outs` cards in `draws` draws from `deck`."""
    draws = min(draws, deck)
    return sum(comb(outs, i) * comb(deck - outs, draws - i) for i in range(k, min(outs, draws) + 1)) / comb(deck, draws)


def combo_turn(combo, by_name, cmd_name, n_tutors, curve):
    """Estimated earliest turn this combo goes off in a fair share of games (>= 10%), or None.
    Each missing piece can be drawn or fetched by any tutor, so the 'outs' are the pieces plus the tutors."""
    pieces = [n for n in combo["_cards"] if n != cmd_name.lower() and n.split(" // ")[0] != cmd_name.lower().split(" // ")[0]]
    cost = combo.get("mv")
    if not cost:
        cost = sum((by_name.get(n) or {}).get("cmc") or 0 for n in combo["_cards"])
    for t, mana in enumerate(curve, start=1):
        if mana + 0.01 < cost:
            continue
        if not pieces or _p_at_least(len(pieces), len(pieces) + n_tutors, 7 + t - 1) >= 0.10:
            return t
    return None


def earliest_win(cards, cmd, db=None):
    """(turn, reason) for the earliest plausible win of a deck (cards = the 99's nonland picks, cmd = commander)."""
    curve = mana_curve(cards)
    tutors = sum("tutor" in c["tags"] for c in cards)
    xt = sum("extra_turn" in c["tags"] for c in cards)
    best, why = BOARD_WIN_TURN - 0.5 * max(0, xt - 1), "winning through the board"
    if db:
        by_name = {c["name"].lower(): c for c in cards + [cmd]}
        for c in cards + [cmd]:
            if " // " in c["name"]:
                by_name.setdefault(c["name"].split(" // ")[0].lower(), c)
        for k in db.complete([c["name"] for c in cards] + [cmd["name"]]):
            if k.get("templates") or not k.get("relevant", True):
                continue
            t = combo_turn(k, by_name, cmd["name"], tutors, curve)
            if t is not None and t < best:
                best, why = t, "a combo: " + " + ".join(k["cards"])
    return round(best, 1), why
