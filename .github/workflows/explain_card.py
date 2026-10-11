"""Explain how the builder understands cards: the 'teach it' loop.

Usage: python src/explain_card.py --cards "Sol Ring; Mentor of the Meek" [--commander "Arabella"]
Prints markdown (for the GitHub run summary): each card's roles, Scryfall Tagger tags, what it produces / needs,
quality, staple status and any correction from data/overrides.json. With a commander: the commander's game plan,
and how much each card helps it. If something is wrong, add a correction to data/overrides.json."""
import argparse

import analyze as A
import build_deck as B
import cardfn as CF
import commanders as CM
import evaluate as E
import knowledge as K
import options as O
import strategy as ST
from common import load_cards


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", default="")
    ap.add_argument("--commander", default="")
    ap.add_argument("--vibe", default=O.DEFAULT_VIBE)
    ap.add_argument("--data", default="data/cards.json")
    a = ap.parse_args()
    idx = load_cards(a.data)
    st = K.status()
    out = ["## How the deck builder understands these cards", ""]
    out.append(f"_Knowledge sources: rules text for every card; Scryfall Tagger tags for {st['tags_cards']} cards "
               f"({st['concepts']} concepts, refreshed {st['tags_date'] or 'never'}); {st['overrides']} corrections in "
               f"data/overrides.json._")
    out.append("")
    prof = ctx = None
    if a.commander.strip():
        cmd, note = CM.lookup(idx, a.commander)
        vibe = O.VIBES.get(a.vibe) or O.VIBES[O.DEFAULT_VIBE]
        prof = A.analyze(cmd, B.all_types(idx), vibe)
        ctx = dict(tribe="", vibe=vibe, vibe_label=a.vibe, mech=None, narr=None, profile=prof, typal=[])
        out += [f"### Commander: {cmd['name']}", ""] + ([f"_{note}_", ""] if note else [])
        r = prof["role"]
        out.append(f"- **Role:** {r['role']}. {r['note']}")
        out.append(f"- **Plans:** " + "; ".join(f"{p['name']} ({p['weight']})" for p in prof["plans"]))
        for s in (prof.get("strategy") or {}).get("story", []):
            out.append(f"- {s}")
        for n in (prof.get("strategy") or {}).get("needs", []):
            out.append(f"- Wants {n['quota']}+ cards that {n['label']}.")
        out.append("")
    for name in [n.strip() for n in a.cards.split(";") if n.strip()]:
        c, note = CM.resolve_card(idx, name)
        f = CF.functions(c)
        q = E.assess(c)
        out.append(f"### {c['name']}")
        out.append("")
        if note:
            out.append(f"_{note}_")
        out.append(f"- {c['type_line']}, {int(c.get('cmc') or 0)} MV. {E.describe(c)}")
        out.append(f"- **Roles:** {', '.join(sorted(E.role_tags(c))) or 'none'} | **quality** {q['q']}/100 for its job and cost")
        out.append(f"- **Scryfall Tagger:** {', '.join(c.get('otags') or []) or 'no tags (or tag data not downloaded yet)'}")
        out.append(f"- **Produces:** {', '.join(sorted(CF.LABELS.get(x, x) for x in f['produces'])) or '-'}")
        out.append(f"- **Needs:** {', '.join(sorted(CF.LABELS.get(x, x) for x in f['wants'])) or '-'}")
        if c.get("_note") or c.get("_staple") is not None or c.get("_never"):
            out.append(f"- **Your correction:** {c.get('_note') or ''} {'(staple)' if c.get('_staple') else ''}"
                       f"{'(never auto-picked)' if c.get('_never') else ''}")
        if prof:
            B.prepare(c, ctx)
            met = [ST.LABELS.get(x) or A.NEED_NAMES.get(x) or x.split(":", 1)[-1] for x in c.get("_needs") or []]
            out.append(f"- **With this commander:** synergy {round(c['_syn'], 1)} ({c['_syn_why'] or 'no direct synergy'}); "
                       f"covers: {', '.join(met) or 'nothing specific'}; staple: {'yes' if B.is_staple(c) else 'no'}"
                       + (f"; **left out: {c['_avoid']}**" if c.get("_avoid") else ""))
        out.append("")
    print("\n".join(out))


if __name__ == "__main__":
    main()
