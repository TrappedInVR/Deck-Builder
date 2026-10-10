"""Interaction simulator: play the deck many times, with its cards actually working together, and use the results
to fine-tune the deck during the build.

It is a text-only, simplified Commander game (not a rules engine). Each card is turned into a small model of what it
does (sim_model): mana, cards drawn, bodies and tokens, triggers on attacking or on creatures entering, extra combats,
trigger doublers, damage amplifiers, haste, board / commander protection, removal, anthems. The COMMANDER is modelled
the same way from its own text, including counting conditions ("X is the number of creatures you control with power
2 or less"), so the cards meet it in play.

The table: three opponents at 40 life, modelled as pressure, not decks:
  spot removal on your best piece (the commander first if it's the win condition), one board wipe around turns 5-9,
  sometimes a hate piece that switches your commander's triggers off until you remove it.
The pilot follows the guide: ramp first, commander on curve, widen, keep protection mana up once the board is worth
it, use removal on hate pieces, hold one-shot extra combats for a big swing.

Tuning (tune): starting from the built deck and the pool of candidates the synergy filters already approved, the
weakest cards in play are swapped for candidates that do the same jobs, and a swap is kept only if the deck kills
measurably faster or more reliably over the same set of shuffled games (common random numbers, so the comparison is
fair). A Bracket 3 guard rejects swaps that make turn-5-or-earlier kills common. Results are RELATIVE (version A vs
version B of this deck), not real win rates."""
import random
import re

import conditions as CN
import strategy as ST
import speed as SP
from analyze import num

OPP_LIFE = 40
MAX_TURN = 15
_W = dict(a=1, an=1, one=1, two=2, three=3, four=4, five=5, x=3, that=2)


def _count(w):
    w = (w or "a").lower()
    return int(w) if w.isdigit() else _W.get(w, 1)


