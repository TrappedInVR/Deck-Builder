"""Commander selection: find legendary creatures that fit the chosen colors, themes, tribe and vibe."""
import difflib
import math
import re

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
    text = cmd.get("text") or ""
    for sub in tl.split("—")[-1].split():
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


def candidates(idx, *, colors=None, mech=None, narr=None, tribe="", vibe=None, avoid=frozenset(),
               require_type_ref=False, forbid_type_ref=False, top=15):
    """Ranked commanders that fit. Returns (list_of_dicts, note). Relaxes themes if too few fit."""
    mech = None if mech in (None, "", O.ANY) else mech
    narr = None if narr in (None, "", O.ANY) else narr
    pool = []
    for c in unique(idx):
        if not c.get("can_be_commander") or BAD_TEXT.search(c.get("text") or ""):
            continue
        if colors is not None and set(c["identity"]) != set(colors):
            continue
        if vibe and vibe["max_gc"] == 0 and c["game_changer"]:
            continue                                   # the commander itself would be a Game Changer
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

    rows = []
    for c, ref in pool:
        m = O.mech_score(c, mech) if mech else 0
        n = O.narr_score(c, narr) if narr else 0
        pop = popularity(c)
        s = 20 * min(m, 3) + 12 * min(n, 6) + pop * 0.5 * (vibe["pop"] if vibe else 1.0)
        vt = O.vibe_tags(c)
        for slot in (vibe["slots"] if vibe else {}):
            if slot in vt:
                s += 15
        if tribe:
            s += 40 if ref.lower() == tribe.lower() else 10
        rows.append(dict(name=c["name"], identity="".join(x for x in "WUBRG" if x in c["identity"]) or "C",
                         rank=c.get("rank"), score=round(s, 1), pop=pop, m=m, n=n,
                         reasons=O.explain(c, mech, narr) + ([f"references its own type ({ref})"] if ref and tribe else [])))

    def strict(r):
        return (not mech or r["m"] > 0) and (not narr or r["n"] >= 2)

    def loose(r):
        return (mech and r["m"] > 0) or (narr and r["n"] >= 1)

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
        note = "no themes selected: ranked by popularity and vibe"
    chosen.sort(key=lambda r: (-r["score"], r["name"]))
    return chosen[:top], note


def weighted_pick(rng, rows):
    """Weighted random choice that leans toward less popular (more surprising) commanders."""
    weights = [1.0 + (30 - r["pop"]) / 10 for r in rows]
    return rng.choices(rows, weights=weights, k=1)[0]
