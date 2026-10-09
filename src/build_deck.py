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
import sys
from collections import Counter

import analyze as A
import combos as K
import commanders as CM
import evaluate as E
import lands as L
import options as O
import validate as V
from common import load_cards, pips

BASICS = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
BASIC_TYPES = (("Plains", "W"), ("Island", "U"), ("Swamp", "B"), ("Mountain", "R"), ("Forest", "G"))
ROLE_KEYS = ("ramp", "draw", "removal", "counter", "sweeper", "recursion", "protection")
ROLE_NAMES = dict(ramp="Ramp", draw="Card draw", removal="Removal", counter="Counterspells", sweeper="Board wipes",
                  recursion="Recursion", protection="Commander protection")

# weights for the overall card score
W_SYN, W_Q, W_POP = 1.5, 0.55, 0.35
SYN_ON_PLAN = 8          # synergy at/above this = "on plan" for counting
MAX_BUDGET = 500.0       # house rule: nonland cards only; lands are never counted
COMBO_DB = None          # set by main() from data/combos.json (Commander Spellbook); None = no combo checks


# ----------------------------------------------------------------------------- small helpers
def price(c):
    try:
        return float(c["usd"]) if c.get("usd") else 0.5
    except ValueError:
        return 0.5


def is_member(c, tribe):
    """One definition of 'counts as the tribe', used for scoring AND counting (Changelings count)."""
    if not tribe:
        return False
    t, x = c["type_line"].lower(), (c.get("text") or "").lower()
    return "creature" in t and (tribe.lower() in t or "changeling" in x)


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
    if k not in _ALL_TYPES:
        _ALL_TYPES[k] = frozenset(A.creature_types(CM.unique(idx)))
    return _ALL_TYPES[k]


# ----------------------------------------------------------------------------- scoring
def theme_bonus(c, ctx):
    s, why = 0.0, ""
    if ctx["tribe"]:
        if is_member(c, ctx["tribe"]):
            s += 45; why = f"{ctx['tribe']} (your tribe)"
        elif ctx["tribe"].lower() in (c.get("text") or "").lower():
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
    a = E.assess(c)
    th, th_why = theme_bonus(c, ctx)
    pop = CM.popularity(c) * ctx["vibe"]["pop"]
    s = W_SYN * syn + W_Q * a["q"] + W_POP * pop + th
    if syn <= 0 and th <= 0 and not a["roles"]:
        s -= 10                                     # generic filler: only if nothing better exists
    if c["cmc"] >= 7 and syn < SYN_ON_PLAN:
        s -= 8
    c["_syn"], c["_syn_why"], c["_q"], c["_th"], c["_th_why"], c["_pop"], c["_s"] = syn, syn_why, a["q"], th, th_why, pop, s


