"""Download Commander Spellbook's combo database (free bulk file) and trim it to data/combos.json.

Source: https://commanderspellbook.com/ (bulk file documented at
https://github.com/SpaceCowMedia/commander-spellbook-backend/blob/master/docs/api.md). Their guidance: use the
bulk file rather than paging the API, fetch it on your own schedule, and credit them. The workflow caches it
for a day, so this runs at most once per day.

If the download fails, the deck builder still works; it just can't check or use combos (it says so)."""
import gzip
import json
import os
import sys
import urllib.request

URL = "https://json.commanderspellbook.com/variants.json.gz"
UA = {"User-Agent": "MTGDeckBuilderHobby/1.0 (personal GitHub Actions project; credits commanderspellbook.com)"}
MAX_CARDS = 5


def trim(v):
    """Keep only what the builder needs. Card names come from 'uses', generic requirements from 'requires'."""
    if (v.get("legalities") or {}).get("commander") is False or v.get("spoiler"):
        return None
    if v.get("status") not in (None, "OK", "E"):         # public statuses: OK and Example
        return None
    uses = v.get("uses") or []
    cards = [u["card"]["name"] for u in uses if (u.get("card") or {}).get("name")]
    if not cards or len(cards) > MAX_CARDS:
        return None
    templates = [r["template"]["name"] for r in (v.get("requires") or []) if (r.get("template") or {}).get("name")]
    feats = [p.get("feature") or {} for p in (v.get("produces") or [])]
    results = [f["name"] for f in feats if f.get("status") in ("S", "C")] or [f.get("name", "") for f in feats][:3]
    return dict(
        id=v.get("id"), cards=cards, templates=templates, results=results[:4],
        tag=v.get("bracketTag") or "R", pop=v.get("popularity") or 0, identity=v.get("identity") or "C",
        mv=v.get("manaValueNeeded"), relevant=any(f.get("status") == "S" for f in feats),
        commander=[u["card"]["name"] for u in uses if u.get("mustBeCommander") and (u.get("card") or {}).get("name")],
        prereq=bool(v.get("notablePrerequisites")))


def main(out="data/combos.json", src=None):
    if src:                                       # a local copy of variants.json(.gz), e.g. for testing
        raw = open(src, "rb").read()
    else:
        print("downloading", URL, flush=True)
        raw = urllib.request.urlopen(urllib.request.Request(URL, headers=UA), timeout=300).read()
    try:
        raw = gzip.decompress(raw)
    except OSError:
        pass
    doc = json.loads(raw.decode("utf-8"))
    variants = doc["variants"] if isinstance(doc, dict) else doc
    keep = [t for t in (trim(v) for v in variants) if t]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    json.dump(dict(timestamp=doc.get("timestamp") if isinstance(doc, dict) else None, source="commanderspellbook.com",
                   combos=keep), open(out, "w", encoding="utf-8"), separators=(",", ":"))
    tags = {}
    for k in keep:
        tags[k["tag"]] = tags.get(k["tag"], 0) + 1
    print(f"wrote {len(keep)} commander-legal combos to {out} (of {len(variants)}); bracket tags: {tags}")


if __name__ == "__main__":
    try:
        main(*sys.argv[1:])
    except Exception as e:                      # never fail the whole run over combo data
        print(f"WARNING: couldn't download combo data ({e}). Decks will be built without combo checks.")
