"""Synergy reference data: which cards REAL decks run with a commander (the closest legitimate thing to EDHREC).

Three sources, all used with their owners' permission or open licenses:
  1. EDHTop16 (edhtop16.com) public GraphQL API: tournament decklists for the commander. A card's play rate with
     this commander is discounted for universal staples (they show up everywhere, so they say little about synergy).
     Competitive data: it is a SIGNAL only; Bracket 3, Game Changer, combo and budget rules still decide.
     Only commanders that see tournament play have data. One or two polite requests per build, cached for a week.
  2. MTGJSON precon decklists (fetch_reference.py, weekly): cards Wizards' designers put with a commander.
  3. Your Reference box: cards you picked yourself (e.g. after browsing EDHREC in your own browser).
score(card) in 0..1 per source; build_deck adds REF_W * the best score to the card's synergy and says why.
The builder never contacts EDHREC."""
import json
import os
import re
import time
import urllib.request

API = "https://edhtop16.com/api/graphql"
UA = {"User-Agent": "DeckAutomation/0.3 (personal hobby project; edhtop16 public API)",
      "Content-Type": "application/json", "Accept": "application/json"}
CACHE_DIR = "data/edhtop16"
MAX_ENTRIES = 150

_INTROSPECT = """query { __schema { queryType { name } types { name kind fields(includeDeprecated: false) {
  name args { name type { kind name ofType { kind name ofType { kind name } } } }
  type { kind name ofType { kind name ofType { kind name ofType { kind name } } } } } } } }"""


def _post(query, variables=None, timeout=25):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(API, data=body, headers=UA, method="POST")
    d = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    if d.get("errors"):
        raise RuntimeError("; ".join(e.get("message", "?") for e in d["errors"])[:300])
    return d["data"]


def _base(t):
    """Unwrap NON_NULL / LIST to the named type; returns (name, is_list)."""
    is_list = False
    while t and t.get("kind") in ("NON_NULL", "LIST"):
        is_list = is_list or t["kind"] == "LIST"
        t = t.get("ofType")
    return (t or {}).get("name"), is_list


def _plan_query(schema):
    """Find, by looking at the live schema, a query that returns a commander's tournament decklists.
    Returns (query string, path description) or (None, reason)."""
    types = {t["name"]: t for t in schema["types"] if t.get("fields")}
    root = types.get(schema["queryType"]["name"]) or {}
    cmd_field = None
    for f in root.get("fields", []):
        tname, is_list = _base(f["type"])
        if not is_list and "commander" in f["name"].lower() and any(a["name"] == "name" for a in f["args"]) \
                and tname in types:
            cmd_field = (f, tname)
            break
    if not cmd_field:
        return None, "no 'commander(name: ...)' field found in the schema"
    f, ctype = cmd_field
    entries = next((x for x in types[ctype]["fields"] if "entr" in x["name"].lower()), None)
    if not entries:
        return None, f"type {ctype} has no entries field"
    etype, is_list = _base(entries["type"])
    edge_path = ""
    node_type = etype
    if etype in types and any(x["name"] == "edges" for x in types[etype]["fields"]):
        edges = next(x for x in types[etype]["fields"] if x["name"] == "edges")
        edge_t, _ = _base(edges["type"])
        node = next((x for x in types[edge_t]["fields"] if x["name"] == "node"), None)
        node_type, _ = _base(node["type"]) if node else (None, False)
        edge_path = "edges { node { %s } }"
    if node_type not in types:
        return None, f"can't read the entry type behind {ctype}.{entries['name']}"
    deck = None
    for name in ("maindeck", "mainDeck", "decklist", "cards", "deck"):
        deck = next((x for x in types[node_type]["fields"] if x["name"] == name), None)
        if deck:
            break
    if not deck:
        return None, f"entry type {node_type} has no maindeck/cards field"
    card_t, _ = _base(deck["type"])
    inner = deck["name"] + (" { name }" if card_t in types and any(x["name"] == "name" for x in types[card_t]["fields"]) else "")
    first = "(first: %d)" % MAX_ENTRIES if any(a["name"] == "first" for a in entries["args"]) else ""
    sel = (edge_path % inner) if edge_path else inner
    q = "query($n: String!) { %s(name: $n) { %s%s { %s } } }" % (f["name"], entries["name"], first, sel)
    return q, f"{f['name']}(name).{entries['name']}.{deck['name']}"


