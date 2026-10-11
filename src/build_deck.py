"""Commander deck builder (v4). No AI calls, $0 to run. Every deck targets Bracket 3.

Flow:  vibe + colors + themes  ->  commander (typed, auto-picked, or randomized)
       ->  commander analysis (analyze.py: its game plans, mana, stats, what it already does for you)
       ->  every legal card scored on:  synergy with the commander's plans  (biggest weight)
                                        card quality vs the best cards for its job (evaluate.py)
                                        your themes, vibe and tribe
                                        a little EDHREC popularity
       ->  100-card list + a report of why each card is there and what was edged out.

Typed Commander always wins: if you type one, the commander/colors dropdowns are ignored. The vibe and
themes still shape the 99, but never by throwing away what the commander actually does."""
import argparse
import json
import os
import random
import re
import zlib
import sys
from collections import Counter

import analyze as A
import combos as K
import commanders as CM
import conditions as CN
import speed as SP
import strategy as ST
import cardfn as CF
import sim as SIM
import reference as RF
import evaluate as E
import lands as L
import options as O
import validate as V
from common import face_keys, load_cards, pips

BASICS = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
BASIC_TYPES = (("Plains", "W"), ("Island", "U"), ("Swamp", "B"), ("Mountain", "R"), ("Forest", "G"))
ROLE_KEYS = ("ramp", "draw", "removal", "counter", "sweeper", "recursion", "protection")
ROLE_NAMES = dict(ramp="Ramp", draw="Card draw", removal="Removal", counter="Counterspells", sweeper="Board wipes",
                  recursion="Recursion", protection="Commander protection")

# weights for the overall card score
W_SYN, W_Q, W_POP = 1.5, 0.55, 0.35
STAPLE_Q = 75           # staple = widely played (EDHREC top STAPLE_RANK) AND efficient at its job for its mana value ...
STAPLE_RANK = 500
SUB_MARGIN = 10         # a synergy card may replace a staple only if it is at most this much worse at the job
STAPLE_ROLES = ("ramp", "draw", "removal", "sweeper")   # staples first in these slots (see staple_first_fill)
STAPLE_Q_ALONE = 90     # ... or so efficient it's a staple whatever its popularity
LINK_W = 12             # value of one full link to the rest of the deck (cardfn.DeckWeb), in card-score points
REF_W = 22              # synergy points for a card real decks run with this commander (reference.py), at full score
SIM_GAMES = 400         # games per deck version in the simulator (same shuffles for every version)
SIM_SWAPS = 8           # at most this many simulator swaps per deck
SIM_GAIN = 0.08         # a swap must make the deck kill at least this many turns faster on average
INSURANCE = {"removal", "protection", "board_protect", "counter", "sweeper"}
LINK_CAP = 2.5          # links beyond this don't add more (a card can't be MORE than fully woven in)
SYN_ON_PLAN = 8          # synergy at/above this = "on plan" for counting
MAX_BUDGET = 1000.0      # house rule: nonland cards only; lands are never counted
DEFAULT_BUDGET = 500.0   # what a blank Budget box means (vibes may stretch above it, see options.budget_stretch)
COMBO_DB = None          # set by main() from data/combos.json (Commander Spellbook); None = no combo checks


# ----------------------------------------------------------------------------- small helpers
def price(c):
    try:
        return float(c["usd"]) if c.get("usd") else 0.5
    except ValueError:
        return 0.5


def is_member(c, tribe):
    """One definition of 'counts as the tribe', used for scoring AND counting. Works for one type or several
    ('Angel/Demon/Dragon'), matches whole words only (so 'Ape' doesn't match 'Shapeshifter'); Changelings count."""
    if not tribe:
        return False
    tl = c["type_line"]
    if "Creature" not in tl:
        return False
    sub = tl.split("—", 1)[1] if "—" in tl else ""
    if "changeling" in (c.get("text") or "").lower():
        return True
    return any(re.search(r"\b%s\b" % re.escape(t), sub, re.I) for t in CM.tribe_types(tribe))


def mentions_tribe(c, tribe):
    x = c.get("text") or ""
    return any(re.search(r"\b%s(?:s|es)?\b" % re.escape(t), x, re.I) for t in CM.tribe_types(tribe))


def land_makes(c, ident):
    """Colors (within identity) a land can make: 'produces', basic land types, and rules text."""
    s = set(c.get("produces") or [])
    t, x = c["type_line"], (c.get("text") or "")
    for ty, col in BASIC_TYPES:
        if ty in t:
            s.add(col)
    for m in re.finditer(r"[Aa]dd ([^.\n]*)", x):
        s |= set(re.findall(r"\{([WUBRG])\}", m.group(1)))
    if re.search(r"any color|any type|commander's color identity", x, re.I):
        s |= set(ident)
    return s & set(ident)


