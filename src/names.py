"""Turn whatever was typed into the closest real name: commanders, cards (Must include / Exclude) and creature types.

How "closest" is judged (all case-, accent- and punctuation-insensitive):
  1. exact match                                    "meren of clan nel toth"
  2. the typed text starts the name, or starts its words in order
                                                    "korv" / "korvold" -> Korvold, Fae-Cursed King; "atraxa praet"
  3. otherwise a blend of
       word match    each typed word vs. the best-matching word in the name (spelling similarity per word)
       spelling      whole-string similarity, against the full name AND the short name before the comma
       length        names of a similar length to what was typed win ties
     with popularity (EDHREC rank) only as a tie-breaker.
Never fails: it always returns the best guess, how confident it is, and the runners-up for the run summary."""
import difflib
import re
import unicodedata


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[’'`]", "", s.lower())
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def _ratio(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def _score(q, qw, name_n, words, short_n, pop=0.0):
    if q == name_n:
        return 1000.0
    if q == short_n or name_n.startswith(q):
        # several names can share a start ("Kaalia ..."): the more popular commander wins, then the closer length
        return 900 + 1.5 * pop + 10 * len(q) / max(len(name_n), 1)
    # typed words are prefixes of the name's words, in order ("atraxa praet", "nel toth")
    i, ok = 0, True
    for w in qw:
        while i < len(words) and not words[i].startswith(w):
            i += 1
        if i == len(words):
            ok = False
            break
        i += 1
    if ok and qw:
        return 800 + 1.5 * pop + 10 * len(q) / max(len(name_n), 1)
    word_sim = sum(max((_ratio(w, x) for x in words), default=0) for w in qw) / max(len(qw), 1)
    spell = max(_ratio(q, name_n), _ratio(q, short_n))
    length = 1 - abs(len(q) - min(len(name_n), max(len(short_n), len(q)))) / max(len(name_n), len(q), 1)
    return 100 * (0.45 * word_sim + 0.35 * spell + 0.20 * max(length, 0))


class Resolver:
    def __init__(self, names, popularity=None):
        self.items = []
        for n in names:
            nn = norm(n)
            short = norm(n.split(",")[0].split(" // ")[0])
            self.items.append((n, nn, nn.split(), short))
        self.pop = popularity or {}

    def resolve(self, typed, n_alts=4):
        """Returns dict(name, typed, exact, confidence 0-100, alternatives)."""
        q = norm(typed)
        if not q:
            return None
        qw = q.split()
        scored = []
        for n, nn, words, short in self.items:
            if q[0] != nn[:1] and q[0] != short[:1] and len(q) > 3:
                # cheap pre-filter for long queries: allow if any typed word is close to any name word's start
                if not any(w[:2] == x[:2] for w in qw for x in words):
                    continue
            scored.append((_score(q, qw, nn, words, short, self.pop.get(n, 0)), self.pop.get(n, 0), n))
        if not scored:
            scored = [(_score(q, qw, nn, words, short, self.pop.get(n, 0)), self.pop.get(n, 0), n)
                      for n, nn, words, short in self.items]
        scored.sort(key=lambda r: (-r[0], -r[1], r[2]))
        best = scored[0]
        conf = 100 if best[0] >= 800 else round(min(best[0], 99))
        alts = [r[2] for r in scored[1:1 + n_alts] if r[0] >= best[0] - 15 or r[0] >= 800]
        if best[0] >= 800 and best[0] < 1000 and not alts:
            alts = []
        return dict(name=best[2], typed=typed, exact=best[0] >= 1000, confidence=conf, alternatives=alts)


def describe(r, what="commander"):
    """One line for the run summary."""
    if not r or r["exact"]:
        return ""
    alts = f" Other close matches: {'; '.join(r['alternatives'])}." if r["alternatives"] else ""
    sure = "" if r["confidence"] >= 70 else " (low confidence; check this is the one you meant)"
    return f"You typed {r['typed']!r} for the {what}; using **{r['name']}**{sure}.{alts}"
