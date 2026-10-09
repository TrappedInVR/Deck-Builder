"""Convert a 'N Card Name' deck list (first line = commander) to a Forge .dck file."""
import sys
from common import parse_deck
deck = parse_deck(sys.argv[1]); name = sys.argv[3] if len(sys.argv) > 3 else "MyDeck"
with open(sys.argv[2], "w", encoding="utf-8") as f:
    f.write(f"[metadata]\nName={name}\n[Commander]\n1 {deck[0][1]}\n[Main]\n")
    for n, c in deck[1:]:
        f.write(f"{n} {c}\n")
