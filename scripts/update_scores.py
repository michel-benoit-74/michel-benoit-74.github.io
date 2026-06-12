#!/usr/bin/env python3
"""
WC 2026 live score updater.

Strategy:
  1. ESPN v2 standings  → authoritative base for all fully-recorded games
  2. ESPN scoreboard    → today's live (state='in') and finished (state='post') scores
  3. For each scoreboard game:
       state='in'   → always add provisional pts/gd (standings never include live games)
       state='post' → add adjustment only if standings.gamesPlayed < games in scoreboard
                      (i.e. standings haven't caught up yet)
  4. Merge → update group cards, stats cards, leaderboard → commit & push if changed
"""

import json, re, subprocess, os, sys
import urllib.request
from datetime import datetime, timezone

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

def fetch_base_standings():
    """Return dict html_name -> {pts, gd, gp, w, d, l} from ESPN standings API."""
    data = fetch_json('https://site.api.espn.com/apis/v2/sports/soccer/fifa.world/standings')
    result = {}
    for group in data.get('children', []):
        for entry in group.get('standings', {}).get('entries', []):
            espn = entry.get('team', {}).get('displayName', '')
            name = ESPN_TO_HTML.get(espn, espn)
            sv = {s['name']: int(s.get('value', 0)) for s in entry.get('stats', [])}
            result[name] = {
                'pts': sv.get('points', 0),
                'gd':  sv.get('pointDifferential', 0),
                'gp':  sv.get('gamesPlayed', 0),
                'w':   sv.get('wins', 0),
                'd':   sv.get('ties', 0),
                'l':   sv.get('losses', 0),
            }
    return result

