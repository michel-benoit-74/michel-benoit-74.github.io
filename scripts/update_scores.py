#!/usr/bin/env python3
"""
WC 2026 live score updater.

Strategy:
  1. ESPN v2 standings  → authoritative base for group-stage stats (gp ≤ 3)
  2. ESPN scoreboard (date queries) → all completed knockout-stage games
  3. ESPN scoreboard (today) → live/just-finished games
  4. Merge all into team_stats; update group cards, stats cards, leaderboard → push if changed

Penalty shootouts: ESPN marks the 90+ET-minute score (tied) plus winner=True on the
winner. game_pts_gd() uses the winner flag to award 3/0 pts instead of 1/1.
"""

import json, re, subprocess, os, sys
import urllib.request
from datetime import datetime, timezone, date, timedelta
from zoneinfo import ZoneInfo
ET = ZoneInfo('America/New_York')  # handles EST/EDT automatically

# First day of WC 2026 knockout stage (Round of 32)
KNOCKOUT_START = date(2026, 6, 28)

# In GitHub Actions GITHUB_WORKSPACE points to the checkout; locally use the dev path
REPO      = os.environ.get('GITHUB_WORKSPACE',
            os.path.expanduser('~/dev/michel-benoit-74.github.io'))
HTML_FILE = os.path.join(REPO, 'index.html')
PAT_FILE  = os.path.expanduser('~/github_wc2026')  # only used for local runs

# ── Name mappings ──────────────────────────────────────────────────────────────

ESPN_TO_HTML = {
    'Bosnia-Herzegovina': 'Bosnia &amp; Herz.',
    'Türkiye':            'Turkey',
    'United States':      'USA',
    'Ivory Coast':        "Côte d'Ivoire",
    'Cape Verde':         'Cabo Verde',
}

HTML_TO_STATS = {
    'Brazil':             'Brasil',
    'Bosnia &amp; Herz.': 'Bosnia',
    'Congo DR':           'DR Congo',
}

# ── Pool ownership ─────────────────────────────────────────────────────────────

OWNER_TEAMS = {
    'je': ['Spain', 'Japan', 'Croatia', 'Algeria', 'Scotland', 'Curaçao'],
    'jb': ['France', 'USA', 'Uruguay', 'Bosnia &amp; Herz.', 'Ghana', 'Iraq'],
    'dp': ['Argentina', 'Colombia', 'Turkey', 'Sweden', 'Iran', 'Qatar'],
    'dg': ['Portugal', 'Mexico', 'Canada', "Côte d'Ivoire", 'Tunisia', 'Haiti'],
    'cb': ['England', 'Morocco', 'South Korea', 'Australia', 'Uzbekistan', 'Jordan'],
    'fr': ['Brazil', 'Switzerland', 'Ecuador', 'Czechia', 'Congo DR', 'Saudi Arabia'],
    'mt': ['Germany', 'Netherlands', 'Paraguay', 'Egypt', 'South Africa', 'Cabo Verde'],
    'sr': ['Belgium', 'Norway', 'Senegal', 'Austria', 'Panama', 'New Zealand'],
}

OWNER_DISPLAY = {
    'je': 'John &amp; Erik',
    'jb': 'Jocke &amp; Benu',
    'dp': 'Dave &amp; Pat',
    'dg': 'Dom &amp; Giac',
    'cb': 'Charles &amp; Bruno',
    'fr': 'Farhad &amp; Rol',
    'mt': 'Matt &amp; Tony',
    'sr': 'Scott &amp; Rob',
}

# CSS class used in <span class="team-pill sm CSS_CLASS"> for each team.
# Used when regenerating leaderboard pills.
TEAM_CSS = {
    'Spain':              'spain',
    'Japan':              'japan',
    'Croatia':            'croatia',
    'Algeria':            'algeria',
    'Scotland':           'scotland',
    'Curaçao':            'curacao',
    'France':             'france',
    'USA':                'usa',
    'Uruguay':            'uruguay',
    'Bosnia &amp; Herz.': 'bosnia',
    'Ghana':              'ghana',
    'Iraq':               'iraq',
    'Argentina':          'argentina',
    'Colombia':           'colombia',
    'Turkey':             'turkey',
    'Sweden':             'sweden',
    'Iran':               'iran',
    'Qatar':              'qatar',
    'Portugal':           'portugal',
    'Mexico':             'mexico',
    'Canada':             'canada',
    "Côte d'Ivoire":      'ivory',
    'Tunisia':            'tunisia',
    'Haiti':              'haiti',
    'England':            'england',
    'Morocco':            'morocco',
    'South Korea':        'southkorea',
    'Australia':          'australia',
    'Uzbekistan':         'uzbekistan',
    'Jordan':             'jordan',
    'Brazil':             'brasil',
    'Switzerland':        'switzerland',
    'Ecuador':            'ecuador',
    'Czechia':            'czechia',
    'Congo DR':           'drcongo',
    'Saudi Arabia':       'saudiarabia',
    'Germany':            'germany',
    'Netherlands':        'netherlands',
    'Paraguay':           'paraguay',
    'Egypt':              'egypt',
    'South Africa':       'southafrica',
    'Cabo Verde':         'caboverde',
    'Belgium':            'belgium',
    'Norway':             'norway',
    'Senegal':            'senegal',
    'Austria':            'austria',
    'Panama':             'panama',
    'New Zealand':        'newzealand',
}