def sim_model(c, conds, is_cmd=False):
    """What a card does in the simulator."""
    t = c.get("type_line") or ""
    x = c.get("text") or ""
    for n in sorted({c["name"], c["name"].split(",")[0], c["name"].split(" // ")[0]}, key=len, reverse=True):
        x = x.replace(n, "~")
    xl = x.lower()
    tags = set(c.get("tags") or [])
    m = dict(name=c["name"], cmc=int(c.get("cmc") or 0), land="land" in tags, creature="Creature" in t,
             instant=bool(re.search(r"\b(?:Instant)\b", t)) or "flash" in xl[:60],
             oneshot=bool(re.search(r"\b(?:Instant|Sorcery)\b", t)), power=num(c.get("power")) or 0)
    m["counts"] = m["creature"] and any(CN.judge(c, r)[0] == "meets" for r in conds if r["type"] in ("creature", "token")) \
        if conds else m["creature"]
    # mana
    m["mana_perm"] = m["mana_once"] = m["mana_turn"] = 0
    if "ramp" in tags:
        if SP._is_fast(c):
            mm = re.search(r"add ((?:\{[^}]+\})+)", x, re.I)
            made = mm.group(1).count("{") if mm else 1
            m["mana_perm" if not m["oneshot"] else "mana_once"] = made if not m["oneshot"] else made
        elif re.search(r"treasure", xl):
            if m["oneshot"]:
                m["mana_once"] = 2
            else:
                m["mana_turn"] = 1 if re.search(r"whenever|at the beginning", xl) else 0
                m["mana_once"] = 0 if m["mana_turn"] else 1
        elif re.search(r"onto the battlefield", xl) and re.search(r"lands?", xl):
            m["mana_perm"] = 2 if re.search(r"two (?:basic )?land", xl) else 1
        else:
            m["mana_perm"] = 1
    # cards
    m["draw_once"] = m["draw_turn"] = 0
    if "draw" in tags:
        d = re.search(r"draws? (a|an|one|two|three|four|x) cards?", xl)
        n = _count(d.group(1)) if d else 1
        if re.search(r"whenever|at the beginning of|\{t\}", xl) and not m["oneshot"]:
            m["draw_turn"] = 1
        else:
            m["draw_once"] = n
    # bodies and tokens
    m["tok_once"] = m["tok_turn"] = m["tok_attack"] = 0
    m["tok_power"] = 1
    for tm in CN._TOKEN_N.finditer(x):
        q = _count(tm.group(1))
        p = tm.group(2)
        m["tok_power"] = 3 if p.lower() == "x" else int(p)
        line = CN._line_of(x, tm.start()).lower()
        if re.search(r"whenever (?:~|you|a creature you control|one or more creatures you control)[^,]{0,30}attack", line):
            m["tok_attack"] += q
        elif re.search(r"whenever|at the beginning of|^[^:]{0,40}:", line):
            m["tok_turn"] += q
        else:
            m["tok_once"] += q
        break
    m["tok_counts"] = True
    for r in conds:
        if r["type"] in ("creature", "token") and r.get("stat") == "power":
            m["tok_counts"] = (m["tok_power"] <= r["n"]) if r["op"] == "le" else (m["tok_power"] >= r["n"])
        if r["kind"] == "keyword":
            m["tok_counts"] = False
    # triggers
    m["attack_drain"] = None                   # damage/life loss to EACH opponent when it (or you) attacks
    am = re.search(r"whenever (?:~|you attack|one or more creatures you control attack)[^.]{0,40}?(?:,|:)[^.]{0,30}"
                   r"(?:deals? (\d+|x) damage to each opponent|each opponent loses (\d+|x) life)", xl)
    if am:
        v = am.group(1) or am.group(2)
        m["attack_drain"] = "X" if v == "x" else int(v)
    m["enter_drain"] = m["enter_draw"] = 0
    if ST.meets(c, dict(patterns=[ST.ENTER_ENGINE, ST.TOKEN_ENGINE, ST.CAST_ENGINE], concepts=["etb_payoff"])):
        dm = re.search(r"(?:deals? (\d+) damage to each opponent|each opponent loses (\d+) life)", xl)
        if dm:
            m["enter_drain"] = int(dm.group(1) or dm.group(2))
        if re.search(r"\bdraw", xl):
            m["enter_draw"] = 1
    m["extra_combat"] = None
    if re.search(r"additional combat phase", xl) or "extra_combat" in (c.get("otags") or ()):
        m["extra_combat"] = "once" if m["oneshot"] else "turn"
    m["trig_double"] = bool(re.search(r"triggers? an additional time|copy target triggered ability", xl)) or \
        "trigger_doubler" in (c.get("otags") or ())
    m["amp_mult"], m["amp_add"] = 1, 0
    if ST.meets(c, dict(patterns=[ST.AMP_DAMAGE], concepts=["damage_amp"])):
        if "triple" in xl:
            m["amp_mult"] = 3
        elif re.search(r"double|twice", xl):
            m["amp_mult"] = 2
        pa = re.search(r"plus (\d+)", xl)
        if pa:
            m["amp_add"] = int(pa.group(1))
    if m["enter_draw"]:
        m["draw_turn"] = 0                     # it draws when bodies enter, not every turn
    m["haste"] = ST.meets(c, dict(patterns=[ST.HASTE], concepts=["haste"])) or "haste" in xl[:40]
    m["board_protect"] = ST.meets(c, dict(patterns=[ST.BOARD_PROTECT], concepts=["board_protect"]))
    m["cmd_protect"] = "protection" in tags and not m["board_protect"]
    m["removal"] = "removal" in tags
    m["evasive"] = m["creature"] and bool(re.search(r"(?:^|\n|, )(?:flying|menace|trample|shadow|fear|intimidate)\b|can't be blocked", xl))
    ov = re.search(r"creatures you control (?:gain trample and )?get \+(\d+|x)/\+(?:\d+|x)[^.]{0,40}until end of turn", xl)
    m["overrun"] = (3 if ov.group(1) == "x" else int(ov.group(1))) if ov and m["oneshot"] else 0
    an = re.search(r"creatures you control get \+(\d+)/\+\d+(?![^.]*until end of turn)", xl)
    m["anthem"] = int(an.group(1)) if an and not m["oneshot"] else 0
    m["is_cmd"] = is_cmd
    # a rough "does anything at all" flag for credit
    return m


