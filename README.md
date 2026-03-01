# CYBER//JOURNAL

A terminal-first encrypted personal journal with a retro TUI, blind-index search, procedural ASCII maps, and a living world simulation built from your journal entries — inspired by Dwarf Fortress.

![Login Screen](media/login.png)

---

## Features

### Journal
- **End-to-End Encryption** — titles, bodies, map previews, tags, mood, and weather all encrypted with AES-GCM
- **Blind Index Search** — search your entries without revealing plaintext keywords (HMAC-SHA256)
- **Procedural ASCII Map Previews** — deterministic terrain generated from your writing
- **Multiple Retro Themes** — VT220 Green, AS/400 Amber, Vector Neon
- **Single-file Local Database** — SQLite via aiosqlite, fully offline
- **TUI Interface** — built with Textual

### Organization
- **Favorites** — star entries for quick access
- **Tagging System** — encrypted tags with blind-index tag search (`tag:name`)
- **Multi-Notebook Support** — organize entries into separate notebooks
- **Entry Templates** — save and reuse entry templates
- **Date-Range Filtering** — filter entries by date range
- **Sorting & Pagination** — sort ascending/descending with paginated browse
- **Word Count Tracking** — automatic word count per entry
- **Calendar View** — month view with entry-count indicators per day
- **Auto-Save Drafts** — drafts saved every 30 seconds, restored on next session

### Mood & Weather
- **Mood Tracking** — select from happy, sad, neutral, anxious, energetic, calm
- **Weather Input** — optional weather notes per entry
- Mood data feeds directly into the world simulation (weather, events, NPC personality)

### Data Portability
- **Export** — decrypt and export entries to JSON or Markdown
- **Import** — import entries from JSON, automatically encrypted on ingest

### World Simulation
- **Persistent World** — every journal entry generates a terrain chunk placed on a growing world grid
- **Biome System** — 15 biomes (ocean, grassland, rainforest, alpine, desert, marsh, etc.) derived from terrain, elevation, and moisture
- **Entity Generation** — settlements, landmarks, ruins, shrines, and resources placed based on entry word count, mood, and biome
- **NPC Generation** — proper nouns extracted from entries become NPC names; personality derived from entry mood; roles assigned by biome
- **Event System** — mood-based and keyword-based world events (festivals, storms, discoveries, conflicts)
- **Weather System** — mood-driven weather with cellular automaton propagation across the map
- **Economy & Trade** — biome-based resource production; A* pathfinding for trade routes between settlements
- **Quest Generation** — template quests from entry keywords (explore, deliver, discover, protect, gather)
- **Discovery Mechanics** — hidden landmarks revealed when explored; "ancient texts" show excerpts from older entries
- **World History Timeline** — chronological event log grouped by era (Primordial Era through Age of Prosperity)
- **Exploration Mode** — arrow-key navigation, tile inspection, quest tracking, weather display

---

## Encryption Architecture

```
Password -> Scrypt(salt) -> KEK -> HKDF("wrap-key") -> wraps DEK (AES-GCM)
DEK -> HKDF("cyberjournal/enc-key") -> enc_key (encrypts entries)
DEK -> HKDF("cyberjournal/search-key") -> search_key (HMAC blind index tokens)
```

- Each encrypted field has its own random 12-byte nonce
- AAD = username for all AES-GCM operations
- Password change re-encrypts all entries atomically (single transaction)
- Password reset via security question wipes all entries (by design)
- Search uses HMAC-SHA256 blind indexing — only hashes stored, never plaintext keywords

---

## Procedural Map Preview

Every journal entry generates a **deterministic ASCII map** — same text always produces the same terrain.

![Entry View](media/entry.png)

### How it works

| Step | Description |
|------|-------------|
| 1 | Entry body is hashed to create a deterministic random seed |
| 2 | A noise function generates a 2D grid of terrain elevation and moisture |
| 3 | Elevation + moisture ranges map to terrain symbols and biomes |
| 4 | Frequently used meaningful words become **POIs (Points of Interest)** |
| 5 | The final ASCII map is encrypted and stored alongside the entry |

