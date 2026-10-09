"""Commander deck builder (v3). No AI calls, $0 to run.

Flow:  vibe + colors + themes  ->  commander (typed, auto-picked, or randomized)  ->  100-card list.
Hard rules (legality, color identity, singleton, budget, Game Changer cap, tutor/extra-turn caps,
the Avoid list) are enforced in code. Judgment is approximated with keyword/theme matching plus
EDHREC popularity. Treat the result as a strong draft, not a finished deck.

Typed Commander always wins: if you type one, the commander/colors dropdowns are ignored (the vibe and
theme dropdowns still shape the 99 cards)."""
import argparse
import json
import math
import os
import random
import re
import sys
from collections import Counter

import commanders as CM
import options as O
from common import load_cards, pips

BASICS = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
VOCAB = ["mill", "graveyard", "+1/+1 counter", "token", "sacrifice", "flying", "untap", "treasure",
         "artifact", "enchantment", "exile", "draw", "whenever", "attacks", "enters"]
BASIC_TYPES = (("Plains", "W"), ("Island", "U"), ("Swamp", "B"), ("Mountain", "R"), ("Forest", "G"))
ROLE_KEYS = ("ramp", "draw", "removal", "counter", "sweeper", "recursion")


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


def gc_ok(c):
    """Game Changers worth a slot: card advantage, interaction, or permanent-based ramp
    (not e.g. Crop Rotation, which only fetches lands)."""
    tags = set(c["tags"])
    if tags & {"draw", "counter", "removal"}:
        return True
    t = c["type_line"]
    return "ramp" in tags and "Instant" not in t and "Sorcery" not in t


def score(c, ctx):
    s = 0.0
    x = (c.get("text") or "").lower()
    if ctx["tribe"]:
        if is_member(c, ctx["tribe"]):
            s += 100
        elif ctx["tribe"].lower() in x:
            s += 40
    s += 15 * sum(1 for k in ctx["cmd_kw"] if k in x)
    s += CM.popularity(c) * ctx["vibe"]["pop"]
    if c["cmc"] >= 7:
        s -= 10
    if ctx["mech"]:
        s += 10 * min(O.mech_score(c, ctx["mech"]), 3)
    if ctx["narr"]:
        s += 4 * min(O.narr_score(c, ctx["narr"]), 5)
    vt = O.vibe_tags(c)
    for slot in ctx["vibe"]["slots"]:
        if slot in vt:
            s += 6
    return s


