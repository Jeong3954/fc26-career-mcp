# Accuracy rules (A1-A18, A5b)

Rewritten for the product. "The user's club" is the club the user manages (manager
career) or plays for (player career); "the user's player" is the controlled player in a
player career. The server applies most of these before data reaches you; the rest are
how you must phrase answers.

| Id | Rule |
|---|---|
| A1 | **Attribution by explicit key only.** A value is about the user only when the row carries the user's club `teamid`, a `playerid` in the verified squad set, the user's `userid`, or a `compobjid` of a competition the user plays in. Never infer ownership from an event name, a global count, row order or "the first row". |
| A2 | **Squad membership** = players linked to the user's club plus players the club loaned out (loan row + contract at the user's club). A contract row alone over-counts: the save keeps contract rows for players already sold. Youth academy players carry a pseudo team id, not the club id. |
| A3 | **Global tables** (players, teams, league links, growth, last-match history, events, loans, grudge/love, previous teams, suspensions) are filtered by A1/A2 before use and labelled "global" when shown unfiltered. |
| A4 | **Confidence.** "Verified" needs two independent sources that agree (live bridge vs save, or data vs what the user sees on screen). One source = "plausible" when internally consistent, "unverified" when stale, zero, placeholder or of unknown unit. |
| A5 | **Event names carry no payload.** They say that something happened somewhere in the game world (e.g. PLAYER_UNHAPPY_WITH_WAGE, count 9) and are never attributed to the user's club or a named player. |
| A5b | **Event payloads.** Some events come with a decoded payload (`event_payloads[]`): attribute only by the payload's own `teamid`/`playerid` (`scope: "ours"` when a squad playerid is present, otherwise `global`). Live Editor's event id-to-name table is wrong for FC 26 past id ~35: key by id + payload label; names are hints only. POTM / MOTM / TOTW have no payload (news only, not readable). |
| A6 | **Scores** are home-away in the fixture's own home/away order; read `home_id`/`away_id` before saying "we won". Big win/loss records use user score vs opponent score. |
| A7 | **Units.** Match ratings in the DB are integers 5-10 (`rating_int_5_10`); the game shows one decimal. Squad-ranking overall values are overall x10-ish (875 = OVR 85; 1023/20 are sentinels). Wages are weekly in game currency; budgets are game currency. |
| A8 | **Dates.** YYYYMMDD ints for contracts, big-win dates and rating dates (20080101 = unset); FIFA day counts since 1582-10-14 for birthdates, join dates, loan ends; growth/injury dates are day counts with an epoch near 1996-04-10 (+-1 day). The save's calendar table is placeholders: the game date comes from the live bridge only. |
| A9 | **Loans.** Loaned-out players (contract status 4, loan wage split 100) are still the user's players; their stats this season belong to the loan club's matches. Loaned-in players carry contract status 1/3/5 and no link row at the parent club. |
| A10 | **Youth.** Mid-season youth players have no real name (created-player name tables only) and the youth pseudo team id; a promoted youth player gets a contract at the user's club (role 5, Prospect) and a youth-history row. |
| A11 | **Freshness.** Save values are "as of the last save" (file time); live values are "as of the last heartbeat" and change every game day. Never mix the two in one sentence without saying which is which. Known stale in the save: board confidence, objectives, reputation and budgets (zeros), league links (previous season's final table), previous-season league goals, manager W/D/L history. |
| A12 | **League table group.** Standings are grouped by `compobjid`; use the group whose fixtures the user plays (the `compobjid` in `next_fixtures` / `league_table`), not the biggest group. If the group does not match, treat position/points as unverified. |
| A13 | **Names.** Real player names are not in the save; they come from the live bridge. A name-less id is reported as an id, never guessed. |
| A14 | **Competition groups.** One standings group = one `compobjid`; a competition can have several groups (cup group stages, split leagues). Report the `compobjid` with every table; never merge groups by name. |
| A15 | **Top lists** cover only competitions the user plays in plus European/international cups: a "top scorer" list is per `compobjid` and says nothing about other leagues. |
| A16 | **Awards.** Award rows are attributed by playerid/teamid + season; type codes stay raw until decoded on the Awards screen; an empty table before a month end is "no award yet", not "unreadable". |
| A17 | **Previous-season fields** (league goals, top-scorer flags, league links, last champion) are labelled "previous season" or "static" and never mixed with live stats. |
| A18 | **National rosters** from the save are "as of the last save"; a call-up is claimed only when the roster diff between two saves shows the player added. |