_WORD_COLOR = {"plains": "W", "island": "U", "swamp": "B", "mountain": "R", "forest": "G",
               "white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
# Things a card needs YOU to have: basic land types, colored permanents/spells, devotion. Hosers that target an
# opponent's colors ("destroy target blue permanent") are not requirements and are judged by evaluate.py instead.
_NEEDS = [
    re.compile(r"\b(Plains|Island|Swamp|Mountain|Forest)s? (?:you control|cards? (?:in|from) your)", re.I),
    re.compile(r"(?:if|as long as|unless) you control (?:an?|two or more|three or more|\w+) (Plains|Island|Swamp|Mountain|Forest)\b", re.I),
    re.compile(r"\b(white|blue|black|red|green) (?:permanents?|creatures?|spells?|sources?|cards?) you (?:control|cast|own)", re.I),
    re.compile(r"(?:if|as long as|unless) you control an? (white|blue|black|red|green) (?:permanent|creature)", re.I),
    re.compile(r"whenever you cast an? (white|blue|black|red|green) spell", re.I),
    re.compile(r"devotion to (white|blue|black|red|green)", re.I),
]


def off_color_lands(c, ident):
    """True if the card needs a color or basic land type this deck can't have
    (Vedalken Shackles in mono-red, 'Whenever you cast a blue spell' in Rakdos, devotion to green in Boros...).
    A requirement counts as met if ANY of the colors it mentions is in the deck."""
    x = c.get("text") or ""
    need = set()
    for rx in _NEEDS:
        need |= {_WORD_COLOR[m.lower()] for m in rx.findall(x)}
    return bool(need) and not (need & set(ident))


def gc_ok(c):
    """Game Changers worth a slot: card advantage, interaction, protection or permanent ramp."""
    tags = E.role_tags(c)
    if tags & {"draw", "counter", "removal", "protection"}:
        return True
    t = c["type_line"]
    return "ramp" in tags and "Instant" not in t and "Sorcery" not in t


_ALL_TYPES = {}


def all_types(idx):
    k = id(idx)
    if not A.ALL_TYPES:
        A.creature_types(CM.unique(idx))
    if k not in _ALL_TYPES:
        _ALL_TYPES[k] = frozenset(A.creature_types(CM.unique(idx)))
    return _ALL_TYPES[k]


# ----------------------------------------------------------------------------- scoring
def theme_bonus(c, ctx):
    s, why = 0.0, ""
    if ctx["tribe"]:
        if is_member(c, ctx["tribe"]):
            s += 45; why = f"{ctx['tribe']} (your tribe)"
        elif mentions_tribe(c, ctx["tribe"]):
            s += 20; why = f"{ctx['tribe']} support"
    if ctx["mech"]:
        m = O.mech_score(c, ctx["mech"])
        if m:
            s += 8 * min(m, 3); why = why or f"{ctx['mech']} theme"
    if ctx["narr"]:
        n = O.narr_score(c, ctx["narr"])
        if n >= 2:
            s += 3 * min(n, 5); why = why or f"{ctx['narr']} flavor"
    vt = O.vibe_tags(c)
    for slot in ctx["vibe"]["slots"]:
        if slot in vt:
            s += 6; why = why or f"{slot} ({ctx['vibe_label'].split(':')[0]} vibe)"
    return s, why


def prepare(c, ctx):
    """Compute and attach every score the builder needs for this card."""
    syn, syn_why = A.synergy(c, ctx["profile"], ctx["typal"])
    ref = ctx.get("ref")
    r, rwhy = ref.score(c) if ref else (0.0, "")
    c["_ref"] = rwhy
    if r > 0:
        syn = min(syn + REF_W * r, A.SYN_CAP)
        c["_needs"] = sorted(set(c.get("_needs") or []) | {"reference"})
        if r >= 0.5 or not syn_why:
            syn_why = f"{syn_why}; {rwhy}" if syn_why else rwhy
    a = E.assess(c)
    # a deck-role card (ramp, draw, removal, protection...) that ALSO serves the commander saves a slot:
    # Ragavan (ramp + a body that counts), Mother of Runes (protection + a body), Young Pyromancer (bodies + more bodies)
    useful_roles = set(a["roles"]) & {"ramp", "draw", "removal", "counter", "recursion", "protection", "sweeper"}
    if useful_roles and c.get("_needs") and syn > 0:
        syn = min(syn * 1.3, A.SYN_CAP)
        c["_needs"] = c["_needs"] + ["role:" + "/".join(sorted(useful_roles))]
    th, th_why = theme_bonus(c, ctx)
    pop = CM.popularity(c) * ctx["vibe"]["pop"]
    # diminishing returns past 40 synergy: a card that is merely "very on-plan" but weak at what it does
    # shouldn't bury a strong card that is clearly on-plan
    syn_eff = syn if syn <= 40 else 40 + 0.5 * (syn - 40)
    s = W_SYN * syn_eff + W_Q * a["q"] + W_POP * pop + th
    if syn <= 0 and th <= 0 and not a["roles"]:
        s -= 10                                     # generic filler: only if nothing better exists
    if c["cmc"] >= 7 and syn < SYN_ON_PLAN:
        s -= 8
    c["_syn"], c["_syn_why"], c["_q"], c["_th"], c["_th_why"], c["_pop"], c["_s"] = syn, syn_why, a["q"], th, th_why, pop, s


def is_staple(c):
    """Commander staples: cards good enough for their mana value to earn a slot even without synergy
    (Sol Ring, Swords to Plowshares, Arcane Signet, Lightning Greaves...). The one exception to synergy-first."""
    if c.get("_staple") is not None:
        return c["_staple"]                          # your data/overrides.json says so
    if not E.assess(c)["roles"]:
        return False
    return (c["_q"] >= STAPLE_Q and (c.get("rank") or 10 ** 6) <= STAPLE_RANK) or c["_q"] >= STAPLE_Q_ALONE


def role_score(c, role):
    """Compare cards for a role: how good they are AT the role first, then how well they fit this deck."""
    # quality decides; synergy and themes break ties between similar cards (capped so a weak on-plan card
    # can't beat a much better one at the actual job)
    staple = 10 if is_staple(c) else 0              # Sol Ring, Swords to Plowshares...: the exception to synergy-first
    return E.role_quality(c, role) + 0.4 * min(c["_syn"], 40) + 0.4 * c["_pop"] + 0.2 * min(c["_th"], 30) + staple


# ----------------------------------------------------------------------------- the deck builder
def build(idx, cmd, plan):
    vibe, tribe = plan["vibe"], plan["tribe"]
    mech = None if plan["mech"] in (None, "", O.ANY) else plan["mech"]
    narr = None if plan["narr"] in (None, "", O.ANY) else plan["narr"]
    ident = set(cmd["identity"])
    avoid = set(plan["avoid"]) | set(vibe["avoid"])
    budget, lands, max_gc = plan["budget"], plan["lands"], plan["max_gc"]
    gc_target = min(vibe["gc_target"], max_gc)
    profile = A.analyze(cmd, all_types(idx), vibe)
    if not profile["plans"] and mech and A.plan_for_theme(mech):
        # the commander has no engine of its own: your theme becomes the deck's plan
        profile["plans"] = [dict(name=A.plan_for_theme(mech), weight=3.0, why=[f"your {mech} theme (the commander has no engine of its own)"])]
    # ONE game plan, decided by what the commander does (analyze.thesis): the lead route is the plan; the others are
    # only side effects (a card that fits them must ALSO serve the plan to be "on plan")
    th = profile.get("thesis") or {}
    if th.get("lead"):
        lead = next((p for p in profile["plans"] if p["name"] == th["lead"]), None) or \
            next((dict(name=r["name"], weight=r["weight"], why=r["why"], style=r["style"]) for r in profile["routes"]
                  if r["name"] == th["lead"]), None)
        if lead:
            profile["side_plans"] = [p["name"] for p in profile["plans"] if p["name"] != lead["name"]]
            profile["plans"] = [lead]
    typal = [t for t in profile["typal"] if t.lower() not in {x.lower() for x in CM.tribe_types(tribe)}]
    fit, natural, _raw = A.vibe_fit(cmd, O.VIBES)
    vmatch = A.fit_label(fit[plan["vibe_label"]], natural == plan["vibe_label"])
    ctx = dict(tribe=tribe, vibe=vibe, vibe_label=plan["vibe_label"], mech=mech, narr=narr, profile=profile, typal=typal)
    warnings = list(plan.get("warnings", []))
    # typed card names: use the closest real card if the spelling is off
    plan.setdefault("reference", [])
    for key, what in (("must", "Must include"), ("exclude", "Exclude"), ("reference", "Reference")):
        fixed = []
        for n in plan[key]:
            if key == "reference" and not n.strip():
                continue
            c, fix = CM.resolve_card(idx, n, f"{what} card")
            fixed.append(c["name"])
            if fix and fix not in warnings:
                warnings.append(fix)
        plan[key] = fixed
    excluded = {n.lower() for n in plan["exclude"]}
    # real-deck synergy reference: EDHTop16 tournament lists, MTGJSON precons, your Reference box (reference.py)
    ref = RF.Reference(cmd, plan.get("reference", []), data_dir=os.path.dirname(plan.get("cards_path") or "data/cards.json") or ".",
                       online=not os.environ.get("DECK_OFFLINE"), log=lambda m: warnings.append(m) if "no tournament" not in m else None)
    ctx["ref"] = ref

    pool = [c for c in CM.unique(idx)
            if set(c["identity"]) <= ident and c["name"] != cmd["name"]
            and c["name"].lower() not in excluded
            and "mass_land_denial" not in c["tags"]
            and not O.avoided(c, avoid)
            and not off_color_lands(c, ident)]
    nonland = [c for c in pool if "land" not in c["tags"]]
    for c in nonland:
        prepare(c, ctx)
    nonland.sort(key=lambda c: (-c["_s"], c["name"]))

    n_nonland = 99 - lands
    if plan["max_creatures"]:
        cap = plan["max_creatures"]
    elif tribe:
        cap = O.TRIBAL_MAX_CREATURES if plan["tribe_mode"] == "explicit" else 34
    else:
        opts = [v for v in (profile["creatures"], O.MECH_MAX_CREATURES.get(mech)) if v]
        cap = round(sum(opts) / len(opts)) if opts else O.DEFAULT_MAX_CREATURES
    picked, names, why = [], set(), {}
    taken = set(face_keys(cmd))           # every face name already in the deck (commander included)
    st = dict(spent=0.0, gc=1 if cmd["game_changer"] else 0, mech=0, narr=0, members=0, onplan=0)
    # Build order (Bracket 3 first, power second): the SYNERGY CORE is built with Game Changers, tutors and fast mana
    # held back, leaving room for them; only then are power cards added, and only if the deck still can't win
    # before turn 6 (speed.py).
    st["core"] = True
    st["limit"] = n_nonland
    tagc, vtc, planc = Counter(), Counter(), Counter()
    per_card_cap = max(5.0, budget * 0.10)
    stretch = plan.get("budget_stretch", 0.0)
    stretch_card_cap = max(per_card_cap, (budget + stretch) * 0.15)

    def worth_stretch(c):
        """Cards worth going above the default budget for: Game Changers, staples, and strong cards that clearly fit
        the commander. Everything else must fit the default budget."""
        if not stretch:
            return False
        return bool(c["game_changer"] or is_staple(c) or (c.get("_syn", 0) >= 30 and c.get("_q", 0) >= 70))
    tag_caps = {"extra_turn": vibe["max_extra_turns"], "tutor": vibe["max_tutors"]}
    if cmd["game_changer"] and st["gc"] > max_gc:
        warnings.append(f"{cmd['name']} is itself a Game Changer, so this deck is at least Bracket 3.")
    top_plans = [p["name"] for p in profile["plans"]]
    all_plans = profile["plans"] + profile.get("interactions", [])

    def add(c, step, reason):
        picked.append(c)
        names.add(c["name"])
        taken.update(face_keys(c))
        why[c["name"]] = (step, reason)
        st["spent"] += price(c)
        st["gc"] += c["game_changer"]
        tagc.update(E.role_tags(c))
        vtc.update(O.vibe_tags(c))
        st["mech"] += bool(mech and O.mech_score(c, mech) > 0)
        st["narr"] += bool(narr and O.narr_score(c, narr) >= 2)
        st["members"] += is_member(c, tribe)
        st["onplan"] += c["_syn"] >= SYN_ON_PLAN
        for p in all_plans:
            if A.plan_hits(c, p):
                planc[p["name"]] += 1

    def remove(c):
        picked.remove(c)
        names.discard(c["name"])
        taken.difference_update(face_keys(c))
        taken.update(face_keys(cmd))
        why.pop(c["name"], None)
        st["spent"] -= price(c)
        st["gc"] -= c["game_changer"]
        tagc.subtract(E.role_tags(c))
        vtc.subtract(O.vibe_tags(c))
        st["mech"] -= bool(mech and O.mech_score(c, mech) > 0)
        st["narr"] -= bool(narr and O.narr_score(c, narr) >= 2)
        st["members"] -= is_member(c, tribe)
        st["onplan"] -= c["_syn"] >= SYN_ON_PLAN
        for p in all_plans:
            if A.plan_hits(c, p):
                planc[p["name"]] -= 1

    db = COMBO_DB
    no_combos = "combos" in avoid
    max_tag = "E" if no_combos else vibe["combo_max"]          # never above S: Ruthless (R) is Bracket 4
    combos_added, combos_removed = [], []

    def is_power(c):
        """Cards that make a deck faster rather than more synergistic."""
        return bool(c["game_changer"] or "tutor" in c["tags"] or "extra_turn" in c["tags"] or SP._is_fast(c))

    def blocked(c, creature_cap=None, force=False, rules_only=False):
        """Why a card can't go in right now ('' = it can). rules_only: ignore 'deck full' (for the close-calls report)."""
        if c["name"] in names or face_keys(c) & taken:
            return "already in (a double-faced card counts once)"
        if not rules_only:
            if len(picked) >= st["limit"]:
                return "deck full"
            if creature_cap is not None and "creature" in c["tags"] and tagc["creature"] >= creature_cap:
                return "creature count reached"
        if force:
            return ""
        if c.get("_never"):
            return "marked 'never' in data/overrides.json"
        if c.get("_avoid"):
            return f"works against the game plan ({c['_avoid']})"
        worth = worth_stretch(c)
        if price(c) > (stretch_card_cap if worth else per_card_cap):
            return f"too pricey (${price(c):.0f})"
        if st["spent"] + price(c) > budget + (stretch if worth else 0):
            return "over budget" + (f" (even with the vibe's ${stretch:.0f} stretch)" if worth and stretch else "")
        if c["game_changer"] and st["gc"] >= gc_target:
            return "Game Changer limit (Bracket 3)" if st["gc"] >= max_gc else "Game Changer target for this vibe reached"
        power = is_power(c)
        if power and st["core"] and not (is_staple(c) and not c["game_changer"] and "tutor" not in c["tags"]
                                          and "extra_turn" not in c["tags"]):   # Sol Ring & co. are staples, not "power"
            return "power card: waits until the synergy core is built"
        if power and not rules_only:
            t, how = SP.earliest_win(picked + [c], cmd, db)
            if t < SP.MIN_WIN_TURN:
                return f"would let the deck win around turn {t:g} ({how}); Bracket 3 aims for turn {SP.MIN_WIN_TURN}+"
        for t, capn in tag_caps.items():
            if t in c["tags"] and tagc[t] >= capn:
                return f"{t.replace('_', ' ')} limit for this vibe"
        return ""

    def try_add(c, step, reason, force=False, creature_cap=None):
        if blocked(c, creature_cap, force):
            return False
        add(c, step, reason)
        return True

    def fill(cands, pred, have, target, step, reason, creature_cap=cap):
        for c in cands:
            if have() >= target or len(picked) >= st["limit"]:
                return
            if pred(c):
                try_add(c, step, reason(c), creature_cap=creature_cap)

    def short(c):
        return c["_syn_why"] or c["_th_why"] or "solid card"

    reserve = (gc_target + min(vibe["max_tutors"], 2) + (2 * vibe["combo_slots"] if db and "combos" not in avoid else 0))
    st["limit"] = n_nonland - reserve

    # 1. cards you demanded
    for n in plan["must"]:
        c = idx.get(n.lower())
        if c and set(c["identity"]) <= ident and "land" not in c["tags"]:
            if "_s" not in c:
                prepare(c, ctx)
            try_add(c, "You asked for it", "must-include", force=True)
        else:
            warnings.append(f"Must-include card unavailable, off-color, or a land: {n}")

    # 3. role quotas: vibe defaults adjusted by what the commander already does
    quotas = dict(vibe["quotas"])
    quotas["protection"] = 0
    for r, d in profile["adjust"].items():
        quotas[r] = max(0, quotas.get(r, 0) + d)
    if "U" not in ident or "counterspells" in avoid:
        quotas["removal"] += quotas["counter"] // 2
        quotas["counter"] = 0
    if "wipes" in avoid:
        quotas["sweeper"] = 0
    if any(p in top_plans[:2] for p in ("Tokens / go wide", "Combat / attack triggers", "Voltron / equipment & auras")) or tribe:
        quotas["sweeper"] = min(quotas["sweeper"], 1)  # creature decks run few (ideally one-sided) wipes
    def staple_first_fill(role, need, cands):
        """Ramp, draw, removal and board wipes: staples by default. A staple only loses its slot to a card that does
        the same job for EQUAL OR LESS mana, nearly as well (role quality within SUB_MARGIN), and fits the commander's
        plan clearly better."""
        staples = [c for c in cands if is_staple(c)]
        staples.sort(key=lambda c: -E.role_quality(c, role))
        subs = [c for c in cands if not is_staple(c) and c["_syn"] >= SYN_ON_PLAN and E.role_quality(c, role) >= 50]
        for st_card in staples:
            if tagc[role] >= need or len(picked) >= st["limit"]:
                return
            if blocked(st_card):
                continue
            sq = E.role_quality(st_card, role)
            better = [x for x in subs if x["name"] not in names and (x.get("cmc") or 0) <= (st_card.get("cmc") or 0)
                      and x["_syn"] >= st_card["_syn"] + SYN_ON_PLAN
                      and E.role_quality(x, role) >= sq - SUB_MARGIN      # a real substitute: nearly as good at the job
                      and (role != "removal" or E.removal_reach(x) >= E.removal_reach(st_card))   # answers as much
                      and not blocked(x, creature_cap=cap)]
            if better:
                x = max(better, key=lambda c: (c["_syn"], E.role_quality(c, role)))
                add(x, ROLE_NAMES[role], f"{E.describe(x)}; instead of the staple {st_card['name']}: same job for "
                                         f"{'less' if (x.get('cmc') or 0) < (st_card.get('cmc') or 0) else 'the same'} mana, "
                                         f"and it fits {x['_syn_why']}{needs_text(x)}")
            else:
                add(st_card, ROLE_NAMES[role], f"{E.describe(st_card)}; staple"
                    + (f", also fits {st_card['_syn_why']}" if st_card["_syn"] >= SYN_ON_PLAN else ""))

    role_rank = {}
    for role, need in quotas.items():
        if need <= 0:
            continue
        cands = sorted((c for c in nonland if role in E.role_tags(c)), key=lambda c: -role_score(c, role))
        role_rank[role] = cands
        if role in STAPLE_ROLES:
            staple_first_fill(role, need, cands)
        fill(cands, lambda c: True, lambda r=role: tagc[r], need, ROLE_NAMES[role],
             lambda c, r=role: E.describe(c) + (f"; fits {c['_syn_why']}{needs_text(c)}" if c["_syn"] >= SYN_ON_PLAN else ""))

    # 3c. the strategic game plan (strategy.py): what multiplies the commander, what protects the plan
    strat = profile.get("strategy") or {}
    for need in strat.get("needs", []):
        ok = lambda c, n=need: ST.meets(c, n) and c["_q"] >= 30
        fill(nonland, ok, lambda ok=ok: sum(1 for x in picked if ok(x)), need["quota"], f"Game plan: {need['label']}",
             lambda c, n=need: f"{n['label']}: {E.describe(c)}{needs_text(c)}")

    # 3a. real-deck reference (reference.py): your Reference list first, then cards most tournament decks / the
    #     official precon run with this commander. Still subject to every rule (colors, Bracket 3, budget, caps).
    ref_strong = lambda c: ref.score(c)[0] >= 0.6 and c["_q"] >= 25
    n_ref = sum(1 for c in nonland if ref_strong(c))
    fill(nonland, ref_strong, lambda: sum(1 for x in picked if ref_strong(x)), min(14, n_ref), "Real-deck reference",
         lambda c: f"{c['_ref']}: {E.describe(c)}{needs_text(c)}")

    # 3b. cards that COUNT for the commander's condition ("creatures with power 2 or less", "with defender"...)
    conds = profile.get("conditions") or []
    for r in conds:
        if not r["strong"] and not CN.counts_creatures([r]):
            continue
        target = 22 if r["type"] in ("creature", "token") else 14
        ok = lambda c, r=r: CN.judge(c, r)[0] in ("meets", "makes") and c["_q"] >= 30
        fill(nonland, ok, lambda ok=ok: sum(1 for x in picked if ok(x)), target, "Counts for the commander",
             lambda c: f"{c['_syn_why']}: {E.describe(c)}{needs_text(c)}")

    # 4. THE game plan (one, from the commander's role): cards must serve it, not just share a keyword with it
    serves = set(th.get("serves") or [])

    def serves_plan(c):
        return bool(set(c.get("_needs") or []) & serves) or not serves

    if th.get("role") == "enabler" and th.get("beneficiary"):
        ben = th["beneficiary"]
        is_b = lambda c: A.is_beneficiary(c, ben) and c["_q"] >= 30
        fill(nonland, is_b, lambda: sum(1 for x in picked if A.is_beneficiary(x, ben)),
             22 if ben["kind"] == "creature_type" else 26, "Commander plan",
             lambda c: f"buffed by your commander ({ben['label']}): {E.describe(c)}{needs_text(c)}")
    if profile["plans"]:
        total = 26 if not (mech or narr or tribe) else 22
        p = profile["plans"][0]
        fill(nonland, lambda c, pl=p: (A.plan_hits(c, pl) or th.get("role") in ("finisher", "enabler"))
             and serves_plan(c) and c["_syn"] >= SYN_ON_PLAN and c["_q"] >= 32,
             lambda: sum(1 for x in picked if serves_plan(x) and x["_syn"] >= SYN_ON_PLAN), total, "Commander plan",
             lambda c, name=p["name"]: f"{c['_syn_why'] or name}: {E.describe(c)}{needs_text(c)}")
        if typal:
            fill(nonland, lambda c: c["_syn_why"].endswith("(named by your commander)"),
                 lambda: sum(1 for x in picked if x["_syn_why"].endswith("(named by your commander)")), 8,
                 "Commander plan", lambda c: c["_syn_why"])
    else:
        warnings.append("This commander has no specific engine in its text, so the deck follows your themes and the vibe.")

    finisher_cmd = (profile.get("role") or {}).get("role") == "finisher"
    # 4-. ability support: every important ability of the commander gets cards that enable / fuel / pay it off
    for p in profile.get("interactions", []):
        if p["weight"] < 1.5:
            continue
        target = 6 if p["kind"] in ("enabler", "fuel") else 4
        if p.get("finisher"):
            target += 2                     # the commander IS the win condition: enable it harder
        # when the commander is the finisher, cards that only USE what it produces (and do nothing else for it)
        # are luxuries: they don't make the win happen, so they don't get slots here
        payoff_only = lambda c: p["kind"] == "payoff" and finisher_cmd and set(c.get("_needs") or []) <= {"payoff", "plan"}
        fill(nonland, lambda c, pl=p: A.interaction_hits(c, pl) > 0 and c["_q"] >= 35 and not payoff_only(c),
             lambda pl=p: planc[pl["name"]], target, "Enable the finisher" if p.get("finisher") else "Ability support",
             lambda c, pl=p: f"{pl['name']} ({pl['why'][0][:60]}): {E.describe(c)}")

    # 4-b. numeric requirement of the winning ability ('Tap ten untapped Elves'): run enough of them
    role = profile.get("role") or {}
    req = role.get("requires")
    req_floor = 0
    if req:
        need = req["count"] + 4
        t = req["type"]
        if t == "creature":
            req_floor = need
        else:
            if t in ("artifact", "enchantment", "planeswalker"):
                member = lambda c, t=t: t.title() in c["type_line"]
            else:
                member = lambda c, t=t: is_member(c, t)
            fill(nonland, member, lambda m=member: sum(1 for x in picked if m(x)), need, "Enable the finisher",
                 lambda c, r=req: f"it needs {r['count']} {r['noun']} ('{r['text']}'): {E.describe(c)}")

    # 4-c. win conditions: an engine/value commander needs cards that turn its advantage into a win;
    #      a finisher commander keeps a couple as backup in case it's removed
    win_target = {"finisher": 2, "enabler": 3, "engine": 4, "value": 5}.get(role.get("role"), 4)
    fill(nonland, lambda c: bool(A.is_wincon(c)) and c["_q"] >= 30 and (c["_syn"] >= SYN_ON_PLAN or c["_q"] >= 45),
         lambda: sum(1 for x in picked if A.is_wincon(x)), win_target, "Win condition",
         lambda c: f"{A.is_wincon(c)}: {E.describe(c)}")

    # 4b. enough creatures for the plan (a voltron or spells deck still needs blockers and bodies)
    floor = max(8, min(cap - 4, round(0.75 * (profile["creatures"] or 26)))) if not plan["max_creatures"] else 0
    if any(p in top_plans[:1] for p in ("Spellslinger (instants & sorceries)",)):
        floor = min(floor, 10)
    if any(p in top_plans[:1] for p in ("Voltron / equipment & auras", "Superfriends (planeswalkers)", "Enchantments", "Artifacts")):
        floor = min(floor, 12)
    floor = max(floor, min(req_floor, cap))
    fill(nonland, lambda c: "creature" in c["tags"], lambda: tagc["creature"], floor, "Creature base",
         lambda c: lambda_reason(c))

    # 5. the vibe's signature slots (picked by score, so on-plan versions win)
    for slot, need in vibe["slots"].items():
        fill(nonland, lambda c, s=slot: s in O.vibe_tags(c), lambda s=slot: vtc[s], need,
             "Vibe", lambda c, s=slot: f"{s} ({plan['vibe_label'].split(':')[0]}): {E.describe(c)}")

    # cards that work AGAINST the commander's plan (a 12/12 for Doran, an anthem for Arabella, a wipe in a go-wide deck)
    # never come in through themes, tribe or filler, even if they match your theme
    strong_conds = [r for r in (profile.get("conditions") or []) if r.get("strong")]

    def fights_plan(c):
        if c.get("_avoid"):
            return True
        return any(CN.judge(c, r)[0] in ("misses", "breaks") for r in strong_conds)

    # 6. your themes (mechanical first, narrative is a softer nudge)
    heavy = bool(tribe)
    if mech:
        fill(nonland, lambda c: O.mech_score(c, mech) > 0 and not fights_plan(c), lambda: st["mech"], 14 if heavy else 18,
             "Your theme", lambda c: f"{mech}: {E.describe(c)}")
    if narr:
        fill(nonland, lambda c: O.narr_score(c, narr) >= 2 and not fights_plan(c), lambda: st["narr"], 7 if (heavy or mech) else 10,
             "Your theme", lambda c: f"{narr}: {E.describe(c)}")

    # 7. tribe members (only when a tribe is in play)
    if tribe:
        tcount = plan["tribe_count"] or (22 if plan["tribe_mode"] == "explicit" else 14)
        fill(nonland, lambda c: is_member(c, tribe) and not fights_plan(c), lambda: st["members"], tcount,
             "Tribe", lambda c: f"{tribe}: {E.describe(c)}")

    # ---- POWER STAGE: the synergy core is done; now Game Changers, combos, tutors and fast mana may join,
    #      each only if the deck still can't win before turn 6
    st["core"] = False
    st["limit"] = n_nonland
    # P1. Game Changers: the ones that help THIS deck most, up to the vibe's target
    def gc_key(x):
        roles = E.assess(x)["roles"]
        value = 12 if set(roles) & {"draw", "removal", "counter", "protection", "sweeper"} else 0   # card advantage / interaction first
        return -(x["_s"] + 0.5 * x["_q"] + value + 0.5 * min(x["_syn"], 40))
    gcs = sorted((x for x in nonland if x["game_changer"] and gc_ok(x)), key=gc_key)
    for c in gcs:
        if st["gc"] >= gc_target:
            break
        try_add(c, "Game Changer", f"{E.describe(c)}" + (f"; fits {c['_syn_why']}" if c["_syn_why"] else ""))

    # P2. combos (Commander Spellbook): only ones this vibe allows, that run through the commander or its plan
    cmd_l = cmd["name"].lower()
    by_name = {c["name"].lower(): c for c in nonland}
    for c in nonland:
        if " // " in c["name"]:
            by_name.setdefault(c["name"].split(" // ")[0].lower(), c)
    if db and vibe["combo_slots"] and not no_combos:
        seeds = {cmd_l} | {c["name"].lower() for c in nonland if c["_syn"] >= SYN_ON_PLAN}
        options_ = {}
        for sname in seeds:
            for k in db.involving(sname):
                if id(k) in options_ or k.get("templates") or not K.allowed(k, max_tag) or not k.get("relevant", True):
                    continue
                if any(n.lower() != cmd_l for n in k.get("commander") or []):
                    continue                                   # needs a different commander
                pieces = [n for n in k["_cards"] if n != cmd_l]
                if not pieces or len(pieces) > 3 or any(n not in by_name for n in pieces):
                    continue
                pc = [by_name[n] for n in pieces]
                fits = cmd_l in k["_cards"] or any(x["_syn"] >= SYN_ON_PLAN for x in pc)
                avg_q = sum(x["_q"] for x in pc) / len(pc)
                if not fits or avg_q < 40:
                    continue
                value = (sum(min(x["_syn"], 40) for x in pc) / len(pc) + 0.3 * avg_q + K.popularity_bonus(k)
                         + (25 if cmd_l in k["_cards"] else 0) - 6 * sum(1 for x in pc if x["name"] not in names))
                options_[id(k)] = (value, k, pc)
        for value, k, pc in sorted(options_.values(), key=lambda r: -r[0]):
            if len(combos_added) >= vibe["combo_slots"]:
                break
            new = [x for x in pc if x["name"] not in names]
            if len(picked) + len(new) > n_nonland or any(blocked(x) for x in new):
                continue
            if SP.earliest_win(picked + new, cmd, db)[0] < SP.MIN_WIN_TURN:   # too fast for Bracket 3
                continue
            for x in new:
                add(x, "Combo", f"{K.describe(k)} [{K.TAG_NAMES.get(k['tag'], k['tag'])}]")
            combos_added.append(k)

    # 8. fill the rest by overall score, without letting any one theme take over
    def over_cap(c):
        if mech and st["mech"] >= 30 and O.mech_score(c, mech) > 0 and c["_syn"] < SYN_ON_PLAN:
            return True
        if narr and st["narr"] >= 16 and O.narr_score(c, narr) >= 2 and c["_syn"] < SYN_ON_PLAN:
            return True
        vt = O.vibe_tags(c)
        return any(slot in vt and vtc[slot] >= need * 2 for slot, need in vibe["slots"].items())

    for c in nonland:                          # first: only cards that serve THE plan
        if len(picked) >= n_nonland:
            break
        if serves_plan(c) and c["_q"] >= 30 and not over_cap(c) and not fights_plan(c):
            try_add(c, "Best remaining fit", lambda_reason(c), creature_cap=cap)
    for c in nonland:
        if len(picked) >= n_nonland:
            break
        if not over_cap(c) and not fights_plan(c):
            try_add(c, "Best remaining fit", lambda_reason(c), creature_cap=cap)
    for c in nonland:
        if len(picked) >= n_nonland:
            break
        try_add(c, "Best remaining fit", lambda_reason(c))

    # 8b. DECK SYNERGY PASS: judge every card against the finished deck, not just the commander. Cards that link to
    #     nothing (the deck neither feeds them nor uses what they make) are swapped for candidates that link better,
    #     unless they are protected: must-includes, power cards, staples (quality >= STAPLE_Q), and cards a role or
    #     game-plan quota still needs.
    web = CF.DeckWeb(picked + [cmd])
    deck_swaps = []

    def dvalue(c, inside):
        return c["_s"] + LINK_W * min(web.links(c, inside)[0], LINK_CAP)

    strat_needs = (profile.get("strategy") or {}).get("needs", [])
    must_l = {n.lower() for n in plan["must"]}

    def protected(c):
        if c["name"].lower() in must_l or is_power(c) or is_staple(c):
            return True
        for r in E.role_tags(c):
            if quotas.get(r, 0) > 0 and tagc[r] - 1 < quotas[r]:
                return True
        for n in strat_needs:
            if ST.meets(c, n) and sum(1 for x in picked if ST.meets(x, n)) - 1 < n["quota"]:
                return True
        return False

    def jobs(c):
        """The deck jobs a card does: its roles that have a quota, and the game-plan needs it covers."""
        return ({r for r in E.role_tags(c) if quotas.get(r, 0) > 0} |
                {n["id"] for n in strat_needs if ST.meets(c, n)})

    pool_in = [c for c in nonland[:250] if c["_q"] >= 30 and not is_power(c) and not c.get("_avoid")]
    for _ in range(15):
        outs = sorted((c for c in picked if not protected(c)), key=lambda c: dvalue(c, True))[:6]
        ins = sorted((c for c in pool_in if c["name"] not in names and not (face_keys(c) & taken)),
                     key=lambda c: -dvalue(c, False))[:60]
        done = False
        for o in outs:
            vo = dvalue(o, True)
            need = jobs(o)                     # like-for-like: the replacement must do every job the old card did
            for c in ins:
                if dvalue(c, False) <= vo + 8:
                    break
                if not need <= jobs(c):
                    continue
                old = why[o["name"]]
                remove(o)
                web.remove(o)
                if blocked(c, creature_cap=cap):
                    add(o, *old)
                    web.add(o)
                    continue
                web.add(c)
                _, lr = web.links(c, True)
                add(c, "Deck synergy", f"links with the deck ({'; '.join(lr) or 'commander'}): {E.describe(c)}{needs_text(c)} "
                                       f"(replaces {o['name']})")
                deck_swaps.append(dict(out=o["name"], into=c["name"]))
                done = True
                break
            if done:
                break
        if not done:
            break

    # 8c. SIMULATION (sim.py): play the deck many times with its cards interacting (the commander included), then
    #     swap the cards that contribute least for approved candidates that do the same jobs, keeping a swap only if
    #     the deck kills faster over the same shuffled games. Same protections as the deck synergy pass.
    sim_info = None
    if plan.get("simulate", True):
        conds = profile.get("conditions") or []
        fin = (profile.get("role") or {}).get("role") == "finisher"
        cmd_m = SIM.sim_model(cmd, conds, True)
        _mc = {}

        def M(c):
            if c["name"] not in _mc:
                _mc[c["name"]] = SIM.sim_model(c, conds)
            return _mc[c["name"]]

        base_seed = zlib.crc32(cmd["name"].encode()) % 100000       # same shuffles every run: reproducible builds
        seeds = list(range(base_seed, base_seed + SIM_GAMES))
        def can_swap(o, c):
            """Would c be allowed in if o left (budget, creature cap, Game Changers...)?"""
            old = why[o["name"]]
            remove(o)
            ok = not blocked(c, creature_cap=cap)
            add(o, *old)
            return ok

        cur = SIM.evaluate([M(c) for c in picked], cmd_m, lands, conds, seeds, fin)
        start = dict(cur)
        sim_swaps = []
        for _ in range(SIM_SWAPS):
            credit = cur["credit"]
            # interaction (removal, protection, wipes, counters) is insurance: its value shows up only when the table
            # acts, so the simulator never cuts it; it tunes the cards that are supposed to DO things
            outs = sorted((c for c in picked if not protected(c) and not (jobs(c) & INSURANCE)),
                          key=lambda c: credit.get(c["name"], 0))[:6]
            ins = sorted((c for c in pool_in if c["name"] not in names and not (face_keys(c) & taken)),
                         key=lambda c: -dvalue(c, False))[:40]
            best = None
            if os.environ.get("SIM_DEBUG"):
                print("SIM outs:", [(o["name"], round(credit.get(o["name"], 0), 2), sorted(jobs(o))) for o in outs])
                print("SIM ins:", [(c["name"], sorted(jobs(c))) for c in ins[:15]])
            for o in outs:
                need = jobs(o)
                for c in [x for x in ins if need <= jobs(x) and can_swap(o, x)][:4]:
                    trial = [M(x) for x in picked if x is not o] + [M(c)]
                    r = SIM.evaluate(trial, cmd_m, lands, conds, seeds, fin)
                    if os.environ.get("SIM_DEBUG"):
                        print(f"   try {o['name']} -> {c['name']}: {r['kill']:.2f} vs {cur['kill']:.2f}")
                    if SIM.score(r) < SIM.score(cur) - SIM_GAIN and (best is None or SIM.score(r) < SIM.score(best[2])):
                        best = (o, c, r)
                if best:
                    break
            if not best:
                break
            o, c, r = best
            old = why[o["name"]]
            remove(o)
            if blocked(c, creature_cap=cap):
                add(o, *old)
                pool_in = [x for x in pool_in if x is not c]
                continue
            add(c, "Simulation", f"simulated games: the deck kills {cur['kill'] - r['kill']:.2f} turns faster on average "
                                 f"with it than with {o['name']}: {E.describe(c)}{needs_text(c)}")
            web.remove(o); web.add(c)
            sim_swaps.append(dict(out=o["name"], into=c["name"], gain=round(cur["kill"] - r["kill"], 2)))
            cur = r
        spread = SIM.kill_spread([M(c) for c in picked], cmd_m, lands, conds, seeds, fin)
        cred = cur["credit"]
        sim_info = dict(games=SIM_GAMES, start_kill=round(start["kill"], 2), kill=round(cur["kill"], 2), spread=spread,
                        fast=round(cur["fast"], 3), swaps=sim_swaps,
                        top=[(n, round(v, 1)) for n, v in sorted(cred.items(), key=lambda kv: -kv[1])[:8]],
                        quiet=[c["name"] for c in picked if cred.get(c["name"], 0) < 0.3 and not is_staple(c)
                               and not (set(E.role_tags(c)) & {"removal", "protection", "counter", "sweeper"})])

    # 9. combo check: swap out a piece of any combo this vibe / Bracket 3 doesn't allow (early two-card wins etc.)
    must_names = {n.lower() for n in plan["must"]}
    if db:
        cut_names, kept = set(), set()
        for _ in range(20):
            found = db.complete([c["name"] for c in picked] + [cmd["name"]])
            bad = [k for k in found if not k.get("templates") and not K.allowed(k, max_tag) and id(k) not in kept
                   and (k.get("relevant", True) or k["tag"] in ("R", "B"))]
            if not bad:
                break
            k = bad[0]
            removable = [c for c in picked if c["name"].lower() in k["_cards"] and c["name"].lower() not in must_names]
            if not removable:
                warnings.append(f"Kept a combo you forced in with Must include: {K.describe(k)} "
                                f"[{K.TAG_NAMES.get(k['tag'])}]. It may push the deck above Bracket 3.")
                kept.add(id(k))
                continue
            victim = min(removable, key=lambda c: c["_s"])
            remove(victim)
            cut_names.add(victim["name"])
            combos_removed.append(dict(combo=K.describe(k), tag=K.TAG_NAMES.get(k["tag"], k["tag"]), cut=victim["name"]))
            have_now = {c["name"].lower() for c in picked} | {cmd_l}
            for c in nonland:
                if c["name"] in names or c["name"] in cut_names or blocked(c, creature_cap=cap):
                    continue
                cl = c["name"].lower()
                makes_bad = any(not K.allowed(x, max_tag) and not x.get("templates")
                                and all(n in have_now or n == cl for n in x["_cards"]) for x in db.involving(cl))
                if not makes_bad:
                    add(c, "Best remaining fit", lambda_reason(c) + f" (replaces {victim['name']}, see combo check)")
                    break

    # 10. speed check: a Bracket 3 deck shouldn't be able to win before turn 6. Cut the cards that make it faster
    #     (power cards or pieces of the fastest combo), lowest-scoring first, and refill with synergy cards.
    for _ in range(12):
        t, how = SP.earliest_win(picked, cmd, db)
        if t >= SP.MIN_WIN_TURN:
            break
        cands = [c for c in picked if c["name"].lower() not in must_names
                 and (is_power(c) or (how.startswith("a combo") and c["name"].lower() in how.lower()))]
        if not cands:
            warnings.append(f"This deck may win around turn {t:g} ({how}) because of cards you asked for.")
            break
        victim = min(cands, key=lambda c: c["_s"])
        remove(victim)
        warnings.append(f"Cut {victim['name']}: with it the deck could win around turn {t:g} ({how}); Bracket 3 aims for turn {SP.MIN_WIN_TURN}+.")
        for c in nonland:
            if c is not victim and not is_power(c) and not blocked(c, creature_cap=cap):
                add(c, "Best remaining fit", lambda_reason(c) + f" (replaces {victim['name']}, see speed check)")
                break
    speed = SP.earliest_win(picked, cmd, db)

    # ---- lands (prices never count against the budget; see lands.py for the house rules)
    for c in pool:
        if "land" in c["tags"]:
            c["_lsyn"], _ = A.synergy(c, profile)
    chosen_lands, land_info = L.choose([c for c in pool if not (face_keys(c) & taken)], ident, lands,
                                       synergy=lambda c: c.get("_lsyn", 0), popularity=CM.popularity,
                                       max_nonbasic=plan["max_nonbasic"])
    nonbasic = [c for c, _ in chosen_lands]
    st["gc"] += sum(c["game_changer"] for c in nonbasic)
    land_cost = sum(price(c) for c in nonbasic)
    colors = [x for x in "WUBRG" if x in ident]
    pc = Counter()
    for c in picked + [cmd, cmd]:                       # the commander's pips count double: you cast it again and again
        for k, v in pips(c["mana_cost"]).items():
            if k in ident:
                pc[k] += v
    basics_n = lands - len(nonbasic)
    basics = Counter()
    if colors:
        total_pips = sum(pc.values()) or len(colors)
        for k in colors:
            basics[BASICS[k]] = max(1, round(basics_n * (pc[k] or 1) / total_pips))
        while sum(basics.values()) > basics_n:
            basics[max(basics, key=basics.get)] -= 1
        while sum(basics.values()) < basics_n:
            basics[max(basics, key=basics.get)] += 1
    else:
        basics["Wastes"] = basics_n

    web_final = CF.DeckWeb(picked + [cmd])
    # ---- report: why each card is in, and the best cards that were edged out per role
    report = []
    for c in sorted(picked, key=lambda c: (c["cmc"], c["name"])):
        step, reason = why[c["name"]]
        lk, lr = web_final.links(c, True)
        report.append(dict(name=c["name"], step=step, why=reason, quality=c["_q"], synergy=round(c["_syn"], 1),
                           links=round(lk, 2), link_why=lr, staple=is_staple(c),
                           roles=sorted(E.assess(c)["roles"]), cmc=c["cmc"], needs=c.get("_needs") or []))
    edged = {}
    for role, cands in role_rank.items():
        alts = []
        in_role = [x for x in cands if x["name"] in names]
        cutoff = min((role_score(x, role) for x in in_role), default=0)
        for c in cands[:40]:
            if c["name"] in names:
                continue
            reason = blocked(c, rules_only=True)
            if not reason:
                gap = cutoff - role_score(c, role)
                reason = (f"just below the cut ({role_score(c, role):.0f} vs {cutoff:.0f}: picks were better at the job or fit "
                          f"{cmd['name'].split(',')[0]} better)") if gap > 0 else \
                         "outscored overall by on-plan cards that also do this job"
            alts.append(dict(name=c["name"], quality=E.role_quality(c, role), synergy=round(c["_syn"], 1),
                             why=E.describe(c), cut=reason))
            if len(alts) >= 3:
                break
        edged[role] = alts

    combo_info = dict(available=db is not None, count=len(db) if db else 0, timestamp=db.timestamp if db else None,
                      max_tag=max_tag, max_tag_name=K.TAG_NAMES.get(max_tag), added=[K.describe(k) for k in combos_added],
                      removed=combos_removed, in_deck=[], near=[])
    if db:
        deck_names = [c["name"] for c in picked + nonbasic] + [cmd["name"]]
        for k in db.complete(deck_names):
            combo_info["in_deck"].append(dict(desc=K.describe(k), tag=k["tag"], tag_name=K.TAG_NAMES.get(k["tag"], k["tag"]),
                                              results=k.get("results"), link=K.link(k), templates=k.get("templates"),
                                              allowed=K.allowed(k, max_tag)))
        if not no_combos:
            addable = {n for n, c in by_name.items() if c["name"] not in names and not blocked(c, rules_only=True)}
            for k, miss in db.near(deck_names, addable, limit=40):
                if K.allowed(k, max_tag) and not k.get("templates") and len(combo_info["near"]) < 5:
                    combo_info["near"].append(dict(desc=K.describe(k), add=by_name[miss]["name"], tag_name=K.TAG_NAMES.get(k["tag"]),
                                                   link=K.link(k)))

    lines = [f"1 {cmd['name']}"] + [f"1 {c['name']}" for c in sorted(picked, key=lambda c: (c["cmc"], c["name"]))] \
        + [f"1 {c['name']}" for c in nonbasic] + [f"{n} {b}" for b, n in basics.items()]
    total = 1 + len(picked) + len(nonbasic) + sum(basics.values())
    gc_cards = ([cmd["name"]] if cmd["game_changer"] else []) + \
        [c["name"] for c in picked + nonbasic if c["game_changer"]]
    problems = V.check(idx, lines, cmd, plan, db, max_tag, off_color_lands)
    for pr in problems:
        warnings.append(f"Deck check: {pr}")
    avg_q = round(sum(c["_q"] for c in picked) / max(1, len(picked)), 1)
    return dict(lines=lines, total=total, spent=st["spent"], gc=len(gc_cards), gc_cards=gc_cards,
                roles={r: tagc[r] for r in ROLE_KEYS}, quotas=quotas, tutors=tagc["tutor"], extra_turns=tagc["extra_turn"],
                creatures=tagc["creature"], creature_cap=cap, members=st["members"], mech_hits=st["mech"], narr_hits=st["narr"],
                onplan=st["onplan"], plan_counts={p["name"]: planc[p["name"]] for p in all_plans}, avg_quality=avg_q,
                nonbasic=len(nonbasic), land_pool=land_info["candidates"], warnings=warnings, land_cost=land_cost,
                land_kinds=land_info["kinds"], tapped_lands=land_info["tapped"],
                land_list=[dict(name=c["name"], kind=k) for c, k in chosen_lands], feel={k: vtc[k] for k in vibe["slots"]},
                wincons=[c["name"] for c in picked if A.is_wincon(c)], deck_swaps=deck_swaps, simulation=sim_info,
                budget=budget, budget_stretch=stretch, stretched=round(max(0.0, st["spent"] - budget), 2),
                reference=dict(ref.summary(), in_deck=sorted(c["name"] for c in picked if c.get("_ref"))),
                deck_links=round(sum(web_final.links(c, True)[0] for c in picked) / max(1, len(picked)), 2),
                unlinked=[c["name"] for c in picked if web_final.links(c, True)[0] == 0 and c["_syn"] < SYN_ON_PLAN],
                strategy_counts={n["id"]: sum(1 for x in picked if ST.meets(x, n)) for n in (profile.get("strategy") or {}).get("needs", [])}, speed=dict(turn=speed[0], how=speed[1]),
                profile=profile, vibe_match=vmatch, natural_vibe=natural, report=report, edged=edged, combos=combo_info,
                validation=problems)


def needs_text(c):
    """'covers 3 needs: counts for it + triggers its abilities + ramp'"""
    n = c.get("_needs") or []
    if len(n) < 2:
        return ""
    words = [A.NEED_NAMES.get(x) or ST.LABELS.get(x) or x.split(":", 1)[-1] for x in n]
    return f" [covers {len(n)} needs: {' + '.join(words)}]"


def lambda_reason(c):
    if c["_syn"] >= SYN_ON_PLAN:
        return f"fits {c['_syn_why']}: {E.describe(c)}{needs_text(c)}"
    if c["_th_why"]:
        return f"{c['_th_why']}: {E.describe(c)}"
    return E.describe(c)


def functional(res, plan):
    """A random deck only counts if it is a real, balanced deck (so 'weird' never means 'broken')."""
    q = plan["vibe"]["quotas"]
    return (res["total"] == 100 and res["roles"]["ramp"] >= min(6, q["ramp"])
            and res["roles"]["draw"] >= min(4, q["draw"]) and res["roles"]["removal"] >= 2)


# ----------------------------------------------------------------------------- plan resolution
def split_names(s):
    return [x.strip() for x in (s or "").split(";") if x.strip()]


def truthy(s):
    return str(s).strip().lower() in ("1", "true", "yes", "y", "on")


def base_plan(a, vibe_label, mech, narr):
    vibe_label = O.OLD_VIBE_NAMES.get(vibe_label, vibe_label)
    if vibe_label not in O.VIBES:
        raise SystemExit(f"Unknown vibe {vibe_label!r}. Options: " + "; ".join(O.VIBE_LABELS))
    vibe = O.VIBES[vibe_label]
    user_avoid, unknown = O.parse_avoid(a.avoid)
    warnings = [f"Avoid: don't recognize {u!r}. Known words: {', '.join(sorted(O.AVOID_WORDS))}" for u in unknown]
    if a.no_extra_turns:
        user_avoid.add("extra turns")
    typed = bool(str(a.budget).strip())
    try:
        budget = float(str(a.budget).replace("$", "").replace(",", "")) if typed else float(vibe.get("budget", DEFAULT_BUDGET))
    except ValueError:
        raise SystemExit(f"Budget must be a number of dollars, got {a.budget!r}")
    if budget > MAX_BUDGET:
        warnings.append(f"Budget capped at ${MAX_BUDGET:.0f} (house rule). Lands never count against it.")
        budget = MAX_BUDGET
    # blank Budget: the vibe may stretch above the default for cards worth it; a typed budget is a hard cap
    stretch = 0.0 if typed else max(0.0, min(float(vibe.get("budget_stretch", 0)), MAX_BUDGET - budget))
    return dict(vibe_label=vibe_label, vibe=vibe, mech=mech, narr=narr, budget=budget, budget_stretch=stretch,
                lands=a.lands or vibe["lands"], max_gc=min(vibe["max_gc"], 3) if a.max_gc is None else a.max_gc,
                must=split_names(a.must), exclude=split_names(a.exclude), avoid=user_avoid,
                reference=split_names(getattr(a, "reference", "")),
                tribe_count=a.tribe_count, max_creatures=a.max_creatures, max_nonbasic=a.max_nonbasic,
                warnings=warnings, tribe="", tribe_mode="off", colors_filter=None)


def theme(label):
    label = O.OLD_MECH_NAMES.get(label, label)
    return None if label in (None, "", O.ANY) else label


def pick_from(rows, a, rng):
    if not rows:
        return None
    return rng.choice(rows[:10]) if a.pick_mode.startswith("Random") else rows[0]


def resolve_normal(a, idx, rng):
    all_types(idx)                      # creature-type list first, so tribe detection sees every type
    vibe_label = O.OLD_VIBE_NAMES.get(a.vibe, a.vibe) or O.DEFAULT_VIBE
    mech, narr = theme(a.mech), theme(a.narr)
    plan = base_plan(a, vibe_label, mech, narr)
    colors = O.parse_colors(a.colors)
    plan["colors_filter"] = colors
    avoid_all = plan["avoid"] | set(plan["vibe"]["avoid"])
    top = 25 if a.suggest_only else 15
    rows, note = [], ""
    if a.commander.strip():
        cmd, fix = CM.lookup(idx, a.commander)
        how = "typed by you (dropdown commander choices ignored)"
        if fix:
            plan["warnings"].append(fix)
            how = "closest match to what you typed"
        if colors is not None and set(cmd["identity"]) != colors:
            plan["warnings"].append("Colors dropdown ignored because you typed a commander.")
            plan["colors_filter"] = None
    else:
        rows, note = CM.candidates(idx, colors=colors, mech=mech, narr=narr, tribe=fix_tribe(idx, a.tribe, plan) if a.tribe.strip() else "",
                                   vibe=plan["vibe"], vibe_label=plan["vibe_label"], avoid=avoid_all, top=top)
        pick = pick_from(rows, a, rng)
        if not pick:
            raise SystemExit("No commander fits those colors/tribe/vibe. Loosen a filter (try colors = Any) and rerun.")
        cmd = idx[pick["name"].lower()]
        how = ("random pick from the top 10 matches" if a.pick_mode.startswith("Random") else "best match")
    if a.tribe.strip():
        plan["tribe"], plan["tribe_mode"] = fix_tribe(idx, a.tribe, plan), "explicit"
    else:
        all_types(idx)
        plan["tribe"] = CM.detect_tribe(cmd)
        plan["tribe_mode"] = "auto" if plan["tribe"] else "off"
    return cmd, plan, how, rows, note


def fix_tribe(idx, typed, plan):
    """'elfs', 'zombie', 'Angel, demon' -> real creature types (closest match), joined with '/'."""
    import names as N
    types = sorted(all_types(idx))
    R = N.Resolver(types)
    out = []
    for part in re.split(r"[/,;&]| and | or ", typed):
        part = part.strip()
        if not part:
            continue
        r = R.resolve(re.sub(r"(?:ies)$", "y", re.sub(r"(?:ves)$", "f", part)).rstrip("s") if len(part) > 3 else part)
        out.append(r["name"])
        if r["name"].lower() != part.lower().rstrip("s"):
            plan["warnings"].append(f"Tribe: you typed {part!r}; using **{r['name']}**.")
    return "/".join(dict.fromkeys(out))


def resolve_random(a, idx, rng):
    """Functional-but-weird: random vibe, colors, mechanical + narrative theme, and a 1-in-10 chance
    of a tribal deck (in which case the tribe is the type(s) the commander supports, linked to the commander).
    The tribal roll and the vibe are rolled ONCE; only colors/themes are re-rolled while hunting for a
    combination that makes a balanced deck, so retries can't bias the odds."""
    all_types(idx)                      # creature-type list first, so tribe detection sees every type
    typed = a.commander.strip()
    user_avoid, _ = O.parse_avoid(a.avoid)
    tribal_roll = True if a.tribe.strip() else rng.random() < 0.10
    for _v in range(8):
        vibe_label = rng.choice(O.VIBE_LABELS)
        for _ in range(40):
            mech, narr = rng.choice(O.MECH_LABELS[1:]), rng.choice(O.NARR_LABELS[1:])
            tribal = tribal_roll
            colors_label = O.random_color_label(rng)
            plan = base_plan(a, vibe_label, mech, narr)
            avoid_all = user_avoid | set(plan["vibe"]["avoid"])
            if typed:
                cmd, fix = CM.lookup(idx, typed)
                rows, note, how = [], "", "typed by you (randomized the rest)"
                if fix:
                    plan["warnings"].append(fix)
                if tribal and not a.tribe.strip() and not CM.detect_tribe(cmd):
                    tribal = False
            else:
                if tribal:
                    narr = plan["narr"] = O.ANY            # the tribe IS the narrative
                rows, note = CM.candidates(idx, colors=O.parse_colors(colors_label), mech=mech, narr=narr,
                                           tribe=a.tribe.strip(), vibe=plan["vibe"], vibe_label=vibe_label,
                                           avoid=avoid_all, top=40, require_type_ref=tribal, forbid_type_ref=not tribal)
                if len(rows) < 3:
                    continue
                cmd = idx[CM.weighted_pick(rng, rows[:20])["name"].lower()]
                how = "randomized" + (" (tribal roll, 1 in 10)" if tribal else "")
                plan["colors_filter"] = O.parse_colors(colors_label)
            if a.tribe.strip():
                plan["tribe"], plan["tribe_mode"] = fix_tribe(idx, a.tribe, plan), "explicit"
            elif tribal:
                plan["tribe"], plan["tribe_mode"] = CM.detect_tribe(cmd), "auto"
            res = build(idx, cmd, plan)
            if functional(res, plan):
                plan["random_colors"] = colors_label
                return cmd, plan, how, rows[:15], note, res
    raise SystemExit("Couldn't find a balanced random combination with those constraints. Rerun (new seed) or loosen them.")


# ----------------------------------------------------------------------------- output
def write_outputs(a, cmd, plan, res, how, rows, note, seed, randomized, better=None, me=None):
    outdir = os.path.dirname(a.out) or "."
    os.makedirs(outdir, exist_ok=True)
    if res:
        open(a.out, "w", encoding="utf-8").write("\n".join(res["lines"]) + "\n")
    open(os.path.join(outdir, "tribe.txt"), "w", encoding="utf-8").write(plan["tribe"])
    open(os.path.join(outdir, "target.txt"), "w", encoding="utf-8").write(str(plan["vibe"]["bracket"]))
    if cmd:
        colors = "".join(x for x in "WUBRG" if x in cmd["identity"]) or "C"
    elif plan.get("colors_filter") is not None:
        colors = "".join(x for x in "WUBRG" if x in plan["colors_filter"]) or "C"
    else:
        colors = "Any"
    info = dict(
        commander=cmd["name"] if cmd else None, commander_how=how, vibe=plan["vibe_label"], vibe_blurb=plan["vibe"]["blurb"],
        bracket=plan["vibe"]["bracket"], colors=colors,
        mechanical_theme=plan["mech"] or O.ANY, narrative_theme=plan["narr"] or O.ANY,
        tribe=plan["tribe"], tribe_mode=plan["tribe_mode"], budget=plan["budget"], seed=seed,
        randomized=randomized, max_gc=plan["max_gc"], avoid=sorted(set(plan["avoid"]) | set(plan["vibe"]["avoid"])),
        options=rows, options_note=note, suggest_only=res is None, warnings=(res or {}).get("warnings", plan["warnings"]),
        better_commanders=better or [], current_fit=me)
    if res:
        info.update({k: res[k] for k in ("total", "spent", "gc_cards", "roles", "quotas", "tutors", "extra_turns", "creatures",
                                         "creature_cap", "members", "mech_hits", "narr_hits", "nonbasic", "feel", "onplan",
                                         "plan_counts", "avg_quality", "profile", "vibe_match", "natural_vibe", "report", "edged",
                                         "land_cost", "land_kinds", "tapped_lands", "land_list", "combos", "validation", "wincons", "speed", "strategy_counts", "deck_swaps", "deck_links", "unlinked", "simulation", "reference", "budget_stretch", "stretched")})
    json.dump(info, open(os.path.join(outdir, "plan.json"), "w"), indent=2)
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commander", default="", help="exact card name; blank = pick one for me")
    ap.add_argument("--vibe", default="", help="deck vibe label (see options.py)")
    ap.add_argument("--colors", default="", help="color label, e.g. 'Simic (UG)'")
    ap.add_argument("--mech", default="", help="mechanical theme label")
    ap.add_argument("--narr", default="", help="narrative theme label")
    ap.add_argument("--tribe", default="", help="creature type to emphasize; blank = tribal only if the commander's text references its own type")
    ap.add_argument("--pick-mode", default=O.PICK_MODES[0], dest="pick_mode")
    ap.add_argument("--randomize", nargs="?", const="true", default="false")
    ap.add_argument("--seed", default="")
    ap.add_argument("--budget", default="", help="USD cap; blank = the vibe's default")
    ap.add_argument("--must", default="", help="cards to force in, separated by ;")
    ap.add_argument("--exclude", default="", help="cards to leave out, separated by ;")
    ap.add_argument("--reference", default="", help="cards real decks run with this commander (your own research), separated by ;")
    ap.add_argument("--avoid", default="", help="e.g. 'wipes, counterspells, tutors, extra turns, stax, infect'")
    ap.add_argument("--suggest-only", action="store_true", dest="suggest_only")
    ap.add_argument("--max-gc", type=int, default=None, dest="max_gc", help="override the Game Changer cap (Bracket 3 = 3)")
    ap.add_argument("--lands", type=int, default=None)
    ap.add_argument("--max-nonbasic", type=int, default=None, dest="max_nonbasic", help="default depends on the number of colors")
    ap.add_argument("--tribe-count", type=int, default=None, dest="tribe_count",
                    help="tribe members to aim for; default 22 if you typed a tribe, 14 if it came from the commander")
    ap.add_argument("--max-creatures", type=int, default=None, dest="max_creatures", help="creature cap; default comes from the commander's plan and theme")
    ap.add_argument("--no-extra-turns", action="store_true", dest="no_extra_turns")
    ap.add_argument("--cards", default="data/cards.json")
    ap.add_argument("--combos", default="data/combos.json", help="Commander Spellbook combos (from fetch_combos.py)")
    ap.add_argument("--out", default="out/deck.txt")
    a = ap.parse_args()

    idx = load_cards(a.cards)
    global COMBO_DB
    COMBO_DB = K.load(a.combos)
    if COMBO_DB is None:
        print("note: no combo data (data/combos.json); building without combo checks", file=sys.stderr)
    seed = a.seed.strip() or str(random.SystemRandom().randrange(1_000_000))
    rng = random.Random(seed)
    randomized = truthy(a.randomize) and not a.suggest_only

    if a.suggest_only:
        a.commander = ""
        cmd, plan, how, rows, note = resolve_normal(a, idx, rng)
        write_outputs(a, None, plan, None, "", rows, note, seed, False)
        print(f"suggestions: {len(rows)} commanders ({note})")
        for r in rows:
            print(f"  {r['name']} [{r['identity']}] score={r['score']} {'; '.join(r['reasons'])}")
        return
    if randomized:
        cmd, plan, how, rows, note, res = resolve_random(a, idx, rng)
    else:
        cmd, plan, how, rows, note = resolve_normal(a, idx, rng)
        res = build(idx, cmd, plan)
    plan["warnings"] = res["warnings"]
    me, better = CM.better_fits(idx, cmd, plan)
    info = write_outputs(a, cmd, plan, res, how, rows, note, seed, randomized, better, me)

    prof = res["profile"]
    print(f"vibe: {plan['vibe_label']} (Bracket {info['bracket']}) | colors: {info['colors']} | "
          f"mech: {info['mechanical_theme']} | narrative: {info['narrative_theme']}", file=sys.stderr)
    print(f"commander: {cmd['name']} ({how}) | tribe: {plan['tribe'] or 'none'} [{plan['tribe_mode']}] | seed: {seed}", file=sys.stderr)
    print(f"plans: " + "; ".join(f"{p['name']} {p['weight']}" for p in prof["plans"]) +
          f" | vibe match: {res['vibe_match']} (natural: {res['natural_vibe']})", file=sys.stderr)
    print(f"roles: {res['roles']} | on-plan cards: {res['onplan']} {res['plan_counts']} | avg quality: {res['avg_quality']} | "
          f"creatures: {res['creatures']}/{res['creature_cap']} | tutors: {res['tutors']} | land pool: {res['land_pool']} "
          f"({res['nonbasic']} nonbasic)", file=sys.stderr)
    for w in res["warnings"]:
        print(f"WARNING: {w}", file=sys.stderr)
    print(f"commander={cmd['name']} tribe={plan['tribe'] or '-'} cards={res['total']} "
          f"est_cost=${res['spent']:.0f} game_changers={res['gc']}")


if __name__ == "__main__":
    main()