# ── Knockout bracket definition ───────────────────────────────────────────────
# R32 entries have fixed 'team1'/'team2'.  Later rounds use 'src1'/'src2' to
# reference the match key whose winner fills that slot.  '3rd' uses losers.
BRACKET_MATCHES = {
    'r32-1':  {'round': 'R32',   'date': 'Jun 28', 'team1': 'South Africa',    'team2': 'Canada'},
    'r32-2':  {'round': 'R32',   'date': 'Jun 29', 'team1': 'Japan',            'team2': 'Brazil'},
    'r32-3':  {'round': 'R32',   'date': 'Jun 29', 'team1': 'Germany',          'team2': 'Paraguay'},
    'r32-4':  {'round': 'R32',   'date': 'Jun 30', 'team1': 'Netherlands',      'team2': 'Morocco'},
    'r32-5':  {'round': 'R32',   'date': 'Jun 30', 'team1': "Côte d'Ivoire",   'team2': 'Norway'},
    'r32-6':  {'round': 'R32',   'date': 'Jun 30', 'team1': 'France',           'team2': 'Sweden'},
    'r32-7':  {'round': 'R32',   'date': 'Jul 1',  'team1': 'Mexico',           'team2': 'Ecuador'},
    'r32-8':  {'round': 'R32',   'date': 'Jul 1',  'team1': 'England',          'team2': 'Congo DR'},
    'r32-9':  {'round': 'R32',   'date': 'Jul 1',  'team1': 'Belgium',          'team2': 'Senegal'},
    'r32-10': {'round': 'R32',   'date': 'Jul 2',  'team1': 'USA',              'team2': 'Bosnia &amp; Herz.'},
    'r32-11': {'round': 'R32',   'date': 'Jul 2',  'team1': 'Spain',            'team2': 'Austria'},
    'r32-12': {'round': 'R32',   'date': 'Jul 2',  'team1': 'Portugal',         'team2': 'Croatia'},
    'r32-13': {'round': 'R32',   'date': 'Jul 3',  'team1': 'Switzerland',      'team2': 'Algeria'},
    'r32-14': {'round': 'R32',   'date': 'Jul 3',  'team1': 'Australia',        'team2': 'Egypt'},
    'r32-15': {'round': 'R32',   'date': 'Jul 3',  'team1': 'Argentina',        'team2': 'Cabo Verde'},
    'r32-16': {'round': 'R32',   'date': 'Jul 4',  'team1': 'Colombia',         'team2': 'Ghana'},
    'r16-1':  {'round': 'R16',   'date': 'Jul 4',  'src1': 'r32-1',  'src2': 'r32-4'},
    'r16-2':  {'round': 'R16',   'date': 'Jul 4',  'src1': 'r32-3',  'src2': 'r32-5'},
    'r16-3':  {'round': 'R16',   'date': 'Jul 5',  'src1': 'r32-2',  'src2': 'r32-6'},
    'r16-4':  {'round': 'R16',   'date': 'Jul 6',  'src1': 'r32-7',  'src2': 'r32-8'},
    'r16-5':  {'round': 'R16',   'date': 'Jul 6',  'src1': 'r32-11', 'src2': 'r32-12'},
    'r16-6':  {'round': 'R16',   'date': 'Jul 7',  'src1': 'r32-9',  'src2': 'r32-10'},
    'r16-7':  {'round': 'R16',   'date': 'Jul 7',  'src1': 'r32-14', 'src2': 'r32-16'},
    'r16-8':  {'round': 'R16',   'date': 'Jul 7',  'src1': 'r32-13', 'src2': 'r32-15'},
    'qf-1':   {'round': 'QF',    'date': 'Jul 9',  'src1': 'r16-1',  'src2': 'r16-2'},
    'qf-2':   {'round': 'QF',    'date': 'Jul 10', 'src1': 'r16-5',  'src2': 'r16-6'},
    'qf-3':   {'round': 'QF',    'date': 'Jul 11', 'src1': 'r16-3',  'src2': 'r16-4'},
    'qf-4':   {'round': 'QF',    'date': 'Jul 12', 'src1': 'r16-7',  'src2': 'r16-8'},
    'sf-1':   {'round': 'SF',    'date': 'Jul 14', 'src1': 'qf-1',   'src2': 'qf-2'},
    'sf-2':   {'round': 'SF',    'date': 'Jul 15', 'src1': 'qf-3',   'src2': 'qf-4'},
    '3rd':    {'round': '3rd',   'date': 'Jul 18', 'src1': 'sf-1-l', 'src2': 'sf-2-l'},
    'final':  {'round': 'Final', 'date': 'Jul 19', 'src1': 'sf-1',   'src2': 'sf-2'},
}