# ----------------------------------------------------------------------------- the deck builder
def build(idx, cmd, plan):
    vibe, tribe = plan["vibe"], plan["tribe"]
    mech = None if plan["mech"] in (None, "", O.ANY) else plan["mech"]
    narr = None if plan["narr"] in (None, "", O.ANY) else plan["narr"]
    ident = set(cmd["identity"])
    avoid = set(plan["avoid"]) | set(vibe["avoid"])
    budget, lands, max_gc = plan["budget"], plan["lands"], plan["max_gc"]
    gc_target = min(vibe["gc_target"], max_gc)
    cmd_text = (cmd.get("text") or "").lower()
    ctx = dict(tribe=tribe, cmd_kw=[k for k in VOCAB if k in cmd_text], vibe=vibe, mech=mech, narr=narr)
    excluded = {n.lower() for n in plan["exclude"]}
    warnings = list(plan.get("warnings", []))

    pool = [c for c in CM.unique(idx)
            if set(c["identity"]) <= ident and c["name"] != cmd["name"]
            and c["name"].lower() not in excluded
            and "mass_land_denial" not in c["tags"]
            and not O.avoided(c, avoid)]
    nonland = [c for c in pool if "land" not in c["tags"]]
    for c in nonland:
        c["_s"] = score(c, ctx)
    nonland.sort(key=lambda c: (-c["_s"], c["name"]))

    n_nonland = 99 - lands
    cap = plan["max_creatures"] or (O.TRIBAL_MAX_CREATURES if tribe else O.MECH_MAX_CREATURES.get(mech, O.DEFAULT_MAX_CREATURES))
    picked, names = [], set()
    st = dict(spent=0.0, gc=1 if cmd["game_changer"] else 0, mech=0, narr=0, members=0)
    tagc, vtc = Counter(), Counter()
    per_card_cap = max(5.0, budget * 0.10)
    tag_caps = {"extra_turn": vibe["max_extra_turns"], "tutor": vibe["max_tutors"]}
    if cmd["game_changer"] and st["gc"] > max_gc:
        warnings.append(f"{cmd['name']} is itself a Game Changer, so this deck is at least Bracket 3.")

    def add(c):
        picked.append(c)
        names.add(c["name"])
        st["spent"] += price(c)
        st["gc"] += c["game_changer"]
        tagc.update(c["tags"])
        vtc.update(O.vibe_tags(c))
        st["mech"] += bool(mech and O.mech_score(c, mech) > 0)
        st["narr"] += bool(narr and O.narr_score(c, narr) >= 2)
        st["members"] += is_member(c, tribe)

    def try_add(c, force=False, creature_cap=None):
        if c["name"] in names or len(picked) >= n_nonland:
            return False
        if creature_cap is not None and "creature" in c["tags"] and tagc["creature"] >= creature_cap:
            return False
        if not force:
            if price(c) > per_card_cap or st["spent"] + price(c) > budget:
                return False
            if c["game_changer"] and st["gc"] >= max_gc:
                return False
            if any(t in c["tags"] and tagc[t] >= cap for t, cap in tag_caps.items()):
                return False
        add(c)
        return True

    def fill(pred, have, target, creature_cap=cap):
        for c in nonland:
            if have() >= target:
                return
            if pred(c):
                try_add(c, creature_cap=creature_cap)

    # 1. cards you demanded
    for n in plan["must"]:
        c = idx.get(n.lower())
        if c and set(c["identity"]) <= ident and "land" not in c["tags"]:
            try_add(c, force=True)
        else:
            warnings.append(f"Must-include card unavailable, off-color, or a land: {n}")

    # 2. Game Changers: most useful first, up to the vibe's target
    for c in sorted((x for x in nonland if x["game_changer"] and gc_ok(x)), key=lambda x: -x["_s"]):
        if st["gc"] >= gc_target:
            break
        try_add(c)

    # 3. role quotas BEFORE anything else so ramp/draw/removal always get slots
    quotas = dict(vibe["quotas"])
    if "U" not in ident or "counterspells" in avoid:
        quotas["removal"] += quotas["counter"] // 2
        quotas["counter"] = 0
    if "wipes" in avoid:
        quotas["sweeper"] = 0
    for role, need in quotas.items():
        fill(lambda c, r=role: r in c["tags"], lambda r=role: tagc[r], need)

    # 4. the vibe's signature slots (group hug, chaos, stax, fast mana...)
    for slot, need in vibe["slots"].items():
        fill(lambda c, s=slot: s in O.vibe_tags(c), lambda s=slot: vtc[s], need)

    # 5. themes (mechanical first, narrative is a softer nudge)
    heavy = bool(tribe)
    if mech:
        fill(lambda c: O.mech_score(c, mech) > 0, lambda: st["mech"], 16 if heavy else 22)
    if narr:
        fill(lambda c: O.narr_score(c, narr) >= 2, lambda: st["narr"], 7 if (heavy or mech) else 10)

    # 6. tribe members (only when a tribe is in play)
    if tribe:
        fill(lambda c: is_member(c, tribe), lambda: st["members"], plan["tribe_count"])

    # 7. fill the rest by score, without letting creatures take every remaining slot
    def over_cap(c):
        """Themes shape the deck but must not take it over."""
        if mech and st["mech"] >= 34 and O.mech_score(c, mech) > 0:
            return True
        if narr and st["narr"] >= 16 and O.narr_score(c, narr) >= 2:
            return True
        vt = O.vibe_tags(c)
        return any(slot in vt and vtc[slot] >= need * 2 for slot, need in vibe["slots"].items())

    for c in nonland:
        if len(picked) >= n_nonland:
            break
        if not over_cap(c):
            try_add(c, creature_cap=cap)
    for c in nonland:
        if len(picked) >= n_nonland:
            break
        try_add(c)

    # ---- lands
    land_pool = [c for c in pool if "land" in c["tags"] and "Basic" not in c["type_line"]
                 and "creature" not in c["tags"]
                 and len(land_makes(c, ident)) >= (2 if len(ident) > 1 else 1)]
    land_pool.sort(key=lambda c: (c.get("rank") or 10 ** 9, c["name"]))
    nonbasic = []
    for c in land_pool:
        if len(nonbasic) >= min(plan["max_nonbasic"], lands - 10):
            break
        if c["game_changer"] and st["gc"] >= max_gc:
            continue
        if price(c) <= max(5.0, budget * 0.03):
            nonbasic.append(c)
            st["gc"] += c["game_changer"]
            st["spent"] += price(c)
    colors = [x for x in "WUBRG" if x in ident]
    pc = Counter()
    for c in picked + [cmd]:
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

    lines = [f"1 {cmd['name']}"] + [f"1 {c['name']}" for c in sorted(picked, key=lambda c: (c["cmc"], c["name"]))] \
        + [f"1 {c['name']}" for c in nonbasic] + [f"{n} {b}" for b, n in basics.items()]
    total = 1 + len(picked) + len(nonbasic) + sum(basics.values())
    gc_cards = ([cmd["name"]] if cmd["game_changer"] else []) + \
        [c["name"] for c in picked + nonbasic if c["game_changer"]]
    if total != 100:
        warnings.append(f"Deck has {total} cards, not 100 (card pool too small for these filters).")
    return dict(lines=lines, total=total, spent=st["spent"], gc=len(gc_cards), gc_cards=gc_cards,
                roles={r: tagc[r] for r in ROLE_KEYS}, tutors=tagc["tutor"], extra_turns=tagc["extra_turn"],
                creatures=tagc["creature"], creature_cap=cap, members=st["members"], mech_hits=st["mech"], narr_hits=st["narr"],
                nonbasic=len(nonbasic), land_pool=len(land_pool), warnings=warnings,
                feel={k: vtc[k] for k in vibe["slots"]})


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
    return dict(vibe_label=vibe_label, vibe=vibe, mech=mech, narr=narr, budget=budget,
                lands=a.lands or vibe["lands"], max_gc=vibe["max_gc"] if a.max_gc is None else a.max_gc,
                must=split_names(a.must), exclude=split_names(a.exclude), avoid=user_avoid,
                tribe_count=a.tribe_count, max_creatures=a.max_creatures, max_nonbasic=a.max_nonbasic,
                warnings=warnings, tribe="", tribe_mode="off")


