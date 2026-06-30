# WC 2026 Fantasy Pool Site — Maintenance Reference

## Overview
Static GitHub Pages site at **https://michel-benoit-74.github.io**  
Repo: `~/dev/michel-benoit-74.github.io`  
Single HTML file: `index.html` — updated in-place by a Python script.

---

## Key Files
| File | Purpose |
|------|---------|
| `index.html` | The entire site (HTML + CSS + no JS framework) |
| `scripts/update_scores.py` | Fetches scores, updates HTML, pushes to GitHub |
| `.github/workflows/update_scores.yml` | GH Actions: runs every 5 min + self-triggers |

---

## Credentials / Auth
- **PAT location (local):** `~/github_wc2026` — read by script at runtime, NEVER hardcode
- **PAT used for:** local `git push` over HTTPS, and for triggering next workflow run via GitHub API
- **In GitHub Actions:** `GITHUB_TOKEN` (auto) for push; `DISPATCH_PAT` secret for self-triggering
- Remote URL must be HTTPS (not SSH) for PAT to work: `https://github.com/michel-benoit-74/michel-benoit-74.github.io.git`

---

## How the Script Works (`update_scores.py`)

### Data sources (all ESPN public APIs, no auth needed)
1. **Standings API** (group stage only, max gp=3):  
   `https://site.api.espn.com/apis/v2/sports/soccer/fifa.world/standings`  
   → `fetch_base_standings()` → returns `(base_standings, grp_eliminated)`  
   Eliminated detection: `gamesPlayed >= 3 AND advanced == 0`

2. **Scoreboard API** (date-specific, used for knockout history):  
   `https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard?dates=YYYYMMDD`  
   → `fetch_knockout_history()` queries from `KNOCKOUT_START` (2026-06-28) through yesterday

3. **Scoreboard API** (today, for live games):  
   Same URL without `dates=` param → `fetch_scoreboard()`

### Processing flow in `main()`
```
git pull --rebase
→ fetch_base_standings()        → base_standings, grp_eliminated
→ fetch_knockout_history()      → list of completed knockout games
→ fetch_scoreboard()            → today's games (live or finished)
→ parse_scoreboard_games()      → normalized game dicts
→ merge_standings()             → full team_stats (group + knockout)
→ get_knockout_eliminated()     → ko_eliminated set
→ eliminated = grp_eliminated | ko_eliminated
→ update HTML (group cards, stats cards, leaderboard)
→ git commit + push (if changed)
→ schedule next run (via sleep 270 + dispatch)
```

### Penalty shootouts
- ESPN `score` for `STATUS_FINAL_PEN` = **ET score only** (no penalty goals) → GD is already correct
- `winner=True/False` on each competitor determines which team gets 3 pts
- `game_pts_gd(score1, score2, winner1, winner2)` handles this:  
  tie score + `winner1=True` → pts1=3, pts2=0

### `merge_standings()` logic
Separates games into group-stage vs knockout by checking `base_standings[team]['gp'] >= 3`.  
Group games: checks scoreboard gp count to avoid double-counting with standings API.  
Knockout games: always applied additively (never in base standings).

---

## HTML Update Functions

| Function | What it updates |
|----------|----------------|
| `update_group_cards(html, team_stats, eliminated)` | pts/gd + sort by pts desc, gd desc + `eliminated` CSS class |
| `update_stats_cards(html, team_stats, eliminated)` | per-team rows in stats tables + `eliminated` class on `<tr>` |
| `update_leaderboard(html, team_stats, eliminated)` | pts/gd totals per owner + regenerates pill spans from `TEAM_CSS` |
| `sort_group_cards(html)` | called inside `update_group_cards`; sorts team divs by (-pts, -gd) |

---

## Name Mappings (critical — three separate dicts)

| Dict | Maps | Used for |
|------|------|---------|
| `ESPN_TO_HTML` | ESPN displayName → HTML name | Standings/scoreboard → internal |
| `HTML_TO_STATS` | HTML name → stats table short name | Brazil→Brasil, etc. |
| `TEAM_CSS` | HTML name → CSS class | Pill styling in leaderboard |