# Path colour class for each match — left-border stripe colour shows which
# QF quadrant a match feeds into so readers can trace the full path.
#   a = QF-1 quadrant (blue)   b = QF-2 quadrant (red)
#   c = QF-3 quadrant (green)  d = QF-4 quadrant (orange)
#   sf1 / sf2 = semi-final halves   final = championship
MATCH_PATH = {
    'r32-1':'a',  'r32-4':'a',  'r32-3':'a',  'r32-5':'a',
    'r16-1':'a',  'r16-2':'a',  'qf-1':'a',
    'r32-11':'b', 'r32-12':'b', 'r32-9':'b',  'r32-10':'b',
    'r16-5':'b',  'r16-6':'b',  'qf-2':'b',
    'r32-2':'c',  'r32-6':'c',  'r32-7':'c',  'r32-8':'c',
    'r16-3':'c',  'r16-4':'c',  'qf-3':'c',
    'r32-13':'d', 'r32-15':'d', 'r32-14':'d', 'r32-16':'d',
    'r16-8':'d',  'r16-7':'d',  'qf-4':'d',
    'sf-1':'sf1', 'sf-2':'sf2',
    '3rd':'sf2',  'final':'final',
}

BRACKET_ROUNDS = [
    ('Round of 32',           ['r32-1','r32-2','r32-3','r32-4','r32-5','r32-6','r32-7','r32-8',
                               'r32-9','r32-10','r32-11','r32-12','r32-13','r32-14','r32-15','r32-16']),
    ('Round of 16',           ['r16-1','r16-2','r16-3','r16-4','r16-5','r16-6','r16-7','r16-8']),
    ('Quarterfinals',         ['qf-1','qf-2','qf-3','qf-4']),
    ('Semifinals',            ['sf-1','sf-2']),
    ('3rd Place &amp; Final', ['3rd','final']),
]

# ── Helpers ────────────────────────────────────────────────────────────────────

def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def gd_class(gd):
    return 'pos' if gd > 0 else ('neg' if gd < 0 else 'zero')

def gd_display(gd):
    return f'+{gd}' if gd > 0 else str(gd)

# ── ESPN data ──────────────────────────────────────────────────────────────────

def fetch_scoreboard():
    """Return today's scoreboard events."""
    return fetch_json(
        'https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard'
    ).get('events', [])

def fetch_knockout_history():
    """Return all COMPLETED (post) knockout-stage games from KNOCKOUT_START through yesterday.
    Today's games are handled separately by fetch_scoreboard() to allow live tracking."""
    games   = []
    today   = datetime.now(ET).date()
    d       = KNOCKOUT_START
    while d < today:
        ds = d.strftime('%Y%m%d')
        try:
            events    = fetch_json(
                f'https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard?dates={ds}'
            ).get('events', [])
            day_games = [g for g in parse_scoreboard_games(events) if g['state'] == 'post']
            games.extend(day_games)
        except Exception as e:
            print(f'fetch_knockout_history: error for {ds}: {e}', file=sys.stderr)
        d += timedelta(days=1)
    return games

def fetch_base_standings():
    """Return (stats_dict, eliminated_set).

    stats_dict  : html_name -> {pts, gd, gp, w, d, l}
    eliminated  : set of html_names whose group stage is complete (gp==3)
                  and who did NOT advance (advanced==0 per ESPN).
    """
    data = fetch_json('https://site.api.espn.com/apis/v2/sports/soccer/fifa.world/standings')
    result    = {}
    eliminated = set()
    for group in data.get('children', []):
        for entry in group.get('standings', {}).get('entries', []):
            espn = entry.get('team', {}).get('displayName', '')
            name = ESPN_TO_HTML.get(espn, espn)
            sv = {s['name']: int(s.get('value') or 0) for s in entry.get('stats', [])}
            result[name] = {
                'pts': sv.get('points', 0),
                'gd':  sv.get('pointDifferential', 0),
                'gp':  sv.get('gamesPlayed', 0),
                'w':   sv.get('wins', 0),
                'd':   sv.get('ties', 0),
                'l':   sv.get('losses', 0),
            }
            if sv.get('gamesPlayed', 0) >= 3 and sv.get('advanced', 1) == 0:
                eliminated.add(name)
    return result, eliminated

