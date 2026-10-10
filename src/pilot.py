"""A pilot's guide for the finished deck: how to play it, written from the commander's game plan (strategy.py) and
the cards actually in the deck. Rule-based like everything else: it names real cards from the list.

Sections: what to keep, turns 1-3, turns 4-6, how to close the game, what to watch out for, rebuilding after a wipe,
and the deck's key cards."""


def _names(rep, pred, k=4):
    out = [r["name"] for r in sorted(rep, key=lambda r: (r.get("cmc", 0), r["name"])) if pred(r)]
    return out[:k]


def _fmt(names):
    return ", ".join(names) if names else "-"


def guide(p):
    """Markdown lines for the run summary (p = plan.json)."""
    prof = p.get("profile") or {}
    rep = p.get("report") or []
    if not rep:
        return []
    strat = prof.get("strategy") or {}
    role = prof.get("role") or {}
    facets = strat.get("facets") or []
    needs = {n["id"]: n for n in strat.get("needs", [])}
    cmd = p.get("commander") or "the commander"
    cmc = int(prof.get("cmc") or 0)
    has = lambda r, k: k in (r.get("needs") or []) or k in (r.get("roles") or [])
    ramp = _names(rep, lambda r: "ramp" in r.get("roles", []) and r.get("cmc", 9) <= 2)
    cheap_jobs = _names(rep, lambda r: r.get("cmc", 9) <= 2 and (r.get("needs") or []) and "ramp" not in r.get("roles", []))
    engines = _names(rep, lambda r: has(r, "enter_engine") or ("draw" in r.get("roles", []) and len(r.get("needs") or []) >= 2), 5)
    protect_board = _names(rep, lambda r: has(r, "board_protect"), 5)
    protect_cmd = _names(rep, lambda r: "protection" in r.get("roles", []) and not has(r, "board_protect"), 4)
    retrig = _names(rep, lambda r: has(r, "retrigger"), 4)
    amp = _names(rep, lambda r: has(r, "amplify"), 4)
    haste = _names(rep, lambda r: has(r, "haste"), 3)
    rebuild = _names(rep, lambda r: "counts" in (r.get("needs") or []) and "tokens every turn" in (r.get("why") or ""), 4) or \
        _names(rep, lambda r: "recursion" in r.get("roles", []), 3)
    removal = _names(rep, lambda r: "removal" in r.get("roles", []) and r.get("cmc", 9) <= 3, 4)
    key = [r["name"] for r in sorted(rep, key=lambda r: -(r.get("synergy", 0) + 10 * r.get("links", 0)))[:6]]

    L = ["### How to play this deck", ""]
    L.append(f"**Keep:** 7-card hands with 2-4 lands and something to do early: ramp ({_fmt(ramp)}) or a cheap card that "
             f"does a job for the plan ({_fmt(cheap_jobs)}). Mulligan hands with 0-1 lands, or 5+ lands and nothing to cast.")
    lo = max(2, cmc - 1)
    when = f"turn {cmc}" if lo >= cmc else f"turn {lo}-{cmc}"
    early = f"**Turns 1-3:** ramp first, then cheap pieces of the plan. {cmd} costs {cmc}: aim to cast it on {when}"
    if "haste" in needs and not haste:
        early += ", ideally when it can attack next turn"
    elif haste:
        early += f" (with haste from {_fmt(haste)} it attacks right away)"
    L.append(early + ".")
    mid = "**Turns 4-6:** "
    if "go-wide" in facets:
        mid += f"widen the board every turn and get your value engines down ({_fmt(engines)}). "
    elif engines:
        mid += f"get your engines down ({_fmt(engines)}). "
    if protect_board:
        mid += f"Once the board is worth protecting, keep mana up for board protection ({_fmt(protect_board)})."
    elif protect_cmd:
        mid += f"Keep protection up for {cmd} ({_fmt(protect_cmd)})."
    L.append(mid)
    close = "**Closing the game:** "
    if role.get("role") == "finisher":
        close += f"{cmd} is the win condition ({', '.join(role.get('how') or [])}). "
        conds = prof.get("conditions") or []
        if conds:
            c0 = conds[0]
            what = f"creatures with {c0.get('keyword')}" if c0.get("kind") == "keyword" else \
                f"creatures with {c0['stat']} {c0['n']} or {'less' if c0['op'] == 'le' else 'greater'}"
            close += (f"Every one of your {what} adds to each trigger: with 8 of them out, one trigger is 8 to each opponent. ")
        if retrig:
            close += f"Make it trigger again with {_fmt(retrig)}. "
        if amp:
            close += f"Multiply every hit with {_fmt(amp)}. "
        close += "Count the table's life totals before you commit: the turn you can deal lethal, do it with protection up."
    else:
        wins = p.get("wincons") or []
        close += f"turn the advantage your engine built into a win with your win conditions ({_fmt(wins[:5])})."
    L.append(close)
    threat = [s for s in strat.get("story", []) if s.startswith("**Biggest threat")]
    if threat:
        L.append(threat[0].replace("**Biggest threat:**", "**Watch out for:**"))
    if removal:
        L.append(f"**Interaction:** save cheap removal ({_fmt(removal)}) for cards that stop YOUR plan (hate pieces, "
                 f"blockers that matter, opposing engines that win faster than you), not for whatever is biggest.")
    if "go-wide" in facets:
        L.append(f"**After a board wipe:** recast {cmd} (2 more mana each time), then rebuild with repeatable token makers "
                 f"({_fmt(rebuild)}); don't overextend into an opponent holding up mana with a wipe in their colors.")
    L.append(f"**Key cards:** {_fmt(key)}.")
    return L[:2] + [f"- {x}" for x in L[2:]] + [""]