def theme(label):
    return None if label in (None, "", O.ANY) else label


def pick_from(rows, a, rng):
    if not rows:
        return None
    return rng.choice(rows[:10]) if a.pick_mode.startswith("Random") else rows[0]


def resolve_normal(a, idx, rng):
    vibe_label = a.vibe or O.DEFAULT_VIBE
    mech, narr = theme(a.mech), theme(a.narr)
    plan = base_plan(a, vibe_label, mech, narr)
    colors = O.parse_colors(a.colors)
    avoid_all = plan["avoid"] | set(plan["vibe"]["avoid"])
    top = 25 if a.suggest_only else 15
    rows, note = CM.candidates(idx, colors=colors, mech=mech, narr=narr, tribe=a.tribe.strip(),
                               vibe=plan["vibe"], avoid=avoid_all, top=top)
    if a.commander.strip():
        cmd, how = CM.lookup(idx, a.commander), "typed by you (dropdown commander choices ignored)"
        if colors is not None and set(cmd["identity"]) != colors:
            plan["warnings"].append("Colors dropdown ignored because you typed a commander.")
        rows, note = [], ""
    else:
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
                                           tribe=a.tribe.strip(), vibe=plan["vibe"], avoid=avoid_all, top=40,
                                           require_type_ref=tribal, forbid_type_ref=not tribal)
                if len(rows) < 3:
                    continue
                cmd = idx[CM.weighted_pick(rng, rows[:20])["name"].lower()]
                how = "randomized" + (" (tribal roll, 1 in 10)" if tribal else "")
            if a.tribe.strip():
                plan["tribe"], plan["tribe_mode"] = a.tribe.strip(), "explicit"
            elif tribal:
                plan["tribe"], plan["tribe_mode"] = CM.detect_tribe(cmd), "auto"
            res = build(idx, cmd, plan)
            if functional(res, plan):
                plan["random_colors"] = colors_label
                return cmd, plan, how, rows[:15], note
    raise SystemExit("Couldn't find a balanced random combination with those constraints. Rerun (new seed) or loosen them.")