def parse_scoreboard_games(events):
    """
    Return list of dicts: {state, team1, score1, winner1, team2, score2, winner2}
    Only 'in' and 'post' games.  winner1/winner2 are True/False/None.
    For penalty shootouts ESPN keeps tied scores but marks the actual winner.
    """
    games = []
    for event in events:
        comp  = event.get('competitions', [{}])[0]
        state = comp.get('status', {}).get('type', {}).get('state', '')
        if state not in ('in', 'post'):
            continue
        comps = comp.get('competitors', [])
        if len(comps) < 2:
            continue
        # For STATUS_FINAL_PEN games ESPN's 'score' is the score at end of
        # extra time — penalty shootout goals are NOT included.  This means
        # GD is automatically based on regulation+ET goals only. ✓
        teams, scores, winners = [], [], []
        for c in sorted(comps, key=lambda x: x.get('order', 0)):
            espn = c['team']['displayName']
            teams.append(ESPN_TO_HTML.get(espn, espn))
            scores.append(int(c.get('score', 0) or 0))
            winners.append(c.get('winner'))   # True / False / None
        if len(teams) == 2:
            games.append({'state':   state,
                          'team1':   teams[0],  'score1':  scores[0], 'winner1': winners[0],
                          'team2':   teams[1],  'score2':  scores[1], 'winner2': winners[1]})
    return games

def game_pts_gd(score1, score2, winner1=None, winner2=None):
    """Return (pts1, gd1, pts2, gd2) treating current score as result.
    winner1/winner2 resolve ties caused by extra time + penalty shootouts.
    """
    gd1 = score1 - score2
    gd2 = score2 - score1
    if score1 > score2:
        pts1, pts2 = 3, 0
    elif score2 > score1:
        pts1, pts2 = 0, 3
    elif winner1 is True:          # tied after ET, team1 won on pens
        pts1, pts2 = 3, 0
    elif winner2 is True:          # tied after ET, team2 won on pens
        pts1, pts2 = 0, 3
    else:
        pts1, pts2 = 1, 1          # genuine draw (group stage)
    return pts1, gd1, pts2, gd2

def get_knockout_eliminated(all_games, base):
    """Return set of teams that lost a completed knockout-stage game.
    A game is knockout if both teams had base_gp >= 3 (i.e. both cleared the group stage).
    The loser of each such game is eliminated.
    """
    ko_eliminated = set()
    for g in all_games:
        if g['state'] != 'post':
            continue
        if base.get(g['team1'], {}).get('gp', 0) < 3 or \
           base.get(g['team2'], {}).get('gp', 0) < 3:
            continue  # group-stage game — skip
        s1, s2 = g['score1'], g['score2']
        w1, w2 = g.get('winner1'), g.get('winner2')
        if s1 > s2 or w1 is True:
            ko_eliminated.add(g['team2'])   # team2 lost
        elif s2 > s1 or w2 is True:
            ko_eliminated.add(g['team1'])   # team1 lost
    return ko_eliminated

def merge_standings(base, games):
    """
    Apply scoreboard game adjustments on top of base standings.

    Group-stage post games: skip if base standings already counted them
      (detected by base_gp >= scoreboard games for that team in group stage).
    Knockout post games: ALWAYS apply — ESPN standings only covers group stage,
      so any game where both teams have base_gp >= 3 is a new knockout game.
    Live (in) games: always applied provisionally.
    """
    import copy
    stats = copy.deepcopy(base)

    # Separate group-stage vs knockout games for dedup logic
    group_games    = [g for g in games if
                      base.get(g['team1'], {}).get('gp', 0) < 3
                      or base.get(g['team2'], {}).get('gp', 0) < 3]
    knockout_games = [g for g in games if
                      base.get(g['team1'], {}).get('gp', 0) >= 3
                      and base.get(g['team2'], {}).get('gp', 0) >= 3]

    # For group-stage dedup: count scoreboard games per team
    group_scoreboard_gp = {}
    for g in group_games:
        group_scoreboard_gp[g['team1']] = group_scoreboard_gp.get(g['team1'], 0) + 1
        group_scoreboard_gp[g['team2']] = group_scoreboard_gp.get(g['team2'], 0) + 1

    for g in group_games + knockout_games:
        t1, t2 = g['team1'], g['team2']
        s1, s2 = g['score1'], g['score2']
        w1, w2 = g.get('winner1'), g.get('winner2')
        pts1, gd1, pts2, gd2 = game_pts_gd(s1, s2, w1, w2)
        is_knockout = g in knockout_games

        for team, pts_adj, gd_adj in [(t1, pts1, gd1), (t2, pts2, gd2)]:
            if team not in stats:
                stats[team] = {'pts': 0, 'gd': 0, 'gp': 0, 'w': 0, 'd': 0, 'l': 0}

            # Group-stage post games: skip if standings already have them
            if g['state'] == 'post' and not is_knockout:
                if stats[team]['gp'] >= group_scoreboard_gp.get(team, 0):
                    continue

            stats[team]['pts'] += pts_adj
            stats[team]['gd']  += gd_adj
            stats[team]['gp']  += 1
            if pts_adj == 3:   stats[team]['w'] += 1
            elif pts_adj == 1: stats[team]['d'] += 1
            else:              stats[team]['l'] += 1

    return stats

# ── Bracket builder & renderer ────────────────────────────────────────────────

