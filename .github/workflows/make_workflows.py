"""Generate .github/workflows/build.yml and suggest.yml from options.py, so the dropdown labels in the
GitHub forms always match what the code expects. Run after editing options.py:  python src/make_workflows.py"""
import json, os
import options as O

q = json.dumps        # JSON strings are valid YAML double-quoted strings


def inp(name, desc, kind="string", default="", options=None):
    lines = [f"      {name}:", f"        description: {q(desc)}", f"        required: false"]
    if kind == "boolean":
        lines += ["        type: boolean", f"        default: {str(bool(default)).lower()}"]
    elif kind == "choice":
        lines += ["        type: choice", f"        default: {q(default)}", "        options:"] + [f"          - {q(o)}" for o in options]
    else:
        lines += [f"        default: {q(default)}"]
    return "\n".join(lines)


VIBE_HELP = " | ".join(f"{k.split(' (')[0]}: {v['blurb']}" for k, v in O.VIBES.items())
AVOID_HELP = ", ".join(sorted(O.AVOID_WORDS))

# The form, top to bottom. Short numbered labels; everything except the vibe is optional.
F_COMMANDER = inp("commander", "1. Commander (optional). Type a name, even partly or misspelled, and the closest real commander is "
                  "used. Leave blank to have one picked for you.", "string", "")
F_VIBE = lambda n: inp("vibe", f"{n}. Vibe: how the deck feels to play against. Every vibe is Bracket 3.", "choice", O.DEFAULT_VIBE, O.VIBE_LABELS)
F_COLORS = lambda n, extra: inp("colors", f"{n}. Colors{extra}.", "choice", O.ANY, O.COLOR_LABELS)
F_MECH = lambda n: inp("mechanical_theme", f"{n}. Mechanical theme (optional): how the deck plays.", "choice", O.ANY, O.MECH_LABELS)
F_NARR = lambda n: inp("narrative_theme", f"{n}. Narrative theme (optional): the flavor and story.", "choice", O.ANY, O.NARR_LABELS)
F_TRIBE = lambda n: inp("tribe", f"{n}. Creature type(s) to build around (optional), e.g. Elf or Angel/Dragon. "
                        "Blank = only if the commander supports a type.", "string", "")

BUILD_INPUTS = [
    F_COMMANDER,
    F_VIBE(2),
    F_COLORS(3, " (only used when Commander is blank)"),
    F_MECH(4),
    F_NARR(5),
    F_TRIBE(6),
    inp("avoid", "7. Never include (optional), comma separated: " + AVOID_HELP + ".", "string", ""),
    inp("must_include", "8. Cards to always include (optional), separated by ; (e.g. Sol Ring; Rhystic Study).", "string", ""),
    inp("exclude", "9. Cards to leave out (optional), separated by ;", "string", ""),
    inp("reference", "10. Reference cards (optional), separated by ;: cards you've seen real decks run with this commander "
        "(e.g. from browsing EDHREC yourself). Strongly favored, but still checked against every rule.", "string", ""),
    inp("budget", "11. Budget in $ for the nonland cards (optional, max 1000). Blank = $500, and stronger vibes may "
        "stretch above it (up to $1000) only for cards worth it. A number you type is a hard cap. Lands never count.",
        "string", ""),
    inp("randomize", "12. Surprise me! Random vibe, colors and themes. Anything you typed above still counts.", "boolean", False),
    inp("seed", "13. Seed (optional): copy the number from a past run's summary to rebuild the same random deck.", "string", ""),
]
SUGGEST_INPUTS = [F_VIBE(1), F_COLORS(2, ""), F_MECH(3), F_NARR(4), F_TRIBE(5)]

HEAD = """on:
  workflow_dispatch:
    inputs:
"""

CACHE_AND_FETCH = """      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - id: day
        run: echo "d=$(date +%F)" >> "$GITHUB_OUTPUT"
      - uses: actions/cache@v4            # one Scryfall download per day, reused across runs
        with:
          path: data/cards.json
          key: cards-v3-${{ steps.day.outputs.d }}
      - name: Fetch Scryfall data (skipped if cached today)
        run: test -s data/cards.json || python src/fetch_cards.py
      - uses: actions/cache@v4            # Commander Spellbook combo database, once per day
        with:
          path: data/combos.json
          key: combos-v1-${{ steps.day.outputs.d }}
      - name: Fetch combo data from Commander Spellbook (skipped if cached today; never fails the run)
        run: test -s data/combos.json || python src/fetch_combos.py
      - id: week
        run: echo "w=$(date +%G-%V)" >> "$GITHUB_OUTPUT"
      - uses: actions/cache@v4            # Scryfall Tagger function tags, once per week (a few hundred polite API calls)
        with:
          path: data/tags.json
          key: tags-v1-${{ steps.week.outputs.w }}
      - name: Fetch Scryfall Tagger function tags (skipped if cached this week; never fails the run)
        run: test -s data/tags.json || python src/fetch_tags.py || true
      - uses: actions/cache@v4            # MTGJSON precon lists (weekly) + EDHTop16 per-commander tournament data (7 days)
        with:
          path: |
            data/reference.json
            data/edhtop16
          key: reference-v1-${{ steps.week.outputs.w }}
      - name: Fetch precon decklists from MTGJSON (skipped if cached this week; never fails the run)
        run: test -s data/reference.json || python src/fetch_reference.py || true
      - uses: actions/cache@v4            # Forge card scripts (structured card logic), once per week
        with:
          path: |
            data/forge
            data/forge_db.json
          key: forge-v1-${{ steps.week.outputs.w }}
      - name: Fetch Forge card scripts (skipped if cached this week; never fails the run)
        run: |
          if [ ! -d data/forge/forge-gui/res/cardsfolder ]; then
            git clone --depth 1 --filter=blob:none --sparse https://github.com/Card-Forge/forge.git data/forge \
              && git -C data/forge sparse-checkout set forge-gui/res/cardsfolder \
              && echo "Forge scripts: $(find data/forge/forge-gui/res/cardsfolder -name '*.txt' | wc -l) cards" \
              || echo "Forge scripts unavailable this run; the builder reads rules text only"
          fi
"""