def _counts_body(power, base_counts, conds, anthem):
    if not conds:
        return True
    for r in conds:
        if r["type"] in ("creature", "token") and r.get("stat") == "power":
            p = power + anthem
            return (p <= r["n"]) if r["op"] == "le" else (p >= r["n"])
    return base_counts


def play(models, cmd_m, lands, conds, rng, finisher=True):
    """One game. Returns (kill_turn, per-card credit dict)."""
    lib = [dict(land=True, name="Land", cmc=0)] * lands + models
    lib = lib[:]
    rng.shuffle(lib)
    hand, lib = lib[:7], lib[7:]
    nl = sum(1 for c in hand if c.get("land"))
    if nl < 2 or nl > 5:                       # one mulligan (draw 7, bottom 1)
        lib = lib + hand
        rng.shuffle(lib)
        hand, lib = lib[:7], lib[7:]
        hand.sort(key=lambda c: (c.get("land") and sum(1 for h in hand if h.get("land")) > 3, c.get("cmc", 0)))
        lib.append(hand.pop())
    credit = {}

    def cr(name, v):
        credit[name] = credit.get(name, 0) + v

    field, bodies = [], []                     # bodies: [power, counts, source, sick]
    lands_n = perm_mana = 0
    opp = [OPP_LIFE] * 3
    cmd_on, cmd_tax, cmd_sick, hate = False, 0, False, False
    wipe_turn = rng.randint(5, 9) if rng.random() < 0.75 else 99
    hate_turn = rng.randint(4, 10) if rng.random() < 0.35 else 99

    def amp(v):
        mult = 1
        add = 0
        for f in field:
            mult *= f["amp_mult"]
            add += f["amp_add"]
        return (v + add) * mult if v > 0 else 0

    def drain_all(v, src):
        d = amp(v)
        for i in range(3):
            opp[i] -= d
        cr(src, v * 3)
        if d > v:                              # the extra damage belongs to the amplifiers
            amps = [f for f in field if f["amp_mult"] > 1 or f["amp_add"]]
            for f in amps:
                cr(f["name"], (d - v) * 3 / len(amps))

    def anthem():
        return sum(f["anthem"] for f in field)

    def counted():
        a = anthem()
        n = sum(1 for b in bodies if _counts_body(b[0], b[1], conds, a))
        if cmd_on and _counts_body(cmd_m["power"], cmd_m["counts"], conds, a):
            n += 1
        return n

    def enter(n_bodies, power, counts, src, entered_turn, evasive=False):
        nonlocal_draw = 0
        for _ in range(n_bodies):
            bodies.append([power, counts, src, entered_turn, evasive])
            cr(src, 1.0 if _counts_body(power, counts, conds, anthem()) else 0.2)
            for f in field:
                if f["enter_drain"]:
                    drain_all(f["enter_drain"], f["name"])
                if f["enter_draw"] and nonlocal_draw < 3:
                    nonlocal_draw += 1
                    if lib:
                        hand.append(lib.pop(0))
                    cr(f["name"], 1.5)

    dmg10 = 0.0
    for turn in range(1, MAX_TURN + 1):
        if turn > 1 and lib:
            hand.append(lib.pop(0))
        for f in field:
            for _ in range(f["draw_turn"]):
                if lib:
                    hand.append(lib.pop(0))
                    cr(f["name"], 1.5)
            if f["tok_turn"]:
                enter(f["tok_turn"], f["tok_power"], f["tok_counts"], f["name"], turn)
        land = next((c for c in hand if c.get("land")), None)
        if land:
            hand.remove(land)
            lands_n += 1
        mana = lands_n + perm_mana + sum(f["mana_turn"] for f in field)
        # hold protection once the board is worth it
        prot = next((c for c in hand if c.get("board_protect") and c.get("instant")), None)
        reserve = prot["cmc"] if prot and (counted() >= 4) else 0
        # cast: ramp, then commander, then the rest by value
        def prio(c):
            if c["mana_perm"] or c["mana_once"] or c["mana_turn"]:
                return (0, c["cmc"])
            if c["extra_combat"] == "once" or c["board_protect"] or c["removal"] and c["oneshot"] or c["overrun"]:
                return (9, c["cmc"])               # held for the right moment
            return (2, -(c["tok_once"] + c["tok_turn"] * 2 + c["counts"] + c["enter_drain"] * 2 + c["enter_draw"]
                         + (c["amp_mult"] - 1) * 3 + c["amp_add"] + (2 if c["extra_combat"] else 0)), c["cmc"])
        spells = sorted((c for c in hand if not c.get("land")), key=prio)
        cast_cmd_done = False
        for c in spells:
            if not cmd_on and not cast_cmd_done and prio(c)[0] > 0 and mana - reserve >= cmd_m["cmc"] + cmd_tax:
                mana -= cmd_m["cmc"] + cmd_tax
                cmd_on, cmd_sick, cast_cmd_done = True, True, True
                field.append(cmd_m)
            if prio(c)[0] == 9:
                if c["removal"] and hate and mana >= c["cmc"]:
                    hand.remove(c); mana -= c["cmc"]; hate = False; cr(c["name"], 4)
                continue
            if mana - reserve < c["cmc"]:
                continue
            hand.remove(c)
            mana -= c["cmc"]
            if c["mana_once"]:
                mana += c["mana_once"]; cr(c["name"], c["mana_once"] * 0.7)
            if c["mana_perm"]:
                perm_mana += c["mana_perm"]; cr(c["name"], c["mana_perm"] * 2)
            for _ in range(c["draw_once"]):
                if lib:
                    hand.append(lib.pop(0))
            if c["draw_once"]:
                cr(c["name"], c["draw_once"] * 1.5)
            if c["removal"] and hate and c["oneshot"]:
                hate = False; cr(c["name"], 4)
            if not c["oneshot"]:
                field.append(c)
            if c["creature"]:
                enter(1, c["power"], c["counts"], c["name"], turn, c["evasive"])
            if c["tok_once"]:
                enter(c["tok_once"], c["tok_power"], c["tok_counts"], c["name"], turn)
        if not cmd_on and mana - reserve >= cmd_m["cmc"] + cmd_tax:
            mana -= cmd_m["cmc"] + cmd_tax
            cmd_on, cmd_sick = True, True
            field.append(cmd_m)
        # combat
        haste = any(f["haste"] for f in field) or cmd_m["haste"]
        combats = 1 + sum(1 for f in field if f["extra_combat"] == "turn")
        ec = next((c for c in hand if c.get("extra_combat") == "once" and c["cmc"] <= mana), None)
        if ec and cmd_on and (not cmd_sick or haste) and counted() >= 4:
            hand.remove(ec); mana -= ec["cmc"]; combats += 1; cr(ec["name"], 0)
            ec_used = ec["name"]
        else:
            ec_used = None
        mult_trig = 2 if any(f["trig_double"] for f in field) else 1
        for k in range(combats):
            attacking = cmd_on and (not cmd_sick or haste)
            for f in field:
                if f["tok_attack"] and attacking:
                    enter(f["tok_attack"], f["tok_power"], f["tok_counts"], f["name"], turn)
            if attacking and not hate and cmd_m["attack_drain"] is not None:
                for _ in range(mult_trig):
                    v = counted() if cmd_m["attack_drain"] == "X" else cmd_m["attack_drain"]
                    before = opp[0]
                    drain_all(v, cmd_m["name"])
                    if k > 0:
                        src = ec_used if (ec_used and k == combats - 1) else next(
                            (f["name"] for f in field if f["extra_combat"] == "turn"), None)
                        if src:
                            cr(src, (before - opp[0]) * 3)
                if mult_trig > 1:
                    cr(next(f["name"] for f in field if f["trig_double"]), 3)
            for f in field:
                if f["attack_drain"] is not None and attacking and f["attack_drain"] != "X" and not f["is_cmd"]:
                    drain_all(f["attack_drain"], f["name"])
            # combat damage from the team (abstract: blockers stop about half), at the lowest-life opponent
            atk = [b for b in bodies if b[3] < turn or haste]
            pump = 0
            ov = next((c for c in hand if c.get("overrun") and c["cmc"] <= mana), None) if k == combats - 1 else None
            if ov and len(atk) >= 4:
                hand.remove(ov); mana -= ov["cmc"]; pump = ov["overrun"]
                cr(ov["name"], pump * len(atk))
            a = anthem()
            power = sum(max(0, b[0] + a + pump) * (1.0 if (b[4] or pump) else 0.5) for b in atk)
            if attacking:
                power += cmd_m["power"] * (1.0 if cmd_m["evasive"] else 0.5)
            dmg = amp(int(power)) if power else 0
            if dmg:
                share = dmg / max(1, sum(max(0, b[0] + a + pump) for b in atk) + (cmd_m["power"] if attacking else 0))
                for b in atk:
                    cr(b[2], max(0, b[0] + a + pump) * share * 0.5)
            alive = [i for i in range(3) if opp[i] > 0]
            if alive and dmg:
                i = min(alive, key=lambda j: opp[j])
                opp[i] -= dmg
        if turn == 10:
            dmg10 = sum(OPP_LIFE - max(0, o) for o in opp) / (3 * OPP_LIFE)
        if all(o <= 0 for o in opp):
            return turn, credit, 1.0
        cmd_sick = False
        # the table answers
        if turn >= 3 and rng.random() < 0.18:
            target_cmd = cmd_on and finisher
            p = next((c for c in hand if c.get("cmd_protect") and c.get("instant") and c["cmc"] <= mana), None)
            if target_cmd:
                if p:
                    hand.remove(p); cr(p["name"], 5)
                else:
                    cmd_on, cmd_tax = False, cmd_tax + 2
                    if cmd_m in field:
                        field.remove(cmd_m)
            elif [f for f in field if not f["is_cmd"]]:
                victim = max((f for f in field if not f["is_cmd"]),
                             key=lambda f: f["enter_drain"] + f["amp_mult"] + f["draw_turn"] + f["tok_turn"])
                field.remove(victim)
        if turn == wipe_turn:
            p = next((c for c in hand if c.get("board_protect") and c.get("instant") and c["cmc"] <= mana), None)
            if p and (counted() >= 3 or cmd_on):
                hand.remove(p); cr(p["name"], 3 + counted())
            else:
                bodies.clear()
                field[:] = [f for f in field if not f["creature"]]
                if cmd_on:
                    cmd_on, cmd_tax = False, cmd_tax + 2
        if turn == hate_turn:
            hate = True
    return MAX_TURN + 1, credit, dmg10