def build_bracket(all_games):
    """Resolve full bracket state from completed/live game results.

    Returns dict  {match_key: state_dict}  where each state_dict has:
        team1, team2  – HTML name or None (TBD)
        score1, score2 – int or None
        winner, loser  – HTML name or None
        pen            – True if decided on penalties
        state          – 'upcoming' | 'live' | 'completed'
        date           – display string, e.g. 'Jul 4'
    """
    game_lookup = {}
    for g in all_games:
        game_lookup[frozenset([g['team1'], g['team2']])] = g

    bstate = {}
    order  = (['r32-' + str(i) for i in range(1, 17)] +
              ['r16-' + str(i) for i in range(1, 9)] +
              ['qf-1','qf-2','qf-3','qf-4','sf-1','sf-2','3rd','final'])

    for key in order:
        m = BRACKET_MATCHES[key]

        # Resolve teams ────────────────────────────────────────────────────────
        if 'team1' in m:
            t1, t2 = m['team1'], m['team2']
        elif key == '3rd':
            t1 = bstate.get('sf-1', {}).get('loser')
            t2 = bstate.get('sf-2', {}).get('loser')
        else:
            t1 = bstate.get(m['src1'], {}).get('winner')
            t2 = bstate.get(m['src2'], {}).get('winner')

        ms = {'team1': t1, 'team2': t2, 'date': m['date'],
              'score1': None, 'score2': None,
              'winner': None, 'loser': None,
              'pen': False, 'state': 'upcoming'}

        # Look up result ───────────────────────────────────────────────────────
        if t1 and t2:
            gkey = frozenset([t1, t2])
            if gkey in game_lookup:
                g = game_lookup[gkey]
                if g['team1'] == t1:
                    s1, s2 = g['score1'], g['score2']
                    w1, w2 = g.get('winner1'), g.get('winner2')
                else:
                    s1, s2 = g['score2'], g['score1']
                    w1, w2 = g.get('winner2'), g.get('winner1')
                ms['score1'], ms['score2'] = s1, s2
                if g['state'] == 'post':
                    ms['state'] = 'completed'
                    # pen = tied score + winner flag (penalty shootout)
                    # Regular wins also set winner=True in ESPN data — NOT a pen
                    ms['pen'] = (s1 == s2 and (w1 is True or w2 is True))
                    if w1 is True:
                        ms['winner'], ms['loser'] = t1, t2
                    elif w2 is True:
                        ms['winner'], ms['loser'] = t2, t1
                    elif s1 > s2:
                        ms['winner'], ms['loser'] = t1, t2
                    elif s2 > s1:
                        ms['winner'], ms['loser'] = t2, t1
                elif g['state'] == 'in':
                    ms['state'] = 'live'

        bstate[key] = ms
    return bstate


def render_bracket_html(bstate):
    """Generate the full bracket section HTML."""

    def pill(team):
        if not team:
            return '<span class="team-pill sm tbd">TBD</span>'
        sn  = HTML_TO_STATS.get(team, team)
        css = TEAM_CSS.get(team,
              team.lower().replace(' ','').replace("'",'')
                          .replace('&amp;','').replace(';',''))
        return f'<span class="team-pill sm {css}">{sn}</span>'

    def render_match(key, ms):
        t1, t2   = ms['team1'], ms['team2']
        s1, s2   = ms['score1'], ms['score2']
        winner   = ms['winner']
        mstate   = ms['state']
        path_cls = f' bk-path-{MATCH_PATH.get(key, "")}'

        def row(team, score, is_winner, is_loser):
            cls = 'bk-team' + (' loser' if is_loser else ' winner' if is_winner else '')
            sc  = f'<span class="bk-score">{score}</span>' if score is not None else ''
            return f'<div class="{cls}">{pill(team)}{sc}</div>'

        t1_win  = (winner == t1 and t1 is not None)
        t1_lose = (winner is not None and winner != t1)
        t2_win  = (winner == t2 and t2 is not None)
        t2_lose = (winner is not None and winner != t2)
        sep     = ('<div class="bk-sep">&ndash;</div>'
                   if s1 is not None else '<div class="bk-sep">vs</div>')
        pen_ln  = '\n          <div class="bk-pen">pens</div>' if ms['pen'] else ''
        date_ln = ('<div class="bk-date live-badge">LIVE</div>'
                   if mstate == 'live'
                   else f'<div class="bk-date">{ms["date"]}</div>')

        return (f'<div class="bk-match {mstate}{path_cls}">\n'
                f'          {row(t1, s1, t1_win, t1_lose)}\n'
                f'          {sep}\n'
                f'          {row(t2, s2, t2_win, t2_lose)}{pen_ln}\n'
                f'          {date_ln}\n'
                f'        </div>')

    legend = (
        '    <div class="bk-legend">\n'
        '      <span class="bk-legend-item"><span class="bk-legend-swatch a"></span>QF·1 path</span>\n'
        '      <span class="bk-legend-item"><span class="bk-legend-swatch b"></span>QF·2 path</span>\n'
        '      <span class="bk-legend-item"><span class="bk-legend-swatch c"></span>QF·3 path</span>\n'
        '      <span class="bk-legend-item"><span class="bk-legend-swatch d"></span>QF·4 path</span>\n'
        '    </div>'
    )
    parts = [legend]
    for round_label, keys in BRACKET_ROUNDS:
        matches = '\n        '.join(render_match(k, bstate[k]) for k in keys)
        parts.append(
            f'    <div class="bracket-round">\n'
            f'      <div class="bracket-round-label">{round_label}</div>\n'
            f'      <div class="bk-grid">\n'
            f'        {matches}\n'
            f'      </div>\n'
            f'    </div>'
        )
    return '\n'.join(parts)