def role_score(c, role):
    """Compare cards for a role: how good they are AT the role first, then how well they fit this deck."""
    # quality decides; synergy and themes break ties between similar cards (capped so a weak on-plan card
    # can't beat a much better one at the actual job)
    return E.role_quality(c, role) + 0.4 * min(c["_syn"], 40) + 0.4 * c["_pop"] + 0.2 * min(c["_th"], 30)


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
    typal = [t for t in profile["typal"] if not tribe or t.lower() != tribe.lower()]
    fit, natural, _raw = A.vibe_fit(cmd, O.VIBES)
    vmatch = A.fit_label(fit[plan["vibe_label"]], natural == plan["vibe_label"])
    ctx = dict(tribe=tribe, vibe=vibe, vibe_label=plan["vibe_label"], mech=mech, narr=narr, profile=profile, typal=typal)
    excluded = {n.lower() for n in plan["exclude"]}
    warnings = list(plan.get("warnings", []))

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
    st = dict(spent=0.0, gc=1 if cmd["game_changer"] else 0, mech=0, narr=0, members=0, onplan=0)
    tagc, vtc, planc = Counter(), Counter(), Counter()
    per_card_cap = max(5.0, budget * 0.10)
    tag_caps = {"extra_turn": vibe["max_extra_turns"], "tutor": vibe["max_tutors"]}
    if cmd["game_changer"] and st["gc"] > max_gc:
        warnings.append(f"{cmd['name']} is itself a Game Changer, so this deck is at least Bracket 3.")
    top_plans = [p["name"] for p in profile["plans"]]
    all_plans = profile["plans"] + profile.get("interactions", [])

    def add(c, step, reason):
        picked.append(c)
        names.add(c["name"])
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

    def blocked(c, creature_cap=None, force=False, rules_only=False):
        """Why a card can't go in right now ('' = it can). rules_only: ignore 'deck full' (for the close-calls report)."""
        if c["name"] in names:
            return "already in"
        if not rules_only:
            if len(picked) >= n_nonland:
                return "deck full"
            if creature_cap is not None and "creature" in c["tags"] and tagc["creature"] >= creature_cap:
                return "creature count reached"
        if force:
            return ""
        if price(c) > per_card_cap:
            return f"too pricey (${price(c):.0f})"
        if st["spent"] + price(c) > budget:
            return "over budget"
        if c["game_changer"] and st["gc"] >= max_gc:
            return "Game Changer limit (Bracket 3)"
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
            if have() >= target or len(picked) >= n_nonland:
                return
            if pred(c):
                try_add(c, step, reason(c), creature_cap=creature_cap)

    def short(c):
        return c["_syn_why"] or c["_th_why"] or "solid card"

    # 1. cards you demanded
    for n in plan["must"]:
        c = idx.get(n.lower())
        if c and set(c["identity"]) <= ident and "land" not in c["tags"]:
            if "_s" not in c:
                prepare(c, ctx)
            try_add(c, "You asked for it", "must-include", force=True)
        else:
            warnings.append(f"Must-include card unavailable, off-color, or a land: {n}")

    # 2. Game Changers: the ones that help THIS deck most, up to the vibe's target
    def gc_key(x):
        roles = E.assess(x)["roles"]
        value = 12 if set(roles) & {"draw", "removal", "counter", "protection", "sweeper"} else 0   # card advantage / interaction first
        return -(x["_s"] + 0.5 * x["_q"] + value + 0.5 * min(x["_syn"], 40))
    gcs = sorted((x for x in nonland if x["game_changer"] and gc_ok(x)), key=gc_key)
    for c in gcs:
        if st["gc"] >= gc_target:
            break
        try_add(c, "Game Changer", f"{E.describe(c)}" + (f"; fits {c['_syn_why']}" if c["_syn_why"] else ""))

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
    role_rank = {}
    for role, need in quotas.items():
        if need <= 0:
            continue
        cands = sorted((c for c in nonland if role in E.role_tags(c)), key=lambda c: -role_score(c, role))
        role_rank[role] = cands
        fill(cands, lambda c: True, lambda r=role: tagc[r], need, ROLE_NAMES[role],
             lambda c, r=role: E.describe(c) + (f"; fits {c['_syn_why']}" if c["_syn"] >= SYN_ON_PLAN else ""))

    # 4. the commander's game plan: the core of the deck, split across its main plans by weight
    if profile["plans"]:
        tw = sum(p["weight"] for p in profile["plans"][:3])
        total = 26 if not (mech or narr or tribe) else 22
        for p in profile["plans"][:3]:
            target = max(4, round(total * p["weight"] / tw))
            have = lambda name=p["name"]: planc[name]
            fill(nonland, lambda c, pl=p: A.plan_hits(c, pl) and c["_syn"] >= SYN_ON_PLAN,
                 have, target, "Commander plan", lambda c, name=p["name"]: f"{name}: {E.describe(c)}")
        if typal:
            fill(nonland, lambda c: c["_syn_why"].endswith("(named by your commander)"),
                 lambda: sum(1 for x in picked if x["_syn_why"].endswith("(named by your commander)")), 8,
                 "Commander plan", lambda c: c["_syn_why"])
    else:
        warnings.append("This commander has no specific engine in its text, so the deck follows your themes and the vibe.")

    # 4-. ability support: every important ability of the commander gets cards that enable / fuel / pay it off
    for p in profile.get("interactions", []):
        if p["weight"] < 1.5:
            continue
        target = 6 if p["kind"] in ("enabler", "fuel") else 4
        fill(nonland, lambda c, pl=p: A.interaction_hits(c, pl) > 0 and c["_q"] >= 35,
             lambda pl=p: planc[pl["name"]], target, "Ability support",
             lambda c, pl=p: f"{pl['name']} ({pl['why'][0][:60]}): {E.describe(c)}")

    # 4a. combos (Commander Spellbook): only ones this vibe allows, that run through the commander or its plan
    db = COMBO_DB
    no_combos = "combos" in avoid
    max_tag = "E" if no_combos else vibe["combo_max"]          # never above S: Ruthless (R) is Bracket 4
    combos_added, combos_removed = [], []
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
            for x in new:
                add(x, "Combo", f"{K.describe(k)} [{K.TAG_NAMES.get(k['tag'], k['tag'])}]")
            combos_added.append(k)

    # 4b. enough creatures for the plan (a voltron or spells deck still needs blockers and bodies)
    floor = max(8, min(cap - 4, round(0.75 * (profile["creatures"] or 26)))) if not plan["max_creatures"] else 0
    if any(p in top_plans[:1] for p in ("Spellslinger (instants & sorceries)",)):
        floor = min(floor, 10)
    if any(p in top_plans[:1] for p in ("Voltron / equipment & auras", "Superfriends (planeswalkers)", "Enchantments", "Artifacts")):
        floor = min(floor, 12)
    fill(nonland, lambda c: "creature" in c["tags"], lambda: tagc["creature"], floor, "Creature base",
         lambda c: lambda_reason(c))

    # 5. the vibe's signature slots (picked by score, so on-plan versions win)
    for slot, need in vibe["slots"].items():
        fill(nonland, lambda c, s=slot: s in O.vibe_tags(c), lambda s=slot: vtc[s], need,
             "Vibe", lambda c, s=slot: f"{s} ({plan['vibe_label'].split(':')[0]}): {E.describe(c)}")

    # 6. your themes (mechanical first, narrative is a softer nudge)
    heavy = bool(tribe)
    if mech:
        fill(nonland, lambda c: O.mech_score(c, mech) > 0, lambda: st["mech"], 14 if heavy else 18,
             "Your theme", lambda c: f"{mech}: {E.describe(c)}")
    if narr:
        fill(nonland, lambda c: O.narr_score(c, narr) >= 2, lambda: st["narr"], 7 if (heavy or mech) else 10,
             "Your theme", lambda c: f"{narr}: {E.describe(c)}")

    # 7. tribe members (only when a tribe is in play)
    if tribe:
        tcount = plan["tribe_count"] or (22 if plan["tribe_mode"] == "explicit" else 14)
        fill(nonland, lambda c: is_member(c, tribe), lambda: st["members"], tcount,
             "Tribe", lambda c: f"{tribe}: {E.describe(c)}")

    # 8. fill the rest by overall score, without letting any one theme take over
    def over_cap(c):
        if mech and st["mech"] >= 30 and O.mech_score(c, mech) > 0 and c["_syn"] < SYN_ON_PLAN:
            return True
        if narr and st["narr"] >= 16 and O.narr_score(c, narr) >= 2 and c["_syn"] < SYN_ON_PLAN:
            return True
        vt = O.vibe_tags(c)
        return any(slot in vt and vtc[slot] >= need * 2 for slot, need in vibe["slots"].items())

    for c in nonland:
        if len(picked) >= n_nonland:
            break
        if not over_cap(c):
            try_add(c, "Best remaining fit", lambda_reason(c), creature_cap=cap)
    for c in nonland:
        if len(picked) >= n_nonland:
            break
        try_add(c, "Best remaining fit", lambda_reason(c))

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

    # ---- lands (prices never count against the budget; see lands.py for the house rules)
    for c in pool:
        if "land" in c["tags"]:
            c["_lsyn"], _ = A.synergy(c, profile)
    chosen_lands, land_info = L.choose(pool, ident, lands, synergy=lambda c: c.get("_lsyn", 0),
                                       popularity=CM.popularity, max_nonbasic=plan["max_nonbasic"])
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

    # ---- report: why each card is in, and the best cards that were edged out per role
    report = []
    for c in sorted(picked, key=lambda c: (c["cmc"], c["name"])):
        step, reason = why[c["name"]]
        report.append(dict(name=c["name"], step=step, why=reason, quality=c["_q"], synergy=round(c["_syn"], 1),
                           roles=sorted(E.assess(c)["roles"]), cmc=c["cmc"]))
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
                profile=profile, vibe_match=vmatch, natural_vibe=natural, report=report, edged=edged, combos=combo_info,
                validation=problems)


