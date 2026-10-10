"""Weekly download of MTGJSON's preconstructed deck lists (open data) -> data/reference.json.

Keeps only Commander precons: {deck file: {"name", "commander", "cards": [...]}}. Designers built these lists around
their commanders, so they're a clean, legitimate source of "these cards were meant to go together".
Never fails the workflow: on any problem it writes nothing and the builder works without precon data.
Data: MTGJSON (mtgjson.com; license terms at mtgjson.com/license)."""
import io
import json
import os
import sys
import tarfile
import urllib.request

URL = "https://mtgjson.com/api/v5/AllDeckFiles.tar.gz"
UA = {"User-Agent": "DeckAutomation/0.3 (personal hobby project; weekly precon refresh)"}


def main(out="data/reference.json"):
    try:
        raw = urllib.request.urlopen(urllib.request.Request(URL, headers=UA), timeout=300).read()
        tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
    except Exception as e:
        print("could not download MTGJSON decks:", e)
        return
    precons = {}
    for m in tf.getmembers():
        if not m.isfile() or not m.name.endswith(".json"):
            continue
        try:
            d = json.load(tf.extractfile(m)).get("data") or {}
        except Exception:
            continue
        if "commander" not in (d.get("type") or "").lower() or not d.get("commander"):
            continue
        cmds = [c.get("name") for c in d["commander"] if c.get("name")]
        cards = sorted({c.get("name") for c in (d.get("mainBoard") or []) + (d.get("commander") or []) if c.get("name")})
        if cmds and cards:
            precons[os.path.basename(m.name)] = dict(name=d.get("name"), commanders=cmds, cards=cards,
                                                     released=d.get("releaseDate"))
    if not precons:
        print("no Commander precons found in the MTGJSON file; nothing written")
        return
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    json.dump(dict(precons=precons, credit="Precon lists from MTGJSON (mtgjson.com)"), open(out, "w"))
    print(f"wrote {out}: {len(precons)} Commander precons")


if __name__ == "__main__":
    main(*(sys.argv[1:2]))