def parse_scoreboard_games(events):
    """
    Return list of dicts: {state, team1, team2, score1, score2}
    Only 'in' and 'post' games.
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
        teams, scores = [], []
        for c in sorted(comps, key=lambda x: x.get('order', 0)):
            espn = c['team']['displayName']
            teams.append(ESPN_TO_HTML.get(espn, espn))
            scores.append(int(c.get('score', 0) or 0))
        if len(teams) == 2:
            games.append({'state': state,
                          'team1': teams[0], 'score1': scores[0],
                          'team2': teams[1], 'score2': scores[1]})
    return games

def game_pts_gd(score1, score2):
    """Return (pts1, gd1, pts2, gd2) treating current score as result."""
    gd1 = score1 - score2
    gd2 = score2 - score1
    if score1 > score2:
        pts1, pts2 = 3, 0
    elif score2 > score1:
        pts1, pts2 = 0, 3
    else:
        pts1, pts2 = 1, 1
    return pts1, gd1, pts2, gd2

def merge_standings(base, games):
    """
    Apply scoreboard game adjustments on top of base standings.
    - 'in'   games: always applied as provisional
    - 'post' games: only applied if standings haven't caught up (gamesPlayed mismatch)
    """
    import copy
    stats = copy.deepcopy(base)

    # Count completed+live games per team from scoreboard
    scoreboard_gp = {}    # team -> count of post+in games today
    for g in games:
        scoreboard_gp[g['team1']] = scoreboard_gp.get(g['team1'], 0) + 1
        scoreboard_gp[g['team2']] = scoreboard_gp.get(g['team2'], 0) + 1

    for g in games:
        t1, t2 = g['team1'], g['team2']
        s1, s2 = g['score1'], g['score2']
        pts1, gd1, pts2, gd2 = game_pts_gd(s1, s2)

        for team, pts_adj, gd_adj, s_for, s_against in [
            (t1, pts1, gd1, s1, s2),
            (t2, pts2, gd2, s2, s1),
        ]:
            if team not in stats:
                stats[team] = {'pts': 0, 'gd': 0, 'gp': 0, 'w': 0, 'd': 0, 'l': 0}

            current_gp = stats[team]['gp']
            # For 'post' games: skip if standings already count this game
            if g['state'] == 'post' and current_gp >= scoreboard_gp.get(team, 0):
                continue

            stats[team]['pts'] += pts_adj
            stats[team]['gd']  += gd_adj
            stats[team]['gp']  += 1
            if pts_adj == 3:  stats[team]['w'] += 1
            elif pts_adj == 1: stats[team]['d'] += 1
            else:              stats[team]['l'] += 1

    return stats

# ── HTML updaters ──────────────────────────────────────────────────────────────

def update_group_cards(html, team_stats):
    for html_name, stats in team_stats.items():
        pts, gd = stats['pts'], stats['gd']
        pattern = (
            r'(<div class="group-team[^"]*">'
            r'<span class="group-name-text">' + re.escape(html_name) + r'</span>'
            r'<span class="group-owner[^"]*">[^<]*</span>)'
            r'<span class="group-stat pts">[^<]*</span>'
            r'<span class="group-stat gd">[^<]*</span>'
            r'(</div>)'
        )
        repl = (r'\1'
                f'<span class="group-stat pts">{pts}</span>'
                f'<span class="group-stat gd">{gd_display(gd)}</span>'
                r'\2')
        html = re.sub(pattern, repl, html)
    return html

def update_stats_cards(html, team_stats):
    for html_name, stats in team_stats.items():
        sname = HTML_TO_STATS.get(html_name, html_name)
        gp, w, d, l = stats['gp'], stats['w'], stats['d'], stats['l']
        gd, pts = stats['gd'], stats['pts']
        gdc, gds = gd_class(gd), gd_display(gd)

        pattern = (
            r'(<tr><td class="tl"><span class="team-pill sm [^"]+">)'
            + re.escape(sname) +
            r'(</span></td>)<td>[^<]*</td><td>[^<]*</td><td>[^<]*</td><td>[^<]*</td>'
            r'<td class="[^"]*">[^<]*</td><td class="pts-n">[^<]*</td></tr>'
        )
        if gp == 0:
            repl = (r'\g<1>' + sname + r'\g<2>'
                    '<td>—</td><td>—</td><td>—</td><td>—</td>'
                    '<td class="zero">—</td><td class="pts-n">—</td></tr>')
        else:
            repl = (r'\g<1>' + sname + r'\g<2>'
                    f'<td>{gp}</td><td>{w}</td><td>{d}</td><td>{l}</td>'
                    f'<td class="{gdc}">{gds}</td><td class="pts-n">{pts}</td></tr>')
        html = re.sub(pattern, repl, html)
    return html

def update_leaderboard(html, team_stats, has_live):
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
            r'(<div class="stats-card-hdr"><span>' + re.escape(display) + r'</span>\s*'
            r'<div class="stats-totals">)'
            r'<span class="stats-pts">[^<]*</span>'
            r'<span class="stats-gd [^"]*">[^<]*</span>'
            r'(</div></div>)'
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
        pills_td = re.search(r'<td class="lb-pills">.*?</td>', row, re.DOTALL).group(0)
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
    now_str  = datetime.now(timezone.utc).strftime('%-I:%M %p UTC')
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

def main():
    # Always fetch scoreboard to check for active or recently-finished games
    events = fetch_scoreboard()
    games  = parse_scoreboard_games(events)

    if not games:
        print('No active or recently finished WC matches. Exiting.')
        return

    has_live = any(g['state'] == 'in' for g in games)
    mode     = 'LIVE' if has_live else 'post-match'
    summaries = ', '.join(
        '{} {}-{} {} ({})'.format(g['team1'], g['score1'], g['score2'], g['team2'], g['state'])
        for g in games)
    print(f'[{mode}] {len(games)} game(s) to process: {summaries}')

    base_standings = fetch_base_standings()
    team_stats     = merge_standings(base_standings, games)

    with open(HTML_FILE) as f:
        html = f.read()

    html = update_group_cards(html, team_stats)
    html = update_stats_cards(html, team_stats)
    html = update_leaderboard(html, team_stats, has_live)

    with open(HTML_FILE, 'w') as f:
        f.write(html)

    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    git_push(f'Scores [{mode}]: {now_str}')

if __name__ == '__main__':
    main()