# ----------------------------------------------------------------------------- output
def write_outputs(a, cmd, plan, res, how, rows, note, seed, randomized):
    outdir = os.path.dirname(a.out) or "."
    os.makedirs(outdir, exist_ok=True)
    if res:
        open(a.out, "w", encoding="utf-8").write("\n".join(res["lines"]) + "\n")
    open(os.path.join(outdir, "tribe.txt"), "w", encoding="utf-8").write(plan["tribe"])
    open(os.path.join(outdir, "target.txt"), "w", encoding="utf-8").write(str(plan["vibe"]["bracket"]))
    info = dict(
        commander=cmd["name"], commander_how=how, vibe=plan["vibe_label"], vibe_blurb=plan["vibe"]["blurb"],
        bracket=plan["vibe"]["bracket"], colors="".join(x for x in "WUBRG" if x in cmd["identity"]) or "C",
        mechanical_theme=plan["mech"] or O.ANY, narrative_theme=plan["narr"] or O.ANY,
        tribe=plan["tribe"], tribe_mode=plan["tribe_mode"], budget=plan["budget"], seed=seed,
        randomized=randomized, max_gc=plan["max_gc"], avoid=sorted(set(plan["avoid"]) | set(plan["vibe"]["avoid"])),
        options=rows, options_note=note, suggest_only=res is None, warnings=(res or {}).get("warnings", plan["warnings"]))
    if res:
        info.update({k: res[k] for k in ("total", "spent", "gc_cards", "roles", "tutors", "extra_turns", "creatures",
                                         "members", "mech_hits", "narr_hits", "nonbasic", "feel")})
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
    ap.add_argument("--max-gc", type=int, default=None, dest="max_gc", help="override the vibe's Game Changer cap")
    ap.add_argument("--lands", type=int, default=None)
    ap.add_argument("--max-nonbasic", type=int, default=14, dest="max_nonbasic")
    ap.add_argument("--tribe-count", type=int, default=24, dest="tribe_count")
    ap.add_argument("--max-creatures", type=int, default=None, dest="max_creatures", help="creature cap; default depends on the theme")
    ap.add_argument("--no-extra-turns", action="store_true", dest="no_extra_turns")
    ap.add_argument("--cards", default="data/cards.json")
    ap.add_argument("--out", default="out/deck.txt")
    a = ap.parse_args()

    idx = load_cards(a.cards)
    seed = a.seed.strip() or str(random.SystemRandom().randrange(1_000_000))
    rng = random.Random(seed)
    randomized = truthy(a.randomize) and not a.suggest_only

    cmd, plan, how, rows, note = (resolve_random if randomized else resolve_normal)(a, idx, rng)
    if a.suggest_only:
        write_outputs(a, cmd, plan, None, how, rows, note, seed, False)
        print(f"suggestions: {len(rows)} commanders ({note})")
        for r in rows:
            print(f"  {r['name']} [{r['identity']}] score={r['score']} {'; '.join(r['reasons'])}")
        return
    res = build(idx, cmd, plan)
    plan["warnings"] = res["warnings"]
    info = write_outputs(a, cmd, plan, res, how, rows, note, seed, randomized)

    print(f"vibe: {plan['vibe_label']} -> Bracket {info['bracket']} | colors: {info['colors']} | "
          f"mech: {info['mechanical_theme']} | narrative: {info['narrative_theme']}", file=sys.stderr)
    print(f"commander: {cmd['name']} ({how}) | tribe: {plan['tribe'] or 'none'} [{plan['tribe_mode']}] | seed: {seed}",
          file=sys.stderr)
    print(f"roles: {res['roles']} | tutors: {res['tutors']} | extra turns: {res['extra_turns']} | "
          f"creatures: {res['creatures']} | theme hits: mech {res['mech_hits']}, narr {res['narr_hits']} | "
          f"land pool: {res['land_pool']} ({res['nonbasic']} nonbasic chosen)", file=sys.stderr)
    for w in res["warnings"]:
        print(f"WARNING: {w}", file=sys.stderr)
    print(f"commander={cmd['name']} tribe={plan['tribe'] or '-'} cards={res['total']} "
          f"est_cost=${res['spent']:.0f} game_changers={res['gc']}")


if __name__ == "__main__":
    main()