def _names(deck_value):
    out = []
    for x in deck_value or []:
        if isinstance(x, str):
            out.append(x)
        elif isinstance(x, dict) and x.get("name"):
            out.append(x["name"])
    return out


def edhtop16(commander, log=print):
    """{card name: play rate 0..1, '_decks': n} for a commander, or {} (never raises). Cached for 7 days."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", commander.lower()).strip("-")
    path = os.path.join(CACHE_DIR, slug + ".json")
    try:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < 7 * 86400:
            return json.load(open(path))
    except (OSError, ValueError):
        pass
    try:
        q, how = _plan_query(_post(_INTROSPECT)["__schema"])
        if not q:
            log(f"EDHTop16: {how}")
            return {}
        d = _post(q, {"n": commander})
        node = next(iter(d.values())) if d else None
        if not node:
            log(f"EDHTop16: no tournament data for {commander}")
            json.dump({}, open(path, "w"))
            return {}
        ent = next(iter(node.values()))
        if isinstance(ent, dict) and "edges" in ent:
            decks = [_names(next(iter((e.get("node") or {}).values()), None)) for e in ent["edges"]]
        else:
            decks = [_names(next(iter(e.values()), None)) for e in (ent or [])]
        decks = [dk for dk in decks if dk]
        if not decks:
            log(f"EDHTop16: {commander} has entries but no decklists")
            return {}
        rates = {}
        for dk in decks:
            for n in set(dk):
                rates[n] = rates.get(n, 0) + 1
        out = {n: round(v / len(decks), 3) for n, v in rates.items()}
        out["_decks"] = len(decks)
        json.dump(out, open(path, "w"))
        log(f"EDHTop16: {len(decks)} tournament decklists for {commander} (via {how})")
        return out
    except Exception as e:                      # the reference is a bonus: never break a build
        log(f"EDHTop16 unavailable: {e}")
        return {}


def load_precons(path="data/reference.json"):
    try:
        return json.load(open(path, encoding="utf-8")).get("precons", {})
    except (OSError, ValueError):
        return {}


class Reference:
    """Per-build reference scores for one commander."""

    def __init__(self, cmd, user_cards=(), data_dir="data", online=True, log=print):
        self.cmd = cmd["name"]
        self.top = edhtop16(self.cmd, log) if online else {}
        self.decks = self.top.pop("_decks", 0) if self.top else 0
        pre = load_precons(os.path.join(data_dir, "reference.json"))
        faces = {self.cmd.lower(), self.cmd.split(" // ")[0].lower()}
        self.precon, self.precon_names = set(), []
        for key, d in pre.items():
            if faces & {x.lower() for x in d.get("commanders") or [d.get("commander", "")]}:
                self.precon |= {n.lower() for n in d["cards"]}
                self.precon_names.append(d["name"])
        self.user = {n.lower() for n in user_cards}

    def score(self, c):
        """(0..1, reason) for one card."""
        n = c["name"].lower()
        best, why = 0.0, ""
        if n in self.user:
            best, why = 1.0, "on your reference list"
        rate = self.top.get(c["name"]) or self.top.get(c["name"].split(" // ")[0]) or 0
        if rate and self.decks >= 5:
            rank = c.get("rank") or 10 ** 6
            damp = 0.3 if rank <= 100 else 0.6 if rank <= 500 else 1.0      # universal staples say little about synergy
            s = min(1.0, rate * damp * 1.4)
            if s > best:
                best, why = s, f"in {round(100 * rate)}% of {self.decks} tournament decks with this commander (EDHTop16)"
        if n in self.precon and 0.5 > best:
            best, why = 0.5, f"in the official precon ({self.precon_names[0]})"
        return best, why

    def summary(self):
        return dict(edhtop16_decks=self.decks, precons=self.precon_names, user=len(self.user))