def update_bracket(html, all_games):
    """Replace content between <!-- BRACKET:START/END --> markers."""
    bstate  = build_bracket(all_games)
    content = render_bracket_html(bstate)
    pattern = r'<!-- BRACKET:START -->.*?<!-- BRACKET:END -->'
    repl    = f'<!-- BRACKET:START -->\n{content}\n  <!-- BRACKET:END -->'
    if not re.search(pattern, html, flags=re.DOTALL):
        print('[update_bracket] markers not found — bracket skipped', file=sys.stderr)
        return html
    return re.sub(pattern, repl, html, flags=re.DOTALL)


# ── HTML updaters ──────────────────────────────────────────────────────────────

def update_group_cards(html, team_stats, eliminated=None):
    if eliminated is None:
        eliminated = set()
    for html_name, stats in team_stats.items():
        pts, gd   = stats['pts'], stats['gd']
        is_elim   = html_name in eliminated

        # Capture the class attribute separately so we can add/remove 'eliminated'
        pattern = (
            r'<div class="(group-team[^"]*)">'
            r'(<span class="group-name-text">' + re.escape(html_name) + r'</span>'
            r'<span class="group-owner[^"]*">[^<]*</span>)'
            r'<span class="group-stat pts">[^<]*</span>'
            r'<span class="group-stat gd">[^<]*</span>'
            r'(</div>)'
        )

        def make_repl(p, g, elim):
            def repl(m):
                classes = m.group(1).replace(' eliminated', '')
                if elim:
                    classes += ' eliminated'
                return (f'<div class="{classes}">'
                        f'{m.group(2)}'
                        f'<span class="group-stat pts">{p}</span>'
                        f'<span class="group-stat gd">{gd_display(g)}</span>'
                        f'{m.group(3)}')
            return repl

        html = re.sub(pattern, make_repl(pts, gd, is_elim), html)
    return sort_group_cards(html)

def sort_group_cards(html):
    """Re-order team rows within each group card by pts desc, then gd desc."""
    def sort_teams(m):
        header   = m.group(1)
        rows_str = m.group(2)
        closing  = m.group(3)
        rows = re.findall(r'<div class="group-team[^"]*">.*?</div>', rows_str, re.DOTALL)

        def row_key(div):
            pts_m = re.search(r'<span class="group-stat pts">([^<]*)</span>', div)
            gd_m  = re.search(r'<span class="group-stat gd">([^<]*)</span>', div)
            try:    pts = int(pts_m.group(1)) if pts_m else 0
            except: pts = 0
            try:    gd  = int((gd_m.group(1) or '0').replace('+', '')) if gd_m else 0
            except: gd  = 0
            return (-pts, -gd)

        sorted_rows = sorted(rows, key=row_key)
        return (header
                + '\n        '.join(sorted_rows)
                + '\n      ' + closing)

    pattern = (
        r'(<div class="group-card-teams">\s*'
        r'<div class="group-standings-hdr">.*?</div>\s*)'   # header
        r'((?:<div class="group-team[^"]*">.*?</div>\s*)+)' # team rows
        r'(</div>)'                                          # closing tag
    )
    return re.sub(pattern, sort_teams, html, flags=re.DOTALL)

def update_stats_cards(html, team_stats, eliminated=None):
    for html_name, stats in team_stats.items():
        sname = HTML_TO_STATS.get(html_name, html_name)
        gp, w, d, l = stats['gp'], stats['w'], stats['d'], stats['l']
        gd, pts = stats['gd'], stats['pts']
        gdc, gds = gd_class(gd), gd_display(gd)
        is_elim   = html_name in (eliminated or set())
        tr_class  = ' class="eliminated"' if is_elim else ''

        # Match <tr> or <tr class="eliminated"> so toggling is idempotent
        pattern = (
            r'<tr(?:\s+class="[^"]*")?>'
            r'(<td class="tl"><span class="team-pill sm [^"]+">)'
            + re.escape(sname) +
            r'(</span></td>)<td>[^<]*</td><td>[^<]*</td><td>[^<]*</td><td>[^<]*</td>'
            r'<td class="[^"]*">[^<]*</td><td class="pts-n">[^<]*</td></tr>'
        )
        if gp == 0:
            repl = (f'<tr{tr_class}>' + r'\g<1>' + sname + r'\g<2>'
                    '<td>—</td><td>—</td><td>—</td><td>—</td>'
                    '<td class="zero">—</td><td class="pts-n">—</td></tr>')
        else:
            repl = (f'<tr{tr_class}>' + r'\g<1>' + sname + r'\g<2>'
                    f'<td>{gp}</td><td>{w}</td><td>{d}</td><td>{l}</td>'
                    f'<td class="{gdc}">{gds}</td><td class="pts-n">{pts}</td></tr>')
        html = re.sub(pattern, repl, html)
    return html

