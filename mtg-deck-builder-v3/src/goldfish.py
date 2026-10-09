"""Fast Monte Carlo goldfish. Models mana development only (no opponents, no combat).
Good for: mana base, curve, mulligan rate, when ramp/threats land. NOT a win-rate estimate."""
import argparse, json, random, re, statistics
from common import load_cards, parse_deck

def prep(deck, cards, tribe):
    cmd_name = deck[0][1]
    lib, cmd = [], None
    for n, name in deck:
        c = cards.get(name.lower())
        if not c:
            print(f"warning: unknown card {name}")
            continue
        info = {"name": c["name"], "cmc": int(c["cmc"]), "land": "land" in c["tags"],
                "ramp": "ramp" in c["tags"] and c["cmc"] <= 4 and "land" not in c["tags"],
                "bonus": 2 if "{C}{C}" in (c.get("text") or "") else 1,
                "tribe": bool(tribe) and tribe.lower() in c["type_line"].lower() and "Creature" in c["type_line"]}
        if name == cmd_name:
            cmd = info
        else:
            lib.extend([info] * n)
    return lib, cmd

def play_game(lib, cmd, rng, turns, big):
    deck = lib[:]; rng.shuffle(deck)
    hand, mulls = deck[:7], 0
    while mulls < 3:
        lands = sum(c["land"] for c in hand)
        if 2 <= lands <= 5:
            break
        mulls += 1; rng.shuffle(deck); hand = deck[:7]
    deck = deck[7:]
    for _ in range(mulls):
        extra = sorted(hand, key=lambda c: (not c["land"], -c["cmc"]))
        lands = sum(c["land"] for c in hand)
        hand.remove(extra[0] if lands > 3 else max(hand, key=lambda c: c["cmc"]))
    lands_in_play = bonus = pending = 0
    res = {"mulls": mulls, "lands": [], "mana": [], "tribe_turn": None, "cmd_turn": None, "big_turn": None}
    for t in range(1, turns + 1):
        if deck: hand.append(deck.pop(0))
        L = [c for c in hand if c["land"]]
        if L:
            hand.remove(L[0]); lands_in_play += 1
        bonus += pending; pending = 0
        mana = lands_in_play + bonus
        res["lands"].append(lands_in_play); res["mana"].append(mana)
        if cmd and res["cmd_turn"] is None and cmd["cmc"] <= mana:
            res["cmd_turn"] = t; mana -= cmd["cmc"]
        if res["big_turn"] is None and mana + (cmd["cmc"] if res["cmd_turn"] == t else 0) >= big:
            res["big_turn"] = t
        for c in sorted([c for c in hand if not c["land"]], key=lambda c: (not c["ramp"], not c["tribe"], -c["cmc"])):
            if c["cmc"] <= mana:
                mana -= c["cmc"]; hand.remove(c)
                if c["ramp"]: pending += c["bonus"]
                if c["tribe"] and res["tribe_turn"] is None: res["tribe_turn"] = t
    return res

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("deck"); ap.add_argument("--tribe", default="")
    ap.add_argument("--games", type=int, default=20000); ap.add_argument("--turns", type=int, default=10)
    ap.add_argument("--big-spell", type=int, default=8, help="mana value of your biggest finisher")
    ap.add_argument("--cards", default="data/cards.json"); ap.add_argument("--json", default="out/goldfish.json")
    a = ap.parse_args()
    cards = load_cards(a.cards)
    lib, cmd = prep(parse_deck(a.deck), cards, a.tribe)
    rng = random.Random(42)
    runs = [play_game(lib, cmd, rng, a.turns, a.big_spell) for _ in range(a.games)]
    def pct(f): return 100 * sum(1 for r in runs if f(r)) / len(runs)
    def avg_turn(k):
        v = [r[k] for r in runs if r[k] is not None]
        return (statistics.mean(v), 100 * len(v) / len(runs)) if v else (None, 0)
    out = {
        "games": a.games, "lands_in_deck": sum(c["land"] for c in lib) + 0,
        "mulligan_rate_pct": pct(lambda r: r["mulls"] > 0),
        "3_lands_by_T3_pct": pct(lambda r: r["lands"][2] >= 3),
        "4_mana_by_T4_pct": pct(lambda r: r["mana"][3] >= 4),
        "stuck_on_<=2_lands_T4_pct": pct(lambda r: r["lands"][3] <= 2),
        "avg_mana_T5": statistics.mean(r["mana"][4] for r in runs),
        "avg_mana_T8": statistics.mean(r["mana"][7] for r in runs),
    }
    for k in ("cmd_turn", "tribe_turn", "big_turn"):
        m, p = avg_turn(k); out[k] = {"avg_turn": m and round(m, 2), "reached_pct": round(p, 1)}
    import os; os.makedirs(os.path.dirname(a.json) or ".", exist_ok=True)
    json.dump(out, open(a.json, "w"), indent=2)
    print(json.dumps(out, indent=2))
    print("\nNote: mana-only model. 'big_turn' = first turn you could pay for your biggest spell.")

if __name__ == "__main__":
    main()
