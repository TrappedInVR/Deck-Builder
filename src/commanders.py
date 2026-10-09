"""Commander selection: find legendary creatures that fit the chosen vibe, colors, themes and tribe.

Ranking uses the commander analysis (analyze.py):
  * vibe personality: mean vibes get commanders that are mean by nature, social vibes get generous ones...
  * plan alignment: a commander whose own engine IS your mechanical theme beats one that merely mentions it
  * plan clarity: commanders with a clear game plan beat vanilla ones
  * theme text matches, tribe, and (a little) EDHREC popularity"""
import difflib
import math
import re

import analyze as A
import options as O

BAD_TEXT = re.compile(r"choose a background|friends forever|doctor's companion", re.I)


def unique(idx):
    return list({id(v): v for v in idx.values()}.values())


def detect_tribe(cmd):
    """The commander's creature type, but ONLY if its rules text mentions that type
    (e.g. 'Whenever a Frog you control attacks'). Otherwise '' = not a tribal commander."""
    tl = cmd["type_line"]
    if "—" not in tl:
        return ""
    text = re.sub(r"create[^.]*?tokens?", " ", A._self_name_free(cmd), flags=re.I)
    for sub in tl.split("//")[0].split("—")[-1].split():
        forms = {sub, sub + "s", sub + "es"}
        if sub.endswith("f"):
            forms.add(sub[:-1] + "ves")          # Elf -> Elves, Wolf -> Wolves
        if sub.endswith("y"):
            forms.add(sub[:-1] + "ies")
        if re.search(r"\b(?:%s)\b" % "|".join(re.escape(f) for f in forms), text, re.I):
            return sub
    return ""


def _has_type(c, tribe):
    tl = c["type_line"]
    subtypes = tl.split("—", 1)[1] if "—" in tl else ""
    return bool(re.search(r"\b%s\b" % re.escape(tribe), subtypes, re.I)) or "changeling" in (c.get("text") or "").lower()


def lookup(idx, name):
    """Find a typed commander. On a miss, say what you probably meant instead of a bare error."""
    c = idx.get(name.strip().lower())
    names = sorted(x["name"] for x in unique(idx) if x.get("can_be_commander"))
    if c is None:
        close = difflib.get_close_matches(name.strip(), names, n=5, cutoff=0.55)
        hint = ("Did you mean: " + "; ".join(close)) if close else "Check the spelling (use the exact Scryfall card name)."
        raise SystemExit(f"Commander not found, or not Commander-legal: {name!r}. {hint}")
    if not c.get("can_be_commander"):
        raise SystemExit(f"{c['name']} can't be a commander (it isn't a legendary creature).")
    return c


def popularity(c):
    return max(0.0, 30 - 6 * math.log10(c["rank"] + 1)) if c.get("rank") else 0.0


def fit_row(c, *, mech=None, narr=None, tribe="", vibe_label=None, ref=None):
    """Score one commander against the user's choices. Returns a row dict with reasons."""
    vibe = O.VIBES[vibe_label] if vibe_label else None
    prof = A.analyze(c)
    m = O.mech_score(c, mech) if mech else 0
    n = O.narr_score(c, narr) if narr else 0
    pop = popularity(c)
    reasons, s = [], 0.0

    # 1. theme text on the commander
    s += 14 * min(m, 3) + 10 * min(n, 6)
    reasons += O.explain(c, mech, narr)
    # 2. plan alignment: its engine IS the theme
    if mech:
        al = sum(p["weight"] for p in prof["plans"] if A.PLANS[p["name"]].get("mech") == mech)
        if al:
            s += min(8 * al, 45)
            reasons.append(f"its own engine is {mech}")
    # 3. a clear plan beats a vanilla body
    clarity = sum(p["weight"] for p in prof["plans"][:3])
    s += min(clarity, 10) * 1.5
    # 4. vibe personality
    vfit, natural = 0.0, None
    if vibe_label:
        fit, natural, raw = A.vibe_fit(c, O.VIBES)
        vfit = fit[vibe_label]
        s += 25 * vfit + (20 if natural == vibe_label else 0)
        if natural == vibe_label:
            reasons.append(f"naturally a {vibe_label.split(':')[0]} commander")
        for slot in vibe["slots"]:
            if slot in O.vibe_tags(c):
                s += 10
    # 5. tribe
    if tribe:
        ref = detect_tribe(c) if ref is None else ref
        s += 40 if ref.lower() == tribe.lower() else 10
        if ref:
            reasons.append(f"cares about {ref}s")
    # 6. a little popularity (proven commanders), scaled by the vibe
    s += pop * 0.4 * (vibe["pop"] if vibe else 1.0)
    top = prof["plans"][0]["name"] if prof["plans"] else "no specific engine"
    return dict(name=c["name"], identity="".join(x for x in "WUBRG" if x in c["identity"]) or "C",
                rank=c.get("rank"), score=round(s, 1), pop=pop, m=m, n=n, vibe_fit=round(vfit, 2),
                natural_vibe=natural, plan=top, reasons=reasons or [f"plan: {top}"])


