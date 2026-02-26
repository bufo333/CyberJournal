import re, hashlib, math
from collections import Counter
from typing import List, Tuple, Dict, Optional

# --- Bitmask directions (topology always open in this generator) ---
N, S, W, E = 1, 2, 4, 8
ALL_OPEN = N | S | W | E  # 15

# -------------------------
# Deterministic PRNG helpers
# -------------------------
def _u32(x: bytes) -> int:
    return int.from_bytes(x[:4], 'big', signed=False)

def rand01(seed: int, *nums: int) -> float:
    """Deterministic float in [0,1) from seed and coordinates."""
    h = hashlib.sha256(f"{seed}|{','.join(map(str, nums))}".encode()).digest()
    return _u32(h) / 2**32

def text_seed(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:16], 16)

# -------------------------
# Simple value noise (hash-based, multi-octave)
# -------------------------
def noise(seed: int, x: float, y: float, scale: float = 12.0, octaves: int = 3, persistence: float = 0.5) -> float:
    """
    Hash-based, cheap 'value noise'. Not true Perlin, but good enough for maps.
    Returns value in [0,1).
    """
    def base(ix: int, iy: int) -> float:
        return rand01(seed, ix, iy)

    def lerp(a: float, b: float, t: float) -> float:
        return a + (b - a) * t

    def smooth(t: float) -> float:
        # classic fade curve for smoother interpolation
        return t * t * (3 - 2 * t)

    total = 0.0
    amp = 1.0
    freq = 1.0
    norm = 0.0
    for _ in range(octaves):
        sx = (x / scale) * freq
        sy = (y / scale) * freq
        x0, y0 = math.floor(sx), math.floor(sy)
        tx, ty = smooth(sx - x0), smooth(sy - y0)
        v00 = base(x0, y0)
        v10 = base(x0 + 1, y0)
        v01 = base(x0, y0 + 1)
        v11 = base(x0 + 1, y0 + 1)
        vx0 = lerp(v00, v10, tx)
        vx1 = lerp(v01, v11, tx)
        vxy = lerp(vx0, vx1, ty)
        total += vxy * amp
        norm += amp
        amp *= persistence
        freq *= 2.0
    return total / max(norm, 1e-9)

# -------------------------
# Terrain classification
# -------------------------
TILES = {
    "water": "~",
    "shore": ",",
    "field": ".",
    "forest": "T",
    "hill":   "^",
    "mount":  "A",
    "river":  "=",
    "road":   "#",
    "poi":    "*",
}
TERRAIN_COST = {
    "water": float("inf"),
    "shore": 2,
    "field": 1,
    "forest": 3,
    "hill":   4,
    "mount":  8,
    "river":  2,   # crossing along a river bed (optional)
}

def classify(elev: float, moist: float) -> str:
    """Map elevation & moisture to a terrain type."""
    if elev < 0.28:              # deep water
        return "water"
    if elev < 0.34:              # shore / swampy margin
        return "shore"
    if elev > 0.80:
        return "mount"
    if elev > 0.65:
        return "hill" if moist < 0.55 else "forest"
    # midlands
    if moist > 0.62:
        return "forest"
    return "field"

# -------------------------
# Text → POIs
# -------------------------
def top_keywords(text: str, k: int = 3) -> List[str]:
    words = re.findall(r"[A-Za-z']{3,}", text.lower())
    stop = set("""
        the and you for that with have this not but are was from your they his her she him our out were about would could should there their them into over under
        of to in on a an is it as by be or if at we i me my ours ours us he she this those these while when where which who whom whose than then than
    """.split())
    c = Counter(w for w in words if w not in stop and len(w) >= 4)
    return [w for w, _ in c.most_common(k)]

def place_symbol(seed: int, w: int, h: int, label: str) -> Tuple[int,int]:
    """Deterministic coordinate from label."""
    hsh = hashlib.sha256(f"{seed}|poi|{label}".encode()).digest()
    # avoid too-close-to-edge placements for visibility
    x = 1 + _u32(hsh[0:4]) % max(1, w-2)
    y = 1 + _u32(hsh[4:8]) % max(1, h-2)
    return x, y