### Symbols

| Symbol | Terrain |
|--------|---------|
| `~` | Water |
| `.` | Grass / plains |
| `#` | Forest |
| `^` | Mountain |
| `*` | Point of interest |

---

## World Simulation

Each journal entry's map becomes a **chunk** in a persistent world. Chunks are placed on a meta-grid using keyword similarity — entries about similar topics are placed adjacent to each other.

### World Lifecycle

```
New Entry -> Generate Chunk -> Place on Grid -> Derive Biome
          -> Spawn Entity -> Generate NPCs -> Trigger Events
          -> Apply Weather -> Generate Quests -> Place Hidden Landmarks
```

### Eras

The world progresses through eras based on total entries:

| Entries | Era |
|---------|-----|
| 0-9 | Primordial Era |
| 10-24 | Dawn Age |
| 25-49 | Age of Exploration |
| 50-99 | Age of Growth |
| 100-199 | Age of Civilization |
| 200+ | Age of Prosperity |

### Exploration Controls

| Key | Action |
|-----|--------|
| Arrow keys | Move cursor |
| Enter | Inspect tile / view source entry |
| `q` | View active quests |
| `h` | View world history timeline |
| Escape | Return to journal |

---

## UI Overview

| Tab | Purpose |
|-----|---------|
| **Browse** | Paginated entry list with sort and notebook filter |
| **New Entry** | Write entries with mood, weather, and template support |
| **Search** | Blind-index keyword search and `tag:` prefix search |
| **Calendar** | Month view showing entries per day |
| **World** | Launch the world explorer |
| **Account** | Settings, password change, export/import, logout |

Additional screens:
- **View Entry** — full text + map preview, edit, delete, favorite, tag management
- **World Explorer** — arrow-key map navigation with tile info, weather, and quest tracking
- **World History** — scrollable chronological event timeline grouped by era

![Settings Screen](media/settings.png)

---

## Code Structure

```
cyberjournal/
  crypto.py       Key derivation, AES-GCM encryption, HMAC blind indexing
  db.py           Async SQLite schema, queries, migrations
  logic.py        Business logic: auth, entry CRUD, search, tags, notebooks,
                  templates, export/import, drafts, calendar, pagination
  map.py          Procedural terrain + POI map generator
  ui.py           Textual TUI screens, modals, and tab navigation
  theme.css       Three retro themes (VT220 Green, AS/400 Amber, Vector Neon)
  errors.py       Domain exception hierarchy

  world/
    world_db.py   Separate SQLite DB for world state persistence
    grid.py       Chunk placement via keyword similarity (Jaccard index)
    biomes.py     Terrain x elevation x moisture -> 15 biome types
    timeline.py   Era system, turn progression, world history
    renderer.py   Tile-based viewport rendering with entity symbols
    explorer.py   Arrow-key exploration screen + world history screen
    hooks.py      Bridge: journal entry events -> world simulation
    events.py     Mood and keyword-driven world events
    weather.py    Mood-driven weather with cellular automaton propagation
    economy.py    Biome resource production, A* trade route pathfinding
    npcs.py       NPC extraction from proper nouns, biome-based roles
    quests.py     Template quest generation, completion tracking

app.py            Entry point
```

---

## Install & Run

```bash
pip install -r requirements.txt
python app.py
```

### Development

```bash
# Install dev dependencies
pip install pytest pytest-asyncio pytest-cov ruff

# Run tests
pytest

# Run linter
ruff check .

# Run with custom DB location
CYBERJOURNAL_DB=/path/to/db.sqlite3 python app.py
```

Data is stored locally in SQLite files created on first run:
- `journal.sqlite3` — encrypted journal data
- `journal_world.sqlite3` — world simulation state

---

## Notes

- Everything remains offline and local
- No analytics or network calls
- Database may be safely backed up or synced (it remains encrypted at rest)
- World simulation hooks are non-fatal — journal functionality is never blocked by world errors
- All async operations use aiosqlite; all crypto is synchronous (CPU-bound)
- Password change is atomic (single transaction) — no risk of partial re-encryption
