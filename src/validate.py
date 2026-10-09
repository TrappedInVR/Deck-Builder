"""Final deck check: every deck the builder makes must pass these rules, whatever the commander, colors or vibe.
Anything that fails is reported in the run summary (and fails the regression tests), so a whole class of bug
can't hide behind one commander.

Rules
  1. exactly 100 cards               2. singleton (basics and "any number" cards excepted)
  3. every card in the commander's color identity and Commander-legal (present in the card data)
  4. land count = the vibe's land target, counting EVERY land-type card (artifact lands included)
  5. no card that needs a color/basic type the deck can't have
  6. no any-color lands in one-color/colorless decks   7. no banned power lands, no mass land denial
  8. at most 3 Game Changers (Bracket 3)             9. extra turns / tutors within the vibe's limits
 10. no forbidden combos (early two-card wins), unless you forced the pieces in with Must include"""
import re

import combos as K
import lands as L

ANY_NUMBER = re.compile(r"a deck can have any number of cards named", re.I)


def check(idx, lines, cmd, plan, combo_db=None, max_tag="S", off_color=None):
    """lines: 'N Card Name' strings (commander first). Returns a list of problems ('' list = deck is valid)."""
    problems = []
    deck = []
    for ln in lines:
        m = re.match(r"^(\d+)\s+(.+)$", ln.strip())
        if m:
            deck.append((int(m.group(1)), m.group(2)))
    total = sum(n for n, _ in deck)
    if total != 100:
        problems.append(f"deck has {total} cards, not 100")
    ident = set(cmd["identity"])
    lands = gc = xt = tut = 0
    tapped_lands = []
    must = {n.lower() for n in plan.get("must", [])}
    for n, name in deck:
        c = idx.get(name.lower())
        if c is None:
            problems.append(f"{name}: not in the Commander-legal card data")
            continue
        basic = "Basic" in c["type_line"]
        if n > 1 and not basic and not ANY_NUMBER.search(c.get("text") or ""):
            problems.append(f"{name}: {n} copies in a singleton deck")
        if not set(c["identity"]) <= ident:
            problems.append(f"{name}: outside the commander's colors")
        if "land" in c["tags"]:
            lands += n
            if not basic and len(ident) <= 1 and re.search(r"add one mana of any color|commander's color identity", c.get("text") or "", re.I):
                problems.append(f"{name}: an any-color land in a one-color deck")
            if not basic and re.search(r"enters(?: the battlefield)? tapped(?! unless)|doesn't untap during your untap step", c.get("text") or "") \
                    and not re.search(r"you may pay 2 life", c.get("text") or ""):
                tapped_lands.append(name)
            if c["name"].lower() in L.POWER_LANDS:
                problems.append(f"{name}: a banned power land (house rule)")
        if off_color and c is not cmd and off_color(c, ident):
            problems.append(f"{name}: needs a color or land type this deck can't have")
        if "mass_land_denial" in c["tags"]:
            problems.append(f"{name}: mass land denial (not allowed in Bracket 3)")
        gc += n * bool(c.get("game_changer"))
        if c is not cmd:
            xt += n * ("extra_turn" in c["tags"])
            tut += n * ("tutor" in c["tags"])
    if len(tapped_lands) > L.MAX_TAPPED:
        problems.append(f"{len(tapped_lands)} tapped lands (max {L.MAX_TAPPED}): {', '.join(tapped_lands)}")
    if lands != plan["lands"]:
        problems.append(f"{lands} lands instead of {plan['lands']} (counting every land-type card)")
    if gc > plan.get("max_gc", 3):
        problems.append(f"{gc} Game Changers (Bracket 3 allows {plan.get('max_gc', 3)})")
    vibe = plan["vibe"]
    if xt > vibe["max_extra_turns"] and not must:
        problems.append(f"{xt} extra-turn cards (vibe allows {vibe['max_extra_turns']})")
    if tut > vibe["max_tutors"] and not must:
        problems.append(f"{tut} tutors (vibe allows {vibe['max_tutors']})")
    if combo_db:
        for k in combo_db.complete([name for _, name in deck]):
            if k.get("templates") or K.allowed(k, max_tag):
                continue
            if all(x in must or x == cmd["name"].lower() for x in k["_cards"]):
                continue                       # you forced it in; reported separately as a warning
            problems.append(f"forbidden combo: {K.describe(k)} [{K.TAG_NAMES.get(k['tag'], k['tag'])}]")
    return problems
