"""Turn the files in out/ into a readable Markdown summary for the GitHub run page ($GITHUB_STEP_SUMMARY).
Usage: python src/summarize.py [--suggest]"""
import json
import os
import sys

ROLE_NAMES = dict(ramp="Ramp", draw="Card draw", removal="Removal", counter="Counterspells", sweeper="Board wipes",
                  recursion="Recursion", protection="Commander protection")
STEP_ORDER = ["You asked for it", "Game Changer", "Ramp", "Card draw", "Removal", "Counterspells", "Board wipes",
              "Recursion", "Commander protection", "Commander plan", "Ability support", "Combo", "Creature base", "Vibe", "Your theme", "Tribe",
              "Best remaining fit"]


def load(name):
    try:
        return json.load(open(os.path.join("out", name)))
    except (OSError, ValueError):
        return None


def cell(s):
    return str(s).replace("|", "/").replace("\n", " ")


def main():
    suggest = "--suggest" in sys.argv
    p = load("plan.json")
    if not p:
        print("## No plan was produced\nThe build step failed before writing out/plan.json. Open the failed step above for the reason.")
        return
    out = []
    A = out.append
    A("## Suggested commanders" if suggest else "## Your deck")
    A("")
    A("| Setting | Choice |\n|---|---|")
    A(f"| Vibe | {p['vibe']} |")
    A(f"| Vibe in plain words | {cell(p['vibe_blurb'])} |")
    A(f"| Colors | {p.get('colors') or 'Any'} |")
    A(f"| Mechanical theme | {p['mechanical_theme']} |")
    A(f"| Narrative theme | {p['narrative_theme']} |")
    if not suggest:
        A(f"| **Commander** | **{p['commander']}** ({p['commander_how']}) |")
        A(f"| Tribe | {p['tribe'] or 'none'} ({p['tribe_mode']}) |")
        A(f"| Bracket | {p['bracket']} (every vibe builds a Bracket 3 deck) |")
        A(f"| Budget | ${p['budget']:.0f} for the nonland cards (estimated ${p.get('spent', 0):.0f}; prices are rough). "
          f"Lands never count against it. |")
        A(f"| Seed | {p['seed']} (use it to reproduce a random result) |")
        if p["avoid"]:
            A(f"| Avoiding | {', '.join(p['avoid'])} |")
    A("")
    for w in p.get("warnings", []):
        A(f"> **Note:** {w}")
    if p.get("warnings"):
        A("")

    opts = p.get("options") or []
    if opts:
        A(f"### Commanders that fit ({p['options_note']})")
        A("")
        A("| # | Commander | Colors | Its game plan | Why it fits | EDHREC rank |\n|---|---|---|---|---|---|")
        for i, r in enumerate(opts, 1):
            A(f"| {i} | {r['name']} | {r['identity']} | {r.get('plan', '')} | {cell('; '.join(r['reasons']))} | {r['rank'] or '?'} |")
        A("")
        A("Want a different one? Copy its exact name into the **Commander** box of *Build and check a deck* and run it again. "
          "A typed commander always overrides the dropdown choices.")
        A("")
    if suggest:
        print("\n".join(out))
        return

    val = p.get("validation")
    if val is not None:
        A("**Deck check:** " + ("passed all rules (100 cards, singleton, colors, land count, no off-color cards, "
                                "Game Changers, combos)." if not val else f"{len(val)} problem(s) found, listed in the notes above."))
        A("")

    # ---- how the commander plays
    prof = p.get("profile") or {}
    A(f"### How {p['commander']} wants to win")
    A("")
    if prof.get("plans"):
        A("The deck is built around these plans, strongest first. Each is read straight from the commander's card:")
        A("")
        for pl in prof["plans"]:
            ev = "; ".join(f'"{w}"' for w in pl["why"])
            A(f"- **{pl['name']}** (weight {pl['weight']}): {ev}. {p['plan_counts'].get(pl['name'], 0)} cards in the deck support it.")
    else:
        A("- Its text has no specific engine, so the deck follows your themes and the vibe.")
    for n in prof.get("notes", []):
        A(f"- {n}")
    A("")
    strat = prof.get("strategy") or {}
    if strat.get("story"):
        A("**Game plan**" + (f" ({', '.join(strat['facets'])})" if strat.get("facets") else "") + ":")
        A("")
        for line in strat["story"]:
            A(f"- {line}")
        sc = p.get("strategy_counts") or {}
        for n in strat.get("needs", []):
            A(f"- {n['label'].capitalize()}: {sc.get(n['id'], 0)} cards (target {n['quota']}). _{n['why']}._")
        A("")
    try:
        import pilot
        for line in pilot.guide(p):
            A(line)
    except Exception as e:                     # the guide is a bonus: never break the summary
        A(f"_(pilot guide unavailable: {e})_")
    role = prof.get("role") or {}
    if role:
        label = {"finisher": "Finisher", "engine": "Engine", "value": "Value piece"}[role["role"]]
        A(f"**The commander's role: {label}.** {role['note']}")
        if p.get("speed"):
            sp = p["speed"]
            A(f"Estimated earliest win: **turn {sp['turn']:g}** ({sp['how']}). Bracket 3 aims for turn 6-8, so Game Changers, "
              f"tutors, fast mana and combos were only added after the synergy core was built, and only if they kept it there.")
        if p.get("wincons") is not None:
            A(f"Win conditions in the 99: {len(p['wincons'])}" + (f" ({', '.join(p['wincons'][:8])})" if p["wincons"] else "") + ".")
        A("")
    abil = [a for a in prof.get("abilities", []) if a["kind"] != "keyword"]
    if abil:
        A("**Its abilities, one by one:**")
        A("")
        A("| Ability | Type | Waits for | Costs | Produces |\n|---|---|---|---|---|")
        for ab in abil:
            A(f"| {cell(ab['text'][:90])} | {ab['kind']} | {ab['event'] or '-'} | {', '.join(ab['costs']) or '-'} | "
              f"{', '.join(ab['outputs']) or '-'} |")
        A("")
    routes = prof.get("routes") or []
    if len(routes) > 1:
        A("**Ways to play this commander** (the vibe picks the lead unless one plan clearly dominates; the rest stay in as support):")
        A("")
        A("| Game plan | Style | From the card | After your vibe |\n|---|---|---|---|")
        for i, r in enumerate(routes[:6]):
            A(f"| {'**' + r['name'] + '** (lead)' if i == 0 else r['name']} | {r.get('style') or '-'} | {r['base']} | {r['weight']} |")
        A("")
    if prof.get("lead_note"):
        A(f"_{prof['lead_note']}_")
        A("")
    inter = prof.get("interactions") or []
    if inter:
        A("**Cards chosen to work with its abilities:**")
        A("")
        for p_ in inter:
            A(f"- {p_['name']}: **{p['plan_counts'].get(p_['name'], 0)}** cards")
    if prof.get("typal"):
        A(f"- Creature types it cares about: {', '.join(prof['typal'])}")
    A("")
    match = p.get("vibe_match")
    nat = p.get("natural_vibe") or ""
    if match == "natural fit":
        A(f"**Vibe match: natural fit.** {p['commander']} naturally plays like *{p['vibe'].split(':')[0]}*.")
    elif match:
        A(f"**Vibe match: {match}.** On its own, {p['commander']} plays most like *{nat.split(':')[0]}*. The deck still leans "
          f"into *{p['vibe'].split(':')[0]}* (signature cards: "
          + (", ".join(f"{k} {v}" for k, v in p.get("feel", {}).items()) or "interaction levels and Game Changers")
          + "), picking versions that also support the commander's plan so the commander is never ignored.")
    A("")

    # ---- what's in the deck
    r, q = p["roles"], p.get("quotas", {})
    A("### What is in the deck")
    A("")
    A("| Role | Cards | Target |\n|---|---|---|")
    for k in ROLE_NAMES:
        if r.get(k) or q.get(k):
            A(f"| {ROLE_NAMES[k]} | {r.get(k, 0)} | {q.get(k, '-')} |")
    A("")
    A(f"- **{p.get('onplan', 0)}** cards directly support the commander's plan | average card quality **{p.get('avg_quality', 0)}**/100 "
      f"(compared against the best card for the same job)")
    if p.get("deck_links") is not None:
        rep = p.get("report") or []
        staples = [r["name"] for r in rep if r.get("staple")]
        A(f"- Deck synergy: each card links to the rest of the deck **{p['deck_links']}** ways on average (it uses what other "
          f"cards make, or makes what they use). {len(p.get('deck_swaps') or [])} cards were swapped in by the deck synergy pass."
          + (f" Stand-alone cards (no links, no commander synergy): {', '.join(p['unlinked'])}." if p.get("unlinked") else ""))
        if staples:
            A(f"- Staples kept for raw efficiency (the exception to synergy-first): {', '.join(staples)}")
    A(f"- Creatures {p['creatures']} (cap {p.get('creature_cap')}) | tutors {p['tutors']} | extra-turn cards {p['extra_turns']}")
    if p["mechanical_theme"] != "Any (no preference)":
        A(f"- Cards matching **{p['mechanical_theme']}**: {p['mech_hits']}")
    if p["narrative_theme"] != "Any (no preference)":
        A(f"- Cards fitting **{p['narrative_theme']}**: {p['narr_hits']} (a soft nudge, not a requirement)")
    A(f"- Game Changers ({len(p['gc_cards'])}/3): {', '.join(p['gc_cards']) or 'none'}")
    lk = p.get("land_kinds") or {}
    A(f"- Lands: {p['nonbasic']} nonbasic (" + ", ".join(f"{v} {k}" for k, v in lk.items()) +
      f"), **{p.get('tapped_lands', 0)} enter tapped**. Land prices (~${p.get('land_cost', 0):.0f}) are not counted in the budget.")
    A("")

    # ---- card by card
    rep = p.get("report") or []
    if rep:
        A("<details><summary><b>Why each card is in the deck</b> (click to open)</summary>")
        A("")
        A("Quality = how good the card is at its job vs the best cards for that job (0-100). "
          "Synergy = how much it helps this commander's plans.")
        A("")
        steps = sorted({x["step"] for x in rep}, key=lambda s: STEP_ORDER.index(s) if s in STEP_ORDER else 99)
        for step in steps:
            rows = [x for x in rep if x["step"] == step]
            A(f"**{step}** ({len(rows)})")
            A("")
            A("| Card | Why | Quality | Synergy |\n|---|---|---|---|")
            for x in rows:
                A(f"| {x['name']} | {cell(x['why'])} | {x['quality']} | {x['synergy']} |")
            A("")
        A("</details>")
        A("")

    edged = p.get("edged") or {}
    if any(edged.values()):
        A("<details><summary><b>Close calls: strong cards that didn't make it</b></summary>")
        A("")
        A("| Role | Card | Quality | Why it's out |\n|---|---|---|---|")
        for role, alts in edged.items():
            for x in alts:
                A(f"| {ROLE_NAMES.get(role, role)} | {x['name']} | {x['quality']} | {cell(x['cut'])} |")
        A("")
        A("Swap any of these in with the **Must include** box (and push something out with **Exclude**).")
        A("</details>")
        A("")

    cb = p.get("combos") or {}
    A("### Combos")
    A("")
    if not cb.get("available"):
        A("- Combo data wasn't available this run, so combos were not checked. (It downloads from Commander Spellbook; try again later.)")
    else:
        A(f"- Allowed for this vibe: up to **{cb.get('max_tag_name')}**. Early two-card wins (Ruthless) are never allowed in Bracket 3.")
        if cb.get("in_deck"):
            A("- Combos in this deck:")
            for k in cb["in_deck"]:
                needs = f" (also needs {', '.join(k['templates'])})" if k.get("templates") else ""
                A(f"  - [{cell(k['desc'])}]({k['link']}): {k['tag_name']}{needs}")
        else:
            A("- No complete combos in this deck.")
        for k in cb.get("removed") or []:
            A(f"- Swapped out **{k['cut']}** to break {cell(k['combo'])} ({k['tag']}).")
        if cb.get("near"):
            A("- One card away (add it with **Must include** if you want the combo):")
            for k in cb["near"]:
                A(f"  - add **{k['add']}**: [{cell(k['desc'])}]({k['link']}) ({k['tag_name']})")
        A(f"- {cb.get('count', 0)} known combos checked. Combo data from [Commander Spellbook](https://commanderspellbook.com/).")
    A("")

    better = p.get("better_commanders") or []
    A("### Commanders that may fit your choices even better")
    A("")
    if better:
        me = p.get("current_fit") or {}
        A(f"These score higher than {p['commander']} (fit score {me.get('score', '?')}) for the vibe and themes you picked:")
        A("")
        A("| Commander | Colors | Its game plan | Why | Fit gain |\n|---|---|---|---|---|")
        for b in better:
            A(f"| {b['name']} | {b['identity']} ({b['group']}) | {b.get('plan', '')} | {cell('; '.join(b['reasons'][:3]))} | +{b['gain']} |")
        A("")
        A("To try one, type its name in the **Commander** box and run again.")
    else:
        A(f"None found: {p['commander']} is already one of the best fits for these choices.")
    A("")

    b = load("bracket.json")
    if b:
        A("### Bracket check (card list only)")
        A("")
        A(f"- Minimum bracket from the card list: **{b['minimum_bracket_from_card_list']}** vs target **{p['bracket']}** -> "
          f"{'OK' if b['fits_target'] else 'OVER'}")
        if b["reasons"]:
            A(f"- Because: {', '.join(b['reasons'])}")
        A("- Combos are checked against Commander Spellbook (see Combos above). Not checked: real game speed.")
        A("")
    g = load("goldfish.json")
    if g:
        A("### Mana and speed (goldfish: no opponents)")
        A("")
        A(f"- 3 lands by turn 3: {g['3_lands_by_T3_pct']:.0f}% | 4 mana by turn 4: {g['4_mana_by_T4_pct']:.0f}% | "
          f"mulligan rate: {g['mulligan_rate_pct']:.0f}%")
        c = g["cmd_turn"]
        A(f"- Commander castable by turn {c['avg_turn']} on average ({c['reached_pct']}% of games)")
        bg = g.get("big_turn") or {}
        if g.get("biggest_spell_mv") and bg.get("avg_turn"):
            A(f"- Biggest spell ({g['biggest_spell_mv']} mana) castable by turn {bg['avg_turn']} on average ({bg['reached_pct']}% of games)")
        A("")
    A("The full list is in the **deck-report** artifact (`deck.txt`, ready to paste into Moxfield with Bulk Edit).")
    print("\n".join(out))


if __name__ == "__main__":
    main()