def candidates(idx, *, colors=None, mech=None, narr=None, tribe="", vibe=None, vibe_label=None, avoid=frozenset(),
               require_type_ref=False, forbid_type_ref=False, top=15, exclude=()):
    """Ranked commanders that fit. Returns (list_of_rows, note). Relaxes themes / vibe if too few fit."""
    mech = None if mech in (None, "", O.ANY) else mech
    narr = None if narr in (None, "", O.ANY) else narr
    exclude = {e.lower() for e in exclude}
    pool = []
    for c in unique(idx):
        if not c.get("can_be_commander") or BAD_TEXT.search(c.get("text") or "") or c["name"].lower() in exclude:
            continue
        if colors is not None and set(c["identity"]) != set(colors):
            continue
        if vibe and vibe["max_gc"] == 0 and c["game_changer"]:
            continue
        if O.avoided(c, avoid):
            continue
        ref = detect_tribe(c)
        if forbid_type_ref and ref:
            continue
        if require_type_ref and not ref:
            continue
        if tribe and not _has_type(c, tribe):
            continue
        pool.append((c, ref))

    rows = [fit_row(c, mech=mech, narr=narr, tribe=tribe, vibe_label=vibe_label, ref=ref) for c, ref in pool]

    def strict(r):
        return (not mech or r["m"] > 0 or "its own engine" in " ".join(r["reasons"])) and (not narr or r["n"] >= 2)

    def loose(r):
        return (mech and (r["m"] > 0 or "its own engine" in " ".join(r["reasons"]))) or (narr and r["n"] >= 1)

    note = "all selected filters matched"
    chosen = [r for r in rows if strict(r)]
    if len(chosen) < 5 and (mech or narr):
        chosen = [r for r in rows if loose(r)]
        note = "few commanders matched every theme, so partial theme matches are included"
        if len(chosen) < 3:
            chosen = rows
            note = "very few commanders matched the themes, so these are the best fits for the colors/vibe only"
    if not (mech or narr):
        chosen = rows
        note = "no themes selected: ranked by vibe personality, game-plan clarity and popularity"
    # Mean vibes: the commander itself should be mean (natural fit), unless that leaves almost nothing
    if vibe_label in O.MEAN_VIBES:
        mean = [r for r in chosen if r["natural_vibe"] in O.MEAN_VIBES or r["vibe_fit"] >= 0.8]
        if len(mean) >= 3:
            chosen = mean
        else:
            note += "; few naturally mean commanders fit these filters, so the closest matches are shown"
    chosen.sort(key=lambda r: (-r["score"], r["name"]))
    return chosen[:top], note


def better_fits(idx, cmd, plan, n_same=3, n_any=3):
    """After a build: commanders that match the user's vibe/themes BETTER than the one used.
    Same color identity first (easy swap), then any colors."""
    mech, narr = plan["mech"], plan["narr"]
    me = fit_row(cmd, mech=mech, narr=narr, tribe=plan["tribe"] if plan["tribe_mode"] == "explicit" else "",
                 vibe_label=plan["vibe_label"])
    avoid = set(plan["avoid"]) | set(plan["vibe"]["avoid"])
    kw = dict(mech=mech, narr=narr, tribe=plan["tribe"] if plan["tribe_mode"] == "explicit" else "",
              vibe=plan["vibe"], vibe_label=plan["vibe_label"], avoid=avoid, top=12, exclude=[cmd["name"]])
    same, _ = candidates(idx, colors=set(cmd["identity"]), **kw)
    colors = plan.get("colors_filter")
    anyc, _ = candidates(idx, colors=colors, **kw)
    out, seen = [], set()
    for rows, k, label in ((same, n_same, "same colors"), (anyc, n_any, "any colors" if colors is None else "your colors")):
        for r in rows:
            if len([o for o in out if o["group"] == label]) >= k:
                break
            if r["name"] in seen or r["score"] <= me["score"] + 3:
                continue
            seen.add(r["name"])
            out.append(dict(r, group=label, gain=round(r["score"] - me["score"], 1)))
    return me, out


def weighted_pick(rng, rows):
    """Weighted random choice that leans toward less popular (more surprising) commanders."""
    weights = [1.0 + (30 - r["pop"]) / 10 for r in rows]
    return rng.choices(rows, weights=weights, k=1)[0]