def evaluate(models, cmd_m, lands, conds, seeds, finisher=True):
    """Mean kill turn (lower = better), share of turn<=5 kills, per-card credit and cast counts."""
    models = sorted(models, key=lambda m: m["name"])   # same deck -> same shuffles, whatever order it was built in
    total, fast, credit, d10 = 0, 0, {}, 0.0
    for s in seeds:
        k, cr, dm = play(models, cmd_m, lands, conds, random.Random(s), finisher)
        total += k
        d10 += dm
        fast += k <= 5
        for n, v in cr.items():
            credit[n] = credit.get(n, 0) + v
    n = len(seeds)
    return dict(kill=total / n, fast=fast / n, dmg10=d10 / n, credit={k: v / n for k, v in credit.items()})


def score(r):
    """Lower is better: average kill turn, plus a Bracket 3 guard against frequent turn-5-or-earlier kills."""
    # damage by turn 10 (share of the table's 120 life) separates decks that rarely finish inside 15 turns
    return r["kill"] - 2.0 * r["dmg10"] + (8 * max(0, r["fast"] - 0.05))


def kill_spread(models, cmd_m, lands, conds, seeds, finisher=True):
    models = sorted(models, key=lambda m: m["name"])
    ks = sorted(play(models, cmd_m, lands, conds, random.Random(s), finisher)[0] for s in seeds)
    d = [play(models, cmd_m, lands, conds, random.Random(s), finisher)[2] for s in seeds[:100]]
    q = lambda f: ks[min(len(ks) - 1, int(f * len(ks)))]
    return dict(median=q(0.5), best10=q(0.1), worst10=q(0.9), never=sum(1 for k in ks if k > MAX_TURN) / len(ks),
                dmg10=round(sum(d) / len(d), 2))