# -------------------------
# River generation (downhill flow)
# -------------------------
def carve_river(grid_types: List[List[str]], elev_map: List[List[float]], seed: int, max_rivers: int = 2):
    h, w = len(grid_types), len(grid_types[0])
    # candidates: high elevation sources not on border
    candidates = [(elev_map[y][x], x, y)
                  for y in range(1, h-1) for x in range(1, w-1)
                  if elev_map[y][x] > 0.75]
    if not candidates:
        return
    # pick up to max_rivers sources deterministically
    candidates.sort(reverse=True)
    picks = []
    for i, (_, x, y) in enumerate(candidates[:8]):
        if len(picks) >= max_rivers: break
        # spread them deterministically
        if i % 3 == 0 or not picks:
            picks.append((x, y))
    # flow
    for sx, sy in picks:
        x, y = sx, sy
        seen = set()
        for _ in range(w*h):  # cap steps
            if (x, y) in seen: break
            seen.add((x, y))
            # stop at existing water
            if grid_types[y][x] == "water":
                break
            grid_types[y][x] = "river"
            # choose lowest neighbor (N,S,E,W) with deterministic tie-break
            best = None
            best_e = elev_map[y][x]
            for nx, ny in [(x, y-1), (x, y+1), (x-1, y), (x+1, y)]:
                if 0 <= nx < w and 0 <= ny < h:
                    e = elev_map[ny][nx]
                    if e < best_e or (abs(e-best_e) < 1e-9 and rand01(seed, x, y, nx, ny) < 0.5):
                        best_e, best = e, (nx, ny)
            if best is None:
                break
            x, y = best