def update_leaderboard(html, team_stats, has_live, eliminated=None):
    # Compute owner totals
    owner_totals = {}
    for owner, teams in OWNER_TEAMS.items():
        t_pts = sum(team_stats.get(t, {}).get('pts', 0) for t in teams)
        t_gd  = sum(team_stats.get(t, {}).get('gd',  0) for t in teams)
        owner_totals[owner] = (t_pts, t_gd)

    # Update stats-card headers
    for owner, (t_pts, t_gd) in owner_totals.items():
        display = OWNER_DISPLAY[owner]
        gdc, gds = gd_class(t_gd), gd_display(t_gd)
        pattern = (
            r'(<div class="stats-card-hdr">\s*<span>' + re.escape(display) + r'</span>\s*'
            r'<div class="stats-totals">)'
            r'<span class="stats-pts">[^<]*</span>'
            r'<span class="stats-gd [^"]*">[^<]*</span>'
            r'(</div>\s*</div>)'
        )
        repl = (r'\1'
                f'<span class="stats-pts">{t_pts} pts</span>'
                f'<span class="stats-gd {gdc}">{gds}</span>'
                r'\2')
        html = re.sub(pattern, repl, html)

    # Extract, update, sort, and re-insert leaderboard rows
    tbody_pat = r'(<tbody>\s*)((?:<tr class="lb-row">.*?</tr>\s*){8})(</tbody>)'
    m = re.search(tbody_pat, html, re.DOTALL)
    if not m:
        print('[update_leaderboard] lb-rows not found', file=sys.stderr)
        return html

    rows = re.findall(r'<tr class="lb-row">.*?</tr>', m.group(2), re.DOTALL)
    updated = {}
    for row in rows:
        nm = re.search(r'<td class="lb-name">([^<]+)</td>', row)
        if not nm:
            continue
        owner = next((k for k, v in OWNER_DISPLAY.items() if v == nm.group(1)), None)
        if owner is None:
            continue
        t_pts, t_gd = owner_totals[owner]
        gdc, gds = gd_class(t_gd), gd_display(t_gd)
        row = re.sub(r'<td class="lb-pts">[^<]*</td>',
                     f'<td class="lb-pts">{t_pts}</td>', row)
        row = re.sub(r'<td class="lb-gd [^"]*">[^<]*</td>',
                     f'<td class="lb-gd {gdc}">{gds}</td>', row)
        # Re-order cells: rank | name | pts | gd | pills
        rank_td  = re.search(r'<td class="lb-rank">[^<]*</td>', row).group(0)
        name_td  = re.search(r'<td class="lb-name">[^<]*</td>', row).group(0)
        pts_td   = re.search(r'<td class="lb-pts">[^<]*</td>', row).group(0)
        gd_td    = re.search(r'<td class="lb-gd [^"]*">[^<]*</td>', row).group(0)
        # Regenerate pills from scratch (ensures text and eliminated class are always correct)
        pill_spans = []
        for team in OWNER_TEAMS[owner]:
            sname    = HTML_TO_STATS.get(team, team)
            css      = TEAM_CSS.get(team, team.lower().replace(' ', '').replace("'", ''))
            elim_cls = ' eliminated' if team in (eliminated or set()) else ''
            pill_spans.append(f'<span class="team-pill sm {css}{elim_cls}">{sname}</span>')
        pills_td = ('<td class="lb-pills">\n'
                    + ''.join(f'          {p}\n' for p in pill_spans)
                    + '        </td>')
        row = (f'      <tr class="lb-row">\n'
               f'        {rank_td}\n'
               f'        {name_td}\n'
               f'        {pts_td}\n'
               f'        {gd_td}\n'
               f'        {pills_td}\n'
               f'      </tr>')
        updated[owner] = row

    sorted_owners = sorted(updated, key=lambda o: (-owner_totals[o][0], -owner_totals[o][1]))
    new_rows = []
    for rank, owner in enumerate(sorted_owners, 1):
        row = re.sub(r'<td class="lb-rank">[^<]*</td>',
                     f'<td class="lb-rank">{rank}</td>', updated[owner])
        new_rows.append(row)

    new_tbody = m.group(1) + '\n      '.join(new_rows) + '\n    ' + m.group(3)
    html = html[:m.start()] + new_tbody + html[m.end():]

    # Update lb-note
    now_str  = datetime.now(ET).strftime('%-I:%M %p ET')
    live_tag = ' · 🔴 LIVE' if has_live else ''
    html = re.sub(
        r'<div class="lb-note">[^<]*</div>',
        f'<div class="lb-note">⚽ Live standings{live_tag} · last updated {now_str}</div>',
        html
    )
    return html