def lambda_reason(c):
    if c["_syn"] >= SYN_ON_PLAN:
        return f"fits {c['_syn_why']}: {E.describe(c)}"
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
    try:
        budget = float(a.budget) if str(a.budget).strip() else float(vibe["budget"])
    except ValueError:
        raise SystemExit(f"Budget must be a number of dollars, got {a.budget!r}")
    if budget > MAX_BUDGET:
        warnings.append(f"Budget capped at ${MAX_BUDGET:.0f} (house rule). Lands never count against it.")
        budget = MAX_BUDGET
    return dict(vibe_label=vibe_label, vibe=vibe, mech=mech, narr=narr, budget=budget,
                lands=a.lands or vibe["lands"], max_gc=min(vibe["max_gc"], 3) if a.max_gc is None else a.max_gc,
                must=split_names(a.must), exclude=split_names(a.exclude), avoid=user_avoid,
                tribe_count=a.tribe_count, max_creatures=a.max_creatures, max_nonbasic=a.max_nonbasic,
                warnings=warnings, tribe="", tribe_mode="off", colors_filter=None)


def theme(label):
    return None if label in (None, "", O.ANY) else label


def pick_from(rows, a, rng):
    if not rows:
        return None
    return rng.choice(rows[:10]) if a.pick_mode.startswith("Random") else rows[0]