If a team's name doesn't match across these, it silently gets skipped. Check all three when adding/fixing teams.

---

## CSS Classes (in `index.html`)
```css
.group-team.eliminated      { opacity: 0.38; }
.stats-tbl tr.eliminated    { opacity: 0.38; }
.team-pill.eliminated       { opacity: 0.35; filter: grayscale(1); text-decoration: line-through; }
```

---

## GitHub Actions Workflow
File: `.github/workflows/update_scores.yml`

- Runs on schedule (`*/5 * * * *`) as backup
- Self-triggers: each run sleeps 270s then POSTs a `workflow_dispatch` to itself
- **Concurrency group** `score-updater` prevents runaway parallel chains (had 34+ simultaneous runs once)
- `cancel-in-progress: false` so a running job isn't killed mid-push

### If workflow gets stuck / piles up
```bash
# List runs
gh run list --workflow=update_scores.yml --limit 20

# Cancel all queued/in-progress
gh run list --workflow=update_scores.yml --status in_progress --json databaseId -q '.[].databaseId' | xargs -I{} gh run cancel {}
gh run list --workflow=update_scores.yml --status queued --json databaseId -q '.[].databaseId' | xargs -I{} gh run cancel {}
```

---

## Local Development / Manual Push
```bash
cd ~/dev/michel-benoit-74.github.io
python3 scripts/update_scores.py   # reads PAT from ~/github_wc2026 automatically
```

Script does `git pull --rebase` at start and `git push` at end if HTML changed.  
Commit message format: `Scores [standings]: 2026-06-30 10:21 ET`  
Or `Scores [live]: ...` when games are currently in progress.

---

## Common Issues & Fixes

### Stats card headers show 0 pts
Regex must allow `\s*` between tags (HTML has newlines):
```python
r'(<div class="stats-card-hdr">\s*<span>' + re.escape(display) + r'</span>\s*...'
```

### Teams not updating beyond group stage
ESPN standings API caps at gp=3. Knockout results come only from the scoreboard date queries.  
Check `KNOCKOUT_START` date if new knockout rounds aren't being fetched.

### Leaderboard pill text disappears
Pills must be **fully regenerated** (not regex-replaced) using `TEAM_CSS` dict.  
Never try to capture and re-emit `</span>` — the text content is between the tags, not in a group.

### Merge conflicts on push
Run `git pull --rebase` before making changes. If diverged badly:
```bash
git fetch origin
git reset --hard origin/main   # takes remote as authoritative
# then re-run the script
```

### New team not showing up / wrong CSS
Add to all three places: `OWNER_TEAMS`, `TEAM_CSS`, and `ESPN_TO_HTML` if ESPN name differs.

---

## Pool Owners & Teams
| Key | Owner | Teams (6 each) |
|-----|-------|----------------|
| `je` | JE | Spain, Japan, Croatia, Algeria, Scotland, Curaçao |
| `jb` | JB | France, USA, Uruguay, Bosnia & Herz., Ghana, Iraq |
| `mb` | MB | Argentina, Colombia, Turkey, Sweden, Iran, Qatar |
| `rb` | RB | Portugal, Mexico, Canada, Côte d'Ivoire, Tunisia, Haiti |
| `cb` | CB | England, Morocco, South Korea, Australia, Uzbekistan, Jordan |
| `pb` | PB | Brazil, Switzerland, Ecuador, Czechia, Congo DR, Saudi Arabia |
| `ab` | AB | Germany, Netherlands, Paraguay, Egypt, South Africa, Cabo Verde |
| `db` | DB | Belgium, Norway, Senegal, Austria, Panama, New Zealand |

---

## Upcoming Dates (WC 2026)
- Knockout stage began: **2026-06-28** (`KNOCKOUT_START` constant)
- Final: **2026-07-19**
- After the tournament ends, disable the workflow to stop unnecessary API calls
