"""Turn the files in out/ into a readable Markdown summary for the GitHub run page ($GITHUB_STEP_SUMMARY).
Usage: python src/summarize.py [--suggest]"""
import json, os, sys

def load(name):
    try:
        return json.load(open(os.path.join("out", name)))
    except (OSError, ValueError):
        return None

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
    A(f"| Vibe in plain words | {p['vibe_blurb']} |")
    A(f"| Colors | {p.get('colors') or 'Any'} |")
    A(f"| Mechanical theme | {p['mechanical_theme']} |")
    A(f"| Narrative theme | {p['narrative_theme']} |")
    if not suggest:
        A(f"| **Commander** | **{p['commander']}** ({p['commander_how']}) |")
        A(f"| Tribe | {p['tribe'] or 'none'} ({p['tribe_mode']}) |")
        A(f"| Target bracket | {p['bracket']} |")
        A(f"| Budget | ${p['budget']:.0f} (estimated cost ${p.get('spent', 0):.0f}; prices are rough) |")
        A(f"| Seed | {p['seed']} (use it to reproduce a random result) |")
        if p["avoid"]:
            A(f"| Avoiding | {', '.join(p['avoid'])} |")
    A("")
    for w in p.get("warnings", []):
        A(f"> **Warning:** {w}")
    if p.get("warnings"):
        A("")
    opts = p.get("options") or []
    if opts:
        A(f"### Commanders that fit ({p['options_note']})")
        A("")
        A("| # | Commander | Colors | Why it fits | EDHREC rank |\n|---|---|---|---|---|")
        for i, r in enumerate(opts, 1):
            A(f"| {i} | {r['name']} | {r['identity']} | {'; '.join(r['reasons']) or 'colors / vibe fit'} | {r['rank'] or '?'} |")
        A("")
        A("Want a different one? Copy its exact name into the **Commander** box of *Build and check a deck* and run it again. "
          "A typed commander always overrides the dropdown choices.")
        A("")
    if suggest:
        print("\n".join(out)); return

    r = p["roles"]
    A("### What is in the deck")
    A("")
    A(f"- Roles: ramp {r['ramp']}, draw {r['draw']}, removal {r['removal']}, counterspells {r['counter']}, "
      f"sweepers {r['sweeper']}, recursion {r['recursion']}")
    A(f"- Creatures {p['creatures']} | tutors {p['tutors']} | extra-turn cards {p['extra_turns']} | nonbasic lands {p['nonbasic']}")
    if p["mechanical_theme"] != "Any (no preference)":
        A(f"- Cards matching **{p['mechanical_theme']}**: {p['mech_hits']}")
    if p["narrative_theme"] != "Any (no preference)":
        A(f"- Cards fitting **{p['narrative_theme']}**: {p['narr_hits']} (a soft nudge, not a requirement)")
    if p.get("feel"):
        A("- Vibe signature cards: " + ", ".join(f"{k} {v}" for k, v in p["feel"].items()))
    A(f"- Game Changers ({len(p['gc_cards'])}): {', '.join(p['gc_cards']) or 'none'}")
    A("")
    b = load("bracket.json")
    if b:
        A("### Bracket check (card list only)")
        A("")
        A(f"- Minimum bracket from the card list: **{b['minimum_bracket_from_card_list']}** vs target **{p['bracket']}** -> "
          f"{'OK' if b['fits_target'] else 'OVER'}")
        if b["reasons"]:
            A(f"- Because: {', '.join(b['reasons'])}")
        A("- Not checked: infinite combos (use Commander Spellbook) and real game speed.")
        A("")
    g = load("goldfish.json")
    if g:
        A("### Mana and speed (goldfish: no opponents)")
        A("")
        A(f"- 3 lands by turn 3: {g['3_lands_by_T3_pct']:.0f}% | 4 mana by turn 4: {g['4_mana_by_T4_pct']:.0f}% | "
          f"mulligan rate: {g['mulligan_rate_pct']:.0f}%")
        c = g["cmd_turn"]
        A(f"- Commander castable by turn {c['avg_turn']} on average ({c['reached_pct']}% of games)")
        A("")
    A("The full list is in the **deck-report** artifact (`deck.txt`, ready to paste into Moxfield with Bulk Edit).")
    print("\n".join(out))

if __name__ == "__main__":
    main()
