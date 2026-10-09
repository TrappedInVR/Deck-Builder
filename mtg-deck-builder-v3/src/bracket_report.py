"""Rule-based Commander Bracket report from card data (Game Changers come from Scryfall's flag).
Cannot judge combos or true game speed. Paste the list into Commander Spellbook / Bracket Checker too."""
import argparse, json
from common import load_cards, parse_deck

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("deck")
    ap.add_argument("--target", type=int, default=3); ap.add_argument("--cards", default="data/cards.json")
    ap.add_argument("--json", default="out/bracket.json")
    a = ap.parse_args()
    cards, deck = load_cards(a.cards), parse_deck(a.deck)
    gc, mld, xt, tutors, unknown, total = [], [], [], [], [], 0
    for n, name in deck:
        total += n
        c = cards.get(name.lower())
        if not c: unknown.append(name); continue
        if c["game_changer"]: gc.append(c["name"])
        if "mass_land_denial" in c["tags"]: mld.append(c["name"])
        if "extra_turn" in c["tags"]: xt.append(c["name"])
        if "tutor" in c["tags"]: tutors.append(c["name"])
    floor = 1
    reasons = []
    if gc: floor = 3; reasons.append(f"{len(gc)} Game Changer(s)")
    if len(gc) > 3: floor = 4; reasons.append("more than 3 Game Changers")
    if mld: floor = 4; reasons.append("mass land denial")
    if len(xt) > 2: floor = max(floor, 3); reasons.append("several extra-turn cards")
    elif xt: floor = max(floor, 2)
    if floor <= 2 and not reasons: floor = 2
    ok = floor <= a.target
    rep = {"cards": total, "minimum_bracket_from_card_list": floor, "reasons": reasons,
           "game_changers": gc, "mass_land_denial": mld, "extra_turns": xt,
           "tutors_for_info": tutors, "unknown_cards": unknown, "fits_target": ok}
    json.dump(rep, open(a.json, "w"), indent=2)
    print(json.dumps(rep, indent=2))
    print(f"\nTarget Bracket {a.target}: {'OK' if ok else 'OVER'} on card-list rules.")
    print("Still check manually: infinite combos (Commander Spellbook) and whether games last 6+ turns.")

if __name__ == "__main__":
    main()