# -------------------------
# Main: text -> terrain map
# -------------------------
def text_to_map(text: str, width: Optional[int] = None, height: Optional[int] = None):
    """
    Produce:
      - openings: 2D bitmask grid (topology; here fully open = 15)
      - types:    2D terrain strings
      - costs:    2D numeric movement costs
      - legend:   dict(symbol->meaning) and poi mapping
    Size auto-derives from text length unless specified.
    """
    seed = text_seed(text)
    words = max(1, len(re.findall(r"[A-Za-z']+", text)))
    # auto size from text length (tweak as desired)
    side = max(16, min(64, int(4 + math.sqrt(words) * 2.5)))
    w = width or side
    h = height or side

    openings = [[ALL_OPEN for _ in range(w)] for _ in range(h)]

    # elevation & moisture fields
    elev_map = [[0.0]*w for _ in range(h)]
    moist_map = [[0.0]*w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            # jitter scale based on seed so different texts "feel" different
            scale_e = 10.0 + (seed % 7)
            scale_m = 14.0 + (seed % 11)
            elev = noise(seed ^ 0xA57E, x, y, scale=scale_e, octaves=4, persistence=0.55)
            moist = noise(seed ^ 0xBEEF, x+1000, y-777, scale=scale_m, octaves=3, persistence=0.6)
            elev_map[y][x] = elev
            moist_map[y][x] = moist

    # classify tiles
    types = [[classify(elev_map[y][x], moist_map[y][x]) for x in range(w)] for y in range(h)]

    # carve rivers after classification
    carve_river(types, elev_map, seed, max_rivers=2)

    # assign costs from types
    costs = [[TERRAIN_COST.get(types[y][x], 1) for x in range(w)] for y in range(h)]

    # POIs from keywords
    kws = top_keywords(text, k=3)
    pois: Dict[Tuple[int,int], str] = {}
    for i, kw in enumerate(kws):
        px, py = place_symbol(seed, w, h, kw)
        pois[(px, py)] = kw
        types[py][px] = "poi"  # mark for rendering; cost stays as underlying terrain by choice

    # legend
    legend = {
        "tiles": {k: TILES[k] for k in TILES},
        "poi": pois,
        "seed": seed,
        "size": (w, h),
        "keywords": kws,
    }
    return openings, types, costs, legend

# -------------------------
# ASCII rendering
# -------------------------
def render_ascii(types: List[List[str]], legend: Dict) -> str:
    h, w = len(types), len(types[0])
    sym = {k: v for k, v in legend["tiles"].items()}
    out = []
    for y in range(h):
        row = []
        for x in range(w):
            t = types[y][x]
            row.append(sym.get(t, '?'))
        out.append("".join(row))
    # legend footer
    lines = ["\nLegend: ~ water  , shore  . field  T forest  ^ hill  A mountain  = river  * poi"]
    if legend["keywords"]:
        lines.append("POIs: " + ", ".join(f"{kw}" for kw in legend["keywords"]))
    lines.append(f"Seed: {legend['seed']}  Size: {legend['size'][0]}x{legend['size'][1]}")
    return "\n".join(out + lines)

# -------------------------
# Colored + UTF renderer
# -------------------------
# If you want Windows 10+ to show colors reliably:
#   pip install colorama
#   from colorama import init as colorama_init
#   colorama_init()

ANSI_RESET = "\x1b[0m"

def _fg(code: str) -> str:
    """Return ANSI color prefix; code can be '31' or '38;5;34' etc."""
    return f"\x1b[{code}m"

# 16-color palette (portable). You can swap to 256-color below if you like.
PALETTE = {
    "water":  "34",     # blue
    "shore":  "33",     # yellow
    "field":  "32",     # green
    "forest": "32;1",   # bright green
    "hill":   "35",     # magenta (no brown in 16-color; change if preferred)
    "mount":  "37;1",   # bright white
    "river":  "36",     # cyan
    "road":   "37",     # gray/white
    "poi":    "31;1",   # bright red
    "unknown":"37",
}

# If you prefer richer colors, replace PALETTE above with this 256-color palette:
# PALETTE = {
#     "water":  "38;5;27",   # deep blue
#     "shore":  "38;5;178",  # sand
#     "field":  "38;5;34",   # grass
#     "forest": "38;5;22",   # dark green
#     "hill":   "38;5;131",  # brownish
#     "mount":  "38;5;250",  # light gray
#     "river":  "38;5;39",   # bright cyan
#     "road":   "38;5;244",  # gray
#     "poi":    "38;5;196",  # red
#     "unknown":"38;5;250",
# }

SYMBOLS_ASCII = {
    "water": "~",
    "shore": ",",
    "field": ".",
    "forest":"T",
    "hill":  "^",
    "mount": "A",
    "river": "=",
    "road":  "#",
    "poi":   "*",
}

SYMBOLS_UTF = {
    "water": "≈",
    "shore": "·",
    "field": "·",
    "forest":"♣",   # you can switch to "🌲" if your font supports it
    "hill":  "▵",
    "mount": "▲",
    "river": "≋",
    "road":  "┄",
    "poi":   "✶",
}

def render_colored_map(
    types: list[list[str]],
    legend: dict | None = None,
    *,
    charset: str = "utf",      # "utf" or "ascii"
    color: bool = True,
    border: bool = True
) -> str:
    """
    Render the terrain grid with colors and optional UTF glyphs.
    - types: 2D list of terrain strings (water/shore/field/forest/hill/mount/river/road/poi)
    - legend: optional dict returned by your generator; used to show seed/keywords
    """
    h, w = len(types), len(types[0])
    sym = SYMBOLS_UTF if charset.lower() == "utf" else SYMBOLS_ASCII

    # border chars
    if charset.lower() == "utf":
        TL, TR, BL, BR, H, V, T = "┌", "┐", "└", "┘", "─", "│", "┼"
    else:
        TL, TR, BL, BR, H, V, T = "+", "+", "+", "+", "-", "|", "+"

    def paint(tile: str, ch: str) -> str:
        if not color:
            return ch
        code = PALETTE.get(tile, PALETTE["unknown"])
        return f"{_fg(code)}{ch}{ANSI_RESET}"

    lines = []

    # top border
    if border:
        lines.append(TL + (H * w) + TR)

    for y in range(h):
        row_chars = []
        for x in range(w):
            t = types[y][x]
            ch = sym.get(t, "?")
            row_chars.append(paint(t, ch))
        row = "".join(row_chars)
        if border:
            lines.append(f"{V}{row}{V}")
        else:
            lines.append(row)

    # bottom border
    if border:
        lines.append(BL + (H * w) + BR)

    # legend footer
    footer = []
    if legend:
        seed = legend.get("seed")
        size = legend.get("size")
        kws  = legend.get("keywords") or []
        footer.append("")
        footer.append(f"Seed: {seed}   Size: {size[0]}x{size[1]}")
        if kws:
            footer.append("POIs: " + ", ".join(kws))
        # colorized mini legend
        footer.append(
            "Legend: "
            + " ".join([
                paint("water", sym["water"]) + "=water",
                paint("shore", sym["shore"]) + "=shore",
                paint("field", sym["field"]) + "=field",
                paint("forest", sym["forest"]) + "=forest",
                paint("hill", sym["hill"])   + "=hill",
                paint("mount", sym["mount"]) + "=mount",
                paint("river", sym["river"]) + "=river",
                paint("poi", sym["poi"])     + "=poi",
            ])
        )
    return "\n".join(lines + footer)

# -------------------------
# Example (keep or delete)
# -------------------------
if __name__ == "__main__":
    # Using your existing generator:
    sample = (
        "Journal — The storm broke at dawn. Wet pines, muddy tracks, "
        "and a low river crossing. Camped near the hill, wrote by lantern."
        "and a low river crossing. Camped near the hill, wrote by lantern."
        "and a low river crossing. Camped near the hill, wrote by lantern."
        "and a low river crossing. Camped near the hill, wrote by lantern."
    )
    openings, types, costs, legend = text_to_map(sample)

    print(render_colored_map(types, legend, charset="utf", color=True, border=True))
    print()
    print(render_colored_map(types, legend, charset="ascii", color=True, border=True))

