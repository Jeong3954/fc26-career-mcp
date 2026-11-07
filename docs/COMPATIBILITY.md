# Compatibility

The bridge runs inside xAranaktu's **FC 26 Live Editor** (third party, not included).
Documented Live Editor API calls (squad, player attributes, season stats, names, events)
work on any Live Editor version that loads. The *memory reads* (standings, fixtures,
league table, transfers, offers, world fixtures and tables) depend on offsets that are
pinned per Live Editor version in the bridge's `SUPPORTED` table, so they run only on
verified versions, and (since 1.0.3) only when you opt in: create an empty file
`enable_memory` in the data folder (`Documents\FC26CareerMCP` by default). Without it every
version runs like the `partial` column below (no game crash risk from memory reads).

| Live Editor | FC 26 title update | Game exe version | Status | Notes |
|---|---|---|---|---|
| v26.3.5 | TU 1.6.3 | 1.0.139.20381 | **full** | verified; all 8 tools, memory reads after opt-in (`enable_memory`) |
| other versions | any | any | **partial** | documented-API data only; memory reads off for the session |

`fc26_status` and `status.json` report the effective `compat`, `memory_reads` and the
`reason` when reads were disabled.

## What works in `partial` mode

| Works | Does not work |
|---|---|
| user / team identity, game date | league table (`league_table`) |
| squad list, contracts, injuries, flags | last results / next fixtures from memory |
| player attributes, potential, ratings | transfers, offers, bids, player talks, targets |
| season stats, top lists from `GetPlayersStats` | world.json fixtures / tables |
| event names and decoded payloads | |
| save-file data (`fc26_save_data`) | |

## Unknown game update with a supported Live Editor version

When the game updates but Live Editor still loads, the bridge keeps `compat: "full"` only
while its **canary checks** pass before every memory walk (pointer validity, list sizes
<= 8000, vector sizes <= 4000, id ranges). A failed canary switches the session to
`partial` with a `reason`; nothing is written to the game at any point.

## After a game or Live Editor update

1. Wait for a Live Editor release whose notes say it works with the new FC 26 version.
2. Wait for a product release that adds that Live Editor version to the `SUPPORTED` table
   (see CHANGELOG.md), then run `python installer\install.py` again (or ask your AI to follow INSTALL_FOR_AI.md).
3. Or, at your own risk, edit the `SUPPORTED` table at the top of
   `bridge/fc26cm_bridge_autorun.lua` yourself (it is one line per version) and copy the
   file to `<Live Editor>\lua\autorun\`. Wrong offsets cannot modify the game, but they can
   produce nonsense data or crash the game session; keep a backup of your saves.

## Other requirements

- Windows 10/11, 64-bit.
- EA SPORTS FC 26 (PC) with an **offline** career (manager or player).
- Python 3.9+ (real CPython; the Microsoft Store stub does not count). INSTALL_FOR_AI.md tells
  the installing AI how to get one (winget or the embeddable zip) if it is missing.
- An AI client: any MCP client (stdio or Streamable HTTP), or any assistant that can read the
  REST API or pasted text (docs/CLIENTS.md).
