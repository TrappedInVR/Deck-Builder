"""Context filter: is a keyword hit GENUINELY synergistic, or just a matching word?

The fast pass (regexes grouped by game plan) finds candidate hits. This module then reads the clause each hit sits
in and asks four questions, the way a player reading the card would:

  1. Is it negated?          "Your opponents CAN'T gain life"            -> not lifegain
  2. Whose stuff is it?      "TARGET PLAYER sacrifices a creature"       -> an edict (removal), not aristocrats fodder
                             "ITS CONTROLLER creates a 3/3 Beast token"  -> your opponent gets the token
                             "whenever a creature AN OPPONENT CONTROLS dies" -> not your creatures dying
  3. Is it a hate effect?    "Exile target card from A GRAVEYARD"         -> graveyard hate, not graveyard synergy
                             "Destroy all TOKENS"                         -> anti-tokens
  4. Is it symmetric?        "EACH PLAYER gains 2 life"                   -> half credit (everyone benefits)

Some plans WANT opponents as the subject (burn, milling opponents, politics, stealing): for those, an opponent subject
is the point and isn't penalized. Returns a weight: 1 = genuine, 0.5 = partial (symmetric/ambiguous), 0 = misleading."""
import re

OPP_OK = {"Burn / drain the table", "Mill your opponents", "Politics / goad / monarch", "Steal & copy opponents' stuff",
          "Poison / proliferate"}
SYMMETRIC_OK = {"Card draw engine / wheels", "Politics / goad / monarch", "Discard / madness"}

_NEG_BEFORE = re.compile(r"\b(?:can't|cannot|don't|doesn't|isn't|aren't|never|prevents?|no longer|rather than)\b[^.;]{0,28}$")
_NEG_AFTER = re.compile(r"^[^.;]{0,10}\b(?:can't|cannot|isn't|aren't)\b")
_OPP_SUBJ = re.compile(r"\b(?:each opponent|target opponent|an opponent|opponents|your opponents|defending player|its controller|"
                       r"its owner|that player|that creature's controller|that permanent's controller|the (?:owner|controller) of)\b")
_YOU_SUBJ = re.compile(r"\b(?:you|your)\b")
_EACH_PLAYER = re.compile(r"\b(?:each player|all players|target player|each other player)\b")
_OPP_OBJ = re.compile(r"^[^.;]{0,45}?\b(?:an opponent controls|your opponents control|you don't control|opponents control|"
                      r"an opponent owns|you don't own|defending player controls|of an opponent|an opponent's|opponent's)\b")
_OPP_IN_HIT = re.compile(r"\b(?:an opponent controls|your opponents control|you don't control|opponents control|an opponent owns|you don't own)\b")
_DESTROY_ALL = re.compile(r"\b(?:destroy|exile|sacrifice)\s+(?:all|each)\b[^.;]{0,40}$")
_GY_HATE = re.compile(r"\bexile\b[^.;]{0,60}\bgraveyards?\b|\bgraveyards?\b[^.;]{0,30}\bexile it instead|"
                      r"\bshuffles? (?:their|his or her|its owner's) graveyard")
_GY_OWN = re.compile(r"\byour graveyard\b|\byou may (?:cast|play)\b|\breturn\b|\bput\b[^.;]{0,40}\bonto the battlefield")

GRAVEYARD = "Graveyard / self-mill / reanimation"
HATEABLE = {"Tokens / go wide", "Artifacts", "Enchantments", "+1/+1 counters", "Lifegain"}


def clause_around(text, start, end):
    """The sentence/ability the match sits in, plus the match position inside it."""
    a = max(text.rfind(".", 0, start), text.rfind("\n", 0, start), text.rfind(";", 0, start)) + 1
    nxt = [i for i in (text.find(".", end), text.find("\n", end), text.find(";", end)) if i != -1]
    b = min(nxt) if nxt else len(text)
    return text[a:b], start - a, end - a


def judge(text, start, end, plan):
    """Weight for one keyword hit at text[start:end] for `plan`. Returns (weight, reason)."""
    clause, s, e = clause_around(text, start, end)
    cl = clause.lower()
    pre, post = cl[:s], cl[e:]
    if _NEG_BEFORE.search(pre) or _NEG_AFTER.search(post):
        return 0.0, "negated"
    if plan == GRAVEYARD and _GY_HATE.search(cl) and not _GY_OWN.search(cl):
        return 0.0, "graveyard hate"
    if plan in HATEABLE and _DESTROY_ALL.search(pre):
        return 0.0, "destroys/exiles them"
    hit = cl[s:e]
    if re.match(r"(?:you|your)\b", hit):            # "...and YOU gain 1 life": the subject is you
        return 1.0, ""
    opp_obj = bool(_OPP_OBJ.search(post)) or bool(_OPP_IN_HIT.search(hit))
    subj = None
    for m in _OPP_SUBJ.finditer(pre):
        subj = m
    opp_subj = subj is not None and not _YOU_SUBJ.search(pre[subj.end():])
    if opp_subj or opp_obj:
        return (1.0, "") if plan in OPP_OK else (0.0, "helps an opponent / affects their stuff")
    if _EACH_PLAYER.search(pre) and not _YOU_SUBJ.search(pre[_EACH_PLAYER.search(pre).end():]):
        return (1.0, "") if plan in SYMMETRIC_OK else (0.5, "symmetric (everyone gets it)")
    return 1.0, ""


def weigh_matches(rx, text, plan, cap=2):
    """Sum of context weights over DISTINCT matches of a compiled regex (capped). Also returns filtered reasons."""
    seen, total, filtered = set(), 0.0, []
    for m in rx.finditer(text):
        key = m.group(0).lower()
        if key in seen:
            continue
        seen.add(key)
        w, why = judge(text, m.start(), m.end(), plan)
        total += w
        if w < 1:
            filtered.append(f"'{m.group(0)}': {why}")
    return min(total, cap), filtered