# ── Git push ───────────────────────────────────────────────────────────────────

def git_push(message):
    try:
        subprocess.run(['git', 'add', 'index.html'], cwd=REPO, check=True)
        r = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=REPO)
        if r.returncode == 0:
            print('No changes to commit.')
            return False
        subprocess.run(['git', 'commit', '-m', message], cwd=REPO, check=True)

        # GitHub Actions supplies GITHUB_TOKEN; local runs use the PAT file
        gh_token = os.environ.get('GITHUB_TOKEN')
        if gh_token:
            # Actions: use x-access-token with the built-in token
            remote = (f'https://x-access-token:{gh_token}@github.com/'
                      'michel-benoit-74/michel-benoit-74.github.io.git')
            subprocess.run(['git', 'remote', 'set-url', 'origin', remote], cwd=REPO, check=True)
            subprocess.run(['git', 'push', 'origin', 'main'], cwd=REPO, check=True)
        else:
            # Local: use PAT file, then restore SSH remote
            pat    = open(PAT_FILE).read().strip()
            remote = (f'https://michel-benoit-74:{pat}@github.com/'
                      'michel-benoit-74/michel-benoit-74.github.io.git')
            subprocess.run(['git', 'remote', 'set-url', 'origin', remote], cwd=REPO, check=True)
            subprocess.run(['git', 'push', 'origin', 'main'],              cwd=REPO, check=True)
            subprocess.run(['git', 'remote', 'set-url', 'origin',
                            'git@github.com:michel-benoit-74/michel-benoit-74.github.io.git'],
                           cwd=REPO, check=True)

        print(f'Pushed: {message}')
        return True
    except subprocess.CalledProcessError as e:
        print(f'Git error: {e}', file=sys.stderr)
        return False

# ── Main ───────────────────────────────────────────────────────────────────────

def git_pull():
    """Pull latest from remote before doing anything — prevents push conflicts
    when both local crontab and GitHub Actions run around the same time."""
    try:
        gh_token = os.environ.get('GITHUB_TOKEN')
        if gh_token:
            remote = (f'https://x-access-token:{gh_token}@github.com/'
                      'michel-benoit-74/michel-benoit-74.github.io.git')
        elif os.path.exists(PAT_FILE):
            pat = open(PAT_FILE).read().strip()
            remote = (f'https://michel-benoit-74:{pat}@github.com/'
                      'michel-benoit-74/michel-benoit-74.github.io.git')
        else:
            return  # no auth available, skip pull
        subprocess.run(['git', 'pull', '--rebase', remote, 'main'],
                       cwd=REPO, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f'git pull failed: {e}', file=sys.stderr)

def main():
    git_pull()  # always start fresh from remote

    base_standings, grp_eliminated = fetch_base_standings()

    # Knockout history: all completed games from Round of 32 start through yesterday
    knockout_history = fetch_knockout_history()
    if knockout_history:
        print(f'Knockout history: {len(knockout_history)} completed game(s)')
        for g in knockout_history:
            print(f'  {g["team1"]} {g["score1"]}-{g["score2"]} {g["team2"]}'
                  f'  (w1={g["winner1"]} w2={g["winner2"]})')

    # Today's live/finished games
    events      = fetch_scoreboard()
    today_games = parse_scoreboard_games(events)
    has_live    = any(g['state'] == 'in' for g in today_games)

    all_games = knockout_history + today_games

    # Combine group-stage and knockout eliminated sets
    ko_eliminated = get_knockout_eliminated(all_games, base_standings)
    eliminated    = grp_eliminated | ko_eliminated
    if grp_eliminated:
        print(f'Group-stage eliminated ({len(grp_eliminated)}): {", ".join(sorted(grp_eliminated))}')
    if ko_eliminated:
        print(f'Knockout eliminated ({len(ko_eliminated)}): {", ".join(sorted(ko_eliminated))}')

    if has_live:
        mode = 'LIVE'
    elif today_games:
        mode = 'post-match'
    else:
        mode = 'standings'

    if today_games:
        summaries = ', '.join(
            '{} {}-{} {} ({})'.format(g['team1'], g['score1'], g['score2'], g['team2'], g['state'])
            for g in today_games)
        print(f'[{mode}] today: {summaries}')
    else:
        print('No active games today.')

    team_stats = merge_standings(base_standings, all_games)

    with open(HTML_FILE) as f:
        html = f.read()

    html = update_bracket(html, all_games)
    html = update_group_cards(html, team_stats, eliminated)
    html = update_stats_cards(html, team_stats, eliminated)
    html = update_leaderboard(html, team_stats, has_live, eliminated)

    with open(HTML_FILE, 'w') as f:
        f.write(html)

    now_str = datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')
    git_push(f'Scores [{mode}]: {now_str}')

if __name__ == '__main__':
    main()
