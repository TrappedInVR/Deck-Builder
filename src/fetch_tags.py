"""Download Scryfall Tagger's community function tags ("otag:") for the concepts the deck builder uses.

Scryfall Tagger is a community project where people label what each card DOES (sacrifice outlet, board wipe,
mass pump...). Human labels catch what text rules miss, so the builder combines both (knowledge.py).

How it stays correct without hard-coding tag names:
  1. read Scryfall's list of tags (https://scryfall.com/docs/tagger-tags) and log which candidates exist there
  2. for each concept, try every candidate slug; the ones that return cards are used (a missing one is a quick 404)
  3. search the API for each slug (legal in Commander), following every result page
Scryfall's API rules are followed: a User-Agent and Accept header, and 120 ms between requests.
Writes data/tags.json: {"concepts": {concept: [slugs used]}, "cards": {name: [concepts]}, "missing": [...], "date": ...}
Never fails the workflow: on any network problem it writes what it has (the builder works without tags)."""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

UA = {"User-Agent": "DeckAutomation/0.2 (personal hobby project; weekly tag refresh)", "Accept": "application/json"}
DELAY = 0.12
MAX_PAGES = 40           # 175 cards per page -> up to 7000 cards per tag

# concept -> candidate Scryfall Tagger slugs (any that exist are used)
CONCEPTS = {
    "ramp": ["ramp", "mana-ramp"],
    "mana_rock": ["mana-rock"],
    "mana_dork": ["mana-dork"],
    "draw": ["draw", "card-draw", "card-advantage", "repeatable-draw"],
    "removal": ["removal", "creature-removal", "spot-removal", "artifact-removal", "enchantment-removal"],
    "board_wipe": ["board-wipe", "sweeper", "wrath", "mass-removal"],
    "one_sided_wipe": ["one-sided-board-wipe", "one-sided-sweeper", "one-sided-wrath"],
    "counterspell": ["counterspell"],
    "tutor": ["tutor"],
    "recursion": ["recursion", "graveyard-recursion", "regrowth"],
    "reanimate": ["reanimate", "reanimation"],
    "sac_outlet": ["sacrifice-outlet", "sac-outlet", "free-sacrifice-outlet"],
    "token_maker": ["token-generator", "creature-token-generator", "repeatable-token-generator", "token-maker"],
    "treasure": ["treasure-generator", "treasure-maker"],
    "anthem": ["anthem", "mass-pump", "team-pump"],
    "extra_combat": ["extra-combat", "additional-combat", "extra-combat-phase"],
    "haste": ["haste-granter", "gives-haste", "grant-haste", "haste-enabler"],
    "board_protect": ["protects-creatures", "mass-protection", "team-protection", "gives-indestructible",
                      "indestructible-granter", "mass-indestructible", "mass-hexproof", "phasing"],
    "damage_amp": ["damage-doubler", "damage-amplifier", "damage-multiplier", "damage-boost"],
    "trigger_doubler": ["trigger-doubler", "trigger-copy", "copy-ability"],
    "token_doubler": ["token-doubler"],
    "counter_doubler": ["counter-doubler"],
    "lifegain": ["lifegain", "life-gain"],
    "lifegain_payoff": ["lifegain-payoff", "lifegain-matters", "gain-life-trigger"],
    "drain": ["drain", "burn-each-opponent", "lose-life-each-opponent", "pinger"],
    "death_payoff": ["death-trigger", "aristocrat", "death-matters", "creature-dies-payoff"],
    "etb_payoff": ["creature-etb-payoff", "etb-payoff", "creature-enters-payoff", "enters-the-battlefield-payoff"],
    "untapper": ["untapper", "untap-creature", "untap-permanent"],
    "cost_reducer": ["cost-reducer", "cost-reduction"],
    "self_mill": ["self-mill"],
    "mill": ["mill"],
    "wheel": ["wheel"],
    "blink": ["blink", "flicker"],
    "clone": ["clone", "copy-permanent"],
    "copy_spell": ["copy-spell", "spell-copy"],
    "extra_land": ["extra-land-drop", "additional-land-drop", "extra-land"],
    "landfall": ["landfall", "landfall-payoff"],
    "fog": ["fog"],
    "stax": ["stax", "tax", "hatebear"],
    "graveyard_hate": ["graveyard-hate"],
    "evasion": ["evasion-granter", "gives-evasion", "unblockable-granter"],
    "cantrip": ["cantrip"],
}


def get(url):
    time.sleep(DELAY)
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()


def known_slugs():
    """Slugs that exist on Scryfall's tag list page (empty set if the page can't be read)."""
    try:
        html = get("https://scryfall.com/docs/tagger-tags").decode("utf-8", "replace")
    except Exception as e:
        print("could not read the tag list page:", e)
        return set()
    found = set(re.findall(r"otag(?::|%3A)([a-z0-9-]+)", html))
    found |= set(re.findall(r"/tags/card/([a-z0-9-]+)", html))
    print(f"tag list page: {len(found)} tags")
    return found


def search(slug):
    q = urllib.parse.quote(f"otag:{slug} legal:commander")
    url = f"https://api.scryfall.com/cards/search?q={q}&unique=cards&order=name"
    names, pages = [], 0
    while url and pages < MAX_PAGES:
        try:
            d = json.loads(get(url))
        except urllib.error.HTTPError as e:
            if e.code == 404:                  # no cards: tag doesn't exist (or is empty)
                return names
            raise
        names += [c["name"] for c in d.get("data", [])]
        url = d.get("next_page") if d.get("has_more") else None
        pages += 1
    return names


def main(out="data/tags.json"):
    exist = known_slugs()
    concepts, cards, missing = {}, {}, []
    errors = 0
    try:
        for concept, slugs in CONCEPTS.items():
            use = slugs                        # try every candidate (a missing tag costs one quick 404)
            used = []
            for s in use:
                try:
                    names = search(s)
                except Exception as e:
                    print(f"  {s}: error {e}")
                    errors += 1
                    if errors >= 5:
                        raise RuntimeError("Scryfall unreachable (5 errors in a row)")
                    continue
                errors = 0
                if names:
                    used.append(s)
                    for n in names:
                        cards.setdefault(n, set()).add(concept)
                print(f"  {concept:16} otag:{s:28} {len(names):5} cards" + ("" if not exist or s in exist else "  (not on the tag list page)"), flush=True)
            if used:
                concepts[concept] = used
            else:
                missing.append(concept)
    except Exception as e:
        print("stopped early:", e)
    if not cards:
        print("no tags downloaded; not writing a file so the next run tries again (the builder works without tags)")
        return
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    json.dump(dict(concepts=concepts, cards={n: sorted(v) for n, v in cards.items()}, missing=missing,
                   date=str(date.today()), credit="Function tags from Scryfall Tagger (tagger.scryfall.com)"),
              open(out, "w"))
    print(f"wrote {out}: {len(cards)} cards tagged, {len(concepts)} concepts found, missing: {', '.join(missing) or 'none'}")


if __name__ == "__main__":
    main(*(sys.argv[1:2]))
