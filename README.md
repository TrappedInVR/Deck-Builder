[README.md](https://github.com/user-attachments/files/33230457/README.md)
# MTG Commander deck builder ($0, runs on GitHub Actions)

Two workflows on the **Actions** tab (click one, then **Run workflow**):

1. **Suggest commanders**: pick vibe, colors and themes. A ranked list of fitting commanders appears on the run page.
2. **Build and check a deck**: builds a 100-card deck, runs a goldfish simulation and a bracket check, and shows a summary on the run page. The full files are in the `deck-report` artifact.

## The dropdowns
- **Deck vibe** (sets the bracket, Game Changer limit, tutors, extra turns, how mean or chaotic the deck is). Default: Competitive-Casual (Bracket 3).
- **Colors**: exact color identity (mono through five-color, plus colorless).
- **Mechanical theme**: counters, spellslinger, tokens, aristocrats, and more.
- **Narrative theme**: keyword-based flavor themes (creature types, card names, rules and flavor text). A soft nudge, not a guarantee.
- **Randomize**: a functional but weird deck. About 1 in 10 is tribal, and the tribe is the commander's own referenced type.

## Text boxes (all optional, blank by default)
Commander (overrides the commander and colors dropdowns; the vibe still applies), Tribe, Budget, Must include, Exclude, Avoid (e.g. infect, land destruction, stax), Seed (reproduce a random result).

## Notes
- GitHub forms can't show a live commander list, so use *Suggest commanders*, then type your pick into the Commander box.
- Blank tribe means tribal only when the commander's own text references its creature type.
- After editing `src/options.py`, run `python src/make_workflows.py` to refresh the workflows.
- Goldfish models mana only. The bracket report checks card-list rules; still check combos on Commander Spellbook.