build = "\n".join([
    "name: Build and check a deck",
    "# GENERATED by src/make_workflows.py from src/options.py. Edit options.py and regenerate; don't hand-edit the choices.",
    "# Click \"Run workflow\", pick your options, then read the summary on the run page (or download the deck-report artifact).",
    HEAD.rstrip("\n"), *BUILD_INPUTS,
]) + """
jobs:
  build:
    runs-on: ubuntu-latest
    env:
      RANDOMIZE: ${{ inputs.randomize }}
      VIBE: ${{ inputs.vibe }}
      COLORS: ${{ inputs.colors }}
      MECH: ${{ inputs.mechanical_theme }}
      NARR: ${{ inputs.narrative_theme }}
      TRIBE: ${{ inputs.tribe }}
      COMMANDER: ${{ inputs.commander }}
      BUDGET: ${{ inputs.budget }}
      MUST: ${{ inputs.must_include }}
      EXCLUDE: ${{ inputs.exclude }}
      REFERENCE: ${{ inputs.reference }}
      AVOID: ${{ inputs.avoid }}
      SEED: ${{ inputs.seed }}
    steps:
""" + CACHE_AND_FETCH + """      - name: Plan and build the deck
        run: >
          python src/build_deck.py --randomize "$RANDOMIZE" --vibe "$VIBE" --colors "$COLORS" --mech "$MECH"
          --narr "$NARR" --tribe "$TRIBE" --commander "$COMMANDER" --budget "$BUDGET"
          --must "$MUST" --exclude "$EXCLUDE" --reference "$REFERENCE" --avoid "$AVOID" --seed "$SEED"
      - name: Goldfish simulation
        # Uses the tribe the builder settled on (typed, auto-detected, or none), saved in out/tribe.txt.
        run: python src/goldfish.py out/deck.txt --tribe "$(cat out/tribe.txt)"
      - name: Bracket report
        run: python src/bracket_report.py out/deck.txt --target "$(cat out/target.txt)"
      - name: Run summary
        if: always()
        run: python src/summarize.py >> "$GITHUB_STEP_SUMMARY"
      - uses: actions/upload-artifact@v4
        if: always()
        with: { name: deck-report, path: out/ }
"""

suggest = "\n".join([
    "name: Suggest commanders",
    "# GENERATED by src/make_workflows.py from src/options.py.",
    "# Pick vibe / colors / themes, run it, and read the ranked commander list on the run page.",
    "# Then type your favorite into the Commander box of \"Build and check a deck\".",
    HEAD.rstrip("\n"), *SUGGEST_INPUTS,
]) + """
jobs:
  suggest:
    runs-on: ubuntu-latest
    env:
      VIBE: ${{ inputs.vibe }}
      COLORS: ${{ inputs.colors }}
      MECH: ${{ inputs.mechanical_theme }}
      NARR: ${{ inputs.narrative_theme }}
      TRIBE: ${{ inputs.tribe }}
    steps:
""" + CACHE_AND_FETCH + """      - name: Find commanders that fit
        run: >
          python src/build_deck.py --suggest-only --vibe "$VIBE" --colors "$COLORS" --mech "$MECH"
          --narr "$NARR" --tribe "$TRIBE"
      - name: Run summary
        if: always()
        run: python src/summarize.py --suggest >> "$GITHUB_STEP_SUMMARY"
"""

explain = """name: Explain cards
# GENERATED by src/make_workflows.py. Shows how the deck builder understands cards (roles, Scryfall Tagger tags,
# what each card produces and needs, staple status), optionally against a commander's game plan.
# If something is wrong, add a correction to data/overrides.json (see knowledge.py for the format).
on:
  workflow_dispatch:
    inputs:
      cards:
        description: "1. Card names, separated by ; (e.g. Sol Ring; Mentor of the Meek)"
        required: false
        default: ""
      commander:
        description: "2. Commander (optional): also show its game plan and how much each card helps it"
        required: false
        default: ""
jobs:
  explain:
    runs-on: ubuntu-latest
    env:
      CARDS: ${{ inputs.cards }}
      COMMANDER: ${{ inputs.commander }}
    steps:
""" + CACHE_AND_FETCH + """      - name: Explain
        run: python src/explain_card.py --cards "$CARDS" --commander "$COMMANDER" >> "$GITHUB_STEP_SUMMARY"
"""

if __name__ == "__main__":
    os.makedirs(".github/workflows", exist_ok=True)
    open(".github/workflows/build.yml", "w", encoding="utf-8").write(build)
    open(".github/workflows/suggest.yml", "w", encoding="utf-8").write(suggest)
    open(".github/workflows/explain.yml", "w", encoding="utf-8").write(explain)
    print("wrote .github/workflows/build.yml, suggest.yml and explain.yml")
