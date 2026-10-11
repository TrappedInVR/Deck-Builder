"""The card knowledge layer: human function tags (Scryfall Tagger) + your own corrections, applied to every card.

Three sources of understanding, strongest last:
  1. rules text, read by the builder's own rules (tags.py, abilities.py, cardfn.py, strategy.py)
  2. Scryfall Tagger's community tags (data/tags.json, from fetch_tags.py): people labelled what each card does
  3. YOUR corrections (data/overrides.json, committed in the repo): always win

What the tags add, per concept:
  roles      ramp / draw / removal / board wipe / counterspell / tutor / recursion
  produces   what the card gives the deck (cardfn vocabulary): a sac outlet produces deaths, a token maker bodies...
  wants      what the card needs from the deck: a death payoff wants deaths, a landfall card wants lands entering...
  needs      game-plan needs (strategy.py) it fills: board protection, extra combats, damage doublers, haste, anthems

data/overrides.json format (all fields optional):
  {"Card Name": {"roles_add": ["removal"], "roles_remove": ["draw"], "produces": ["body"], "wants": ["creature_dies"],
                 "concepts_add": ["board_protect"], "staple": true, "never": true, "note": "why"}}
  staple: true = always treated as a staple (false = never);  never: true = the builder never picks it on its own
  (you can still force it with Must include)."""
import json
import os

ROLE_FROM = {"ramp": "ramp", "mana_rock": "ramp", "mana_dork": "ramp", "draw": "draw", "wheel": "draw", "removal": "removal",
             "board_wipe": "sweeper", "counterspell": "counter", "tutor": "tutor", "recursion": "recursion", "reanimate": "recursion",
             "board_protect": "protection"}
PRODUCES_FROM = {"sac_outlet": {"creature_dies", "sacrifice"}, "token_maker": {"body", "creature_enters", "token_enters"},
                 "treasure": {"token_enters", "artifact_enters", "artifact_fodder"}, "lifegain": {"gain_life"},
                 "drain": {"opponent_loses_life"}, "untapper": {"untap"}, "self_mill": {"to_graveyard"},
                 "blink": {"creature_enters"}, "clone": {"creature_enters", "body"}, "extra_combat": {"attacks", "combat_damage"},
                 "extra_land": {"land_enters"}, "reanimate": {"creature_enters", "leaves_graveyard", "body"},
                 "recursion": {"leaves_graveyard"}, "draw": {"draw"}}
WANTS_FROM = {"sac_outlet": {"body"}, "lifegain_payoff": {"gain_life"}, "death_payoff": {"creature_dies"},
              "etb_payoff": {"creature_enters"}, "landfall": {"land_enters"}, "reanimate": {"to_graveyard"},
              "token_doubler": {"token_enters"}, "counter_doubler": {"counters_put"}}
# strategy.py need id -> tag concepts that fill it
NEED_FROM = {"board_protect": ["board_protect"], "amplify": ["damage_amp"], "haste": ["haste"], "anthem": ["anthem"],
             "enter_engine": ["etb_payoff"], "retrigger": ["trigger_doubler"], "retrigger_attack": ["extra_combat"]}

_STATE = {"tags": None, "over": None, "dir": None}


def _load(data_dir):
    if _STATE["dir"] == data_dir:
        return
    _STATE["dir"] = data_dir
    try:
        t = json.load(open(os.path.join(data_dir, "tags.json"), encoding="utf-8"))
        _STATE["tags"] = {k.lower(): v for k, v in (t.get("cards") or {}).items()}
        _STATE["meta"] = dict(concepts=t.get("concepts") or {}, missing=t.get("missing") or [], date=t.get("date"))
    except (OSError, ValueError):
        _STATE["tags"], _STATE["meta"] = {}, None
    try:
        o = json.load(open(os.path.join(data_dir, "overrides.json"), encoding="utf-8"))
        _STATE["over"] = {k.lower(): v for k, v in o.items() if not k.startswith("_") and isinstance(v, dict)}
    except (OSError, ValueError):
        _STATE["over"] = {}


def status():
    """For the run summary: is the tag database loaded, and how many corrections are active."""
    m = _STATE.get("meta")
    return dict(tags_cards=len(_STATE["tags"] or {}), tags_date=m and m["date"], concepts=len((m or {}).get("concepts", {})),
                missing=(m or {}).get("missing", []), overrides=len(_STATE["over"] or {}))


def apply(c, data_dir="data"):
    """Attach tag concepts and overrides to one card (called by common.load_cards after the rules-based tags)."""
    _load(data_dir)
    names = [c["name"].lower()] + [f.strip().lower() for f in c["name"].split(" // ")]
    concepts = set()
    for n in names:
        concepts |= set(_STATE["tags"].get(n, []))
    o = {}
    for n in names:
        if n in _STATE["over"]:
            o = _STATE["over"][n]
            break
    concepts |= set(o.get("concepts_add") or [])
    c["otags"] = sorted(concepts)
    tags = set(c.get("tags") or [])
    if "land" not in tags:
        for k in concepts:
            if k in ROLE_FROM:
                tags.add(ROLE_FROM[k])
    tags |= set(o.get("roles_add") or [])
    tags -= set(o.get("roles_remove") or [])
    c["tags"] = sorted(tags) if isinstance(c.get("tags"), list) else tags
    prod, want = set(o.get("produces") or []), set(o.get("wants") or [])
    for k in concepts:
        prod |= PRODUCES_FROM.get(k, set())
        want |= WANTS_FROM.get(k, set())
    c["_k_produces"], c["_k_wants"] = sorted(prod), sorted(want)
    if "staple" in o:
        c["_staple"] = bool(o["staple"])
    if o.get("never"):
        c["_never"] = True
    if o.get("note"):
        c["_note"] = o["note"]
    return c