def resolve_normal(a, idx, rng):
    vibe_label = O.OLD_VIBE_NAMES.get(a.vibe, a.vibe) or O.DEFAULT_VIBE
    mech, narr = theme(a.mech), theme(a.narr)
    plan = base_plan(a, vibe_label, mech, narr)
    colors = O.parse_colors(a.colors)
    plan["colors_filter"] = colors
    avoid_all = plan["avoid"] | set(plan["vibe"]["avoid"])
    top = 25 if a.suggest_only else 15
    rows, note = [], ""
    if a.commander.strip():
        cmd, how = CM.lookup(idx, a.commander), "typed by you (dropdown commander choices ignored)"
        if colors is not None and set(cmd["identity"]) != colors:
            plan["warnings"].append("Colors dropdown ignored because you typed a commander.")
            plan["colors_filter"] = None
    else:
        rows, note = CM.candidates(idx, colors=colors, mech=mech, narr=narr, tribe=a.tribe.strip(),
                                   vibe=plan["vibe"], vibe_label=plan["vibe_label"], avoid=avoid_all, top=top)
        pick = pick_from(rows, a, rng)
        if not pick:
            raise SystemExit("No commander fits those colors/tribe/vibe. Loosen a filter (try colors = Any) and rerun.")
        cmd = idx[pick["name"].lower()]
        how = ("random pick from the top 10 matches" if a.pick_mode.startswith("Random") else "best match")
    if a.tribe.strip():
        plan["tribe"], plan["tribe_mode"] = a.tribe.strip(), "explicit"
    else:
        plan["tribe"] = CM.detect_tribe(cmd)
        plan["tribe_mode"] = "auto" if plan["tribe"] else "off"
    return cmd, plan, how, rows, note


def resolve_random(a, idx, rng):
    """Functional-but-weird: random vibe, colors, mechanical + narrative theme, and a 1-in-10 chance
    of a tribal deck (in which case the tribe is the commander's own type, linked to the commander).
    The tribal roll and the vibe are rolled ONCE; only colors/themes are re-rolled while hunting for a
    combination that makes a balanced deck, so retries can't bias the odds."""
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
                cmd, rows, note, how = CM.lookup(idx, typed), [], "", "typed by you (randomized the rest)"
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
                plan["tribe"], plan["tribe_mode"] = a.tribe.strip(), "explicit"
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
                                         "land_cost", "land_kinds", "tapped_lands", "land_list", "combos", "validation")})
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
