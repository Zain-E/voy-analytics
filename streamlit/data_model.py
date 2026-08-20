"""
Data-model documentation for the Voy dashboard (the "Data model" tab).

Nothing on that tab is hand-drawn — the architecture diagram, the ERD and the
table summaries are all derived from the dbt project itself:

  • ``models/**/*.sql``      → lineage edges, read off the ref() / source() calls
  • ``models/**/*.yml``      → descriptions, columns, tests, declared relationships
  • ``dbt_project.yml``      → materialisation per layer
  • ``target/catalog.json``  → real column types + row counts (optional; written by
                               ``dbt docs generate``, everything degrades without it)

Same principle as keeping lineage in dbt docs rather than a static image: a
diagram generated from the code cannot drift from the code. The one thing dbt
does *not* model is the star-schema join graph — dbt only knows the FKs you
declare as ``relationships`` tests — so the handful of documented joins that
have no test are listed in ``JOIN_EDGES`` below, mirroring
``models/marts/context.md``.

Neither diagram is laid out in the browser: Mermaid cannot lay out a diagram inside a
Streamlit tab that is hidden on first paint (it has nothing to measure), and a CDN has
no business in the critical path of a dashboard that otherwise only needs BigQuery. So

  • the **lineage DAG** is Mermaid pre-rendered to ``assets/lineage.svg`` — a layered
    DAG genuinely wants an auto-layout engine. Regenerate it after changing a model
    (needs node for `npx`)::

        python streamlit/data_model.py

    The tab flags itself if the committed render no longer matches the project, so a
    forgotten regeneration is visible rather than silent.
  • the **ERD** is drawn directly as SVG by :func:`erd_svg`, on every render. It is a
    small, fixed star, so its layout is declared rather than solved — which is also
    the only way to get ``dim_customer`` in the middle, since every arrow points away
    from the hub and a layout engine therefore ranks it to one edge.

Either way the viewer inlines the SVG and adds zoom/pan in a few lines of vanilla JS,
so the tab works offline and renders whichever tab is open.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
import yaml

ASSETS = Path(__file__).resolve().parent / "assets"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "models"
CATALOG_PATH = PROJECT_ROOT / "target" / "catalog.json"
DBT_PROJECT = PROJECT_ROOT / "dbt_project.yml"

# Voy brand — mirrors the palette in app.py (kept local so this module stays
# importable on its own; app.py owns the dashboard chrome).
GREEN, OLIVE, TERRA, INK = "#0F3D2E", "#9BA84B", "#C4703E", "#09110A"
MUTED, SURFACE, GRID = "#6B6B63", "#FBFAF6", "#E8E7DE"

LAYERS = ("sources", "staging", "intermediate", "marts")
LAYER_TITLE = {
    "sources": "Raw sources · voy (read-only)",
    "staging": "Staging · views",
    "intermediate": "Intermediate · views",
    "marts": "Marts · tables",
}
# fill / stroke per layer, used by the flowchart classDefs
LAYER_STYLE = {
    "sources": ("#EFEDE4", "#A8A79C"),
    "staging": ("#E6EDDE", "#7D9A54"),
    "intermediate": ("#F7E3D1", TERRA),
    "marts": ("#D7E3D5", GREEN),
    "viz": ("#F1F4DC", OLIVE),
}
# The two orientation points from the README — drawn with a heavier stroke.
CORE_MODELS = ("int_customer_continuous_subscriptions", "fct_customer_per_month_snapshot")

# Documented star-schema joins that dbt does NOT carry as `relationships` tests
# (parent, child, key). The dim_subscription → dim_customer FK *is* declared in
# models/marts/_marts.yml and is picked up automatically — it is not repeated here.
# Source of truth for these: models/marts/context.md § "Star schema & join keys".
JOIN_EDGES = (
    # (parent, child, fk column, edge label)
    ("dim_customer", "fct_customer_per_month_snapshot", "customer_id", "customer_id"),
    ("dim_customer", "stg_voy__activity", "customer_id", "customer_id"),
    ("dim_customer", "int_customer_continuous_subscriptions", "customer_id",
     "customer_id · period grain"),
    ("dim_subscription", "stg_voy__activity", "subscription_id", "subscription_id · spell grain"),
    ("dim_subscription", "int_subscription_active_periods", "subscription_id",
     "subscription_id · period grain"),
)
# Reporting rollups: aggregates of a parent, not FK children — drawn dashed.
ROLLUP_EDGES = (
    ("fct_customer_per_month_snapshot", "viz_cohort_retention", "aggregated · cohort x tenure"),
    # The daily viz reads both merges: customers from the customer-grain one, the
    # informational subscription count from the subscription-grain one.
    ("int_customer_continuous_subscriptions", "viz_active_users_daily",
     "aggregated · active customers"),
    ("int_subscription_active_periods", "viz_active_users_daily",
     "aggregated · live subscriptions"),
)

_REF_RE = re.compile(r"\bref\(\s*['\"]([\w.]+)['\"]\s*\)")
_SRC_RE = re.compile(r"\bsource\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*\)")


# ------------------------------------------------------------------ parsing --
def _clean(text: str | None) -> str:
    """Collapse a folded-YAML description into one line."""
    return " ".join((text or "").split())


def _short(text: str | None, n: int = 54) -> str:
    """First sentence, truncated — for diagram labels."""
    full = _clean(text).replace('"', "'")
    first = full.split(". ")[0].rstrip(".")
    out = first if len(first) >= 30 else full          # don't cut at "e.g." and friends
    return f"{out[: n - 1]}…" if len(out) > n else out


def _test_names(raw) -> list[str]:
    """dbt test entries (str or single-key dict) → flat list of short names."""
    out: list[str] = []
    for t in raw or []:
        if isinstance(t, str):
            out.append(t)
        elif isinstance(t, dict):
            out.extend(k.split(".")[-1] for k in t)
    return out


def _relationship(raw) -> tuple[str, str] | None:
    """Pull (parent_model, parent_field) out of a `relationships` test, if any."""
    for t in raw or []:
        if isinstance(t, dict) and "relationships" in t:
            cfg = t["relationships"] or {}
            m = _REF_RE.search(str(cfg.get("to", "")))
            if m:
                return m.group(1), str(cfg.get("field", ""))
    return None


def _materializations() -> dict[str, str]:
    """Per-layer materialisation from dbt_project.yml (staging: view, marts: table…)."""
    cfg = yaml.safe_load(DBT_PROJECT.read_text()) or {}
    project = ((cfg.get("models") or {}).get(cfg.get("name", "")) or {})
    return {layer: (project.get(layer) or {}).get("+materialized", "table") for layer in LAYERS}


def _fingerprint() -> str:
    """Newest mtime across the dbt project files — edits bust the cache below."""
    files = [*MODELS_DIR.rglob("*.sql"), *MODELS_DIR.rglob("*.yml"), DBT_PROJECT, CATALOG_PATH]
    return str(max((f.stat().st_mtime_ns for f in files if f.exists()), default=0))


@st.cache_data(show_spinner=False)
def _parse(_fp: str) -> dict:
    """Parse the dbt project into {models, sources}. Keyed on _fp so it re-reads on edit."""
    models: dict[str, dict] = {}
    sources: dict[str, dict] = {}

    def _columns(node: dict) -> list[dict]:
        cols = []
        for c in node.get("columns") or []:
            raw = c.get("tests") or c.get("data_tests")
            cols.append({
                "name": c["name"],
                "description": _clean(c.get("description")),
                "tests": _test_names(raw),
                "relationship": _relationship(raw),
            })
        return cols

    # ---- schema YAML: descriptions, columns, tests --------------------------
    for path in sorted(MODELS_DIR.rglob("*.yml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for m in doc.get("models") or []:
            model_tests = m.get("tests") or m.get("data_tests") or []
            grain_cols: list[str] = []
            for t in model_tests:
                if isinstance(t, dict):
                    for k, v in t.items():
                        if k.endswith("unique_combination_of_columns"):
                            grain_cols = list((v or {}).get("combination_of_columns") or [])
            models[m["name"]] = {
                "name": m["name"],
                "description": _clean(m.get("description")),
                "columns": _columns(m),
                "tests": _test_names(model_tests),
                "grain_columns": grain_cols,
                "layer": "", "path": "", "materialized": "", "refs": [], "sources": [],
            }
        for s in doc.get("sources") or []:
            for t in s.get("tables") or []:
                sources[f"{s['name']}.{t['name']}"] = {
                    "name": t["name"],
                    "schema": s["name"],
                    "database": s.get("database", ""),
                    "description": _clean(t.get("description")),
                    "columns": _columns(t),
                    "tests": [],
                    "grain_columns": [],
                    "layer": "sources",
                    "path": str(path.relative_to(PROJECT_ROOT)),
                    "materialized": "source",
                    "refs": [], "sources": [],
                }

    # ---- model SQL: layer, materialisation, lineage edges -------------------
    mats = _materializations()
    for path in sorted(MODELS_DIR.rglob("*.sql")):
        sql = path.read_text()
        rec = models.setdefault(path.stem, {
            "name": path.stem, "description": "", "columns": [], "tests": [], "grain_columns": [],
        })
        rec.update(
            layer=path.parent.name,
            path=str(path.relative_to(PROJECT_ROOT)),
            materialized=mats.get(path.parent.name, "table"),
            refs=sorted(set(_REF_RE.findall(sql))),
            sources=sorted({f"{a}.{b}" for a, b in _SRC_RE.findall(sql)}),
        )

    # ---- catalog.json (optional): real column types + row counts ------------
    for rec in models.values():
        rec["types"], rec["row_count"] = {}, None
    if CATALOG_PATH.exists():
        try:
            cat = json.loads(CATALOG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            cat = {}
        for node in {**cat.get("nodes", {}), **cat.get("sources", {})}.values():
            meta = node.get("metadata", {})
            rec = models.get(meta.get("name")) or sources.get(f"{meta.get('schema')}.{meta.get('name')}")
            if rec is None:
                continue
            rec["types"] = {c: v.get("type", "") for c, v in (node.get("columns") or {}).items()}
            rec["row_count"] = ((node.get("stats") or {}).get("num_rows") or {}).get("value")
    for rec in sources.values():
        rec.setdefault("types", {})
        rec.setdefault("row_count", None)

    return {"models": models, "sources": sources}


def load_project() -> dict:
    return _parse(_fingerprint())


# ------------------------------------------------------------------ helpers --
# Name-based fallbacks, used only when target/catalog.json is absent (project not
# built). First match wins; anything unmatched falls through to string.
_TYPE_RULES = (
    ("prefix", ("is_", "has_"), "bool"),
    ("exact", ("day",), "date"),
    ("suffix", ("_date", "_month", "_start", "_end"), "date"),
    ("suffix", ("_retention", "_rate"), "float64"),
    ("contains", ("_since_",), "int64"),
    ("prefix", ("retained_", "num_"), "int64"),
    ("suffix", ("_count", "_days", "_index", "_size", "_id", "_32d",
                "_customers", "_subscriptions"), "int64"),
    ("exact", ("tenure",), "int64"),
)
_MATCHERS = {
    "prefix": lambda c, n: c.startswith(n),
    "suffix": lambda c, n: c.endswith(n),
    "exact": lambda c, n: c == n,
    "contains": lambda c, n: n in c,
}


def column_type(rec: dict, column: str) -> str:
    """Real BigQuery type from catalog.json when built; a name-based guess otherwise."""
    if rec.get("types", {}).get(column):
        return rec["types"][column].lower()
    c = column.lower()
    for kind, needles, typ in _TYPE_RULES:
        if any(_MATCHERS[kind](c, n) for n in needles):
            return typ
    return "string"


def grain(rec: dict) -> str:
    """Declared grain: composite-uniqueness test → column-level `unique` → blank."""
    if rec.get("grain_columns"):
        return " × ".join(rec["grain_columns"])
    for c in rec.get("columns", []):
        if "unique" in c["tests"]:
            return c["name"]
    return ""


def keys_for(rec: dict) -> dict[str, str]:
    """column -> 'PK' / 'FK', from the declared uniqueness and relationships tests."""
    pk = set(rec.get("grain_columns") or []) | {c["name"] for c in rec["columns"] if "unique" in c["tests"]}
    fk = {c["name"] for c in rec["columns"] if c["relationship"]}
    fk |= {col for _p, child, col, _lbl in JOIN_EDGES if child == rec["name"]}
    return {**{c: "PK" for c in pk}, **{c: "FK" for c in fk if c not in pk}}


def columns_table(rec: dict) -> list[dict]:
    """Display rows for one table: catalog column order when built, YAML order otherwise."""
    docs = {c["name"]: c for c in rec["columns"]}
    order = list(rec.get("types") or {}) or list(docs)
    order += [name for name in docs if name not in order]
    keys = keys_for(rec)
    return [{
        "Column": name,
        "Type": column_type(rec, name),
        "Key": keys.get(name, ""),
        "Tests": ", ".join(docs.get(name, {}).get("tests", [])),
        "Description": docs.get(name, {}).get("description", ""),
    } for name in order]


def test_count(rec: dict) -> int:
    return len(rec.get("tests", [])) + sum(len(c["tests"]) for c in rec.get("columns", []))


def stats() -> dict:
    p = load_project()
    models, srcs = p["models"], p["sources"]
    return {
        "sources": len(srcs),
        "models": len(models),
        "tests": sum(test_count(r) for r in models.values()),
        "layers": {layer: [r for r in models.values() if r["layer"] == layer] for layer in LAYERS},
    }


def _node_id(name: str) -> str:
    return "n_" + re.sub(r"\W", "_", name)


def _sort_key(rec: dict) -> tuple[int, str]:
    order = {"dim": 0, "int": 0, "stg": 0, "fct": 1, "viz": 2}
    return order.get(rec["name"][:3], 3), rec["name"]


# ------------------------------------------------------------------ mermaid --
def lineage_mermaid() -> str:
    """Layered architecture DAG, straight from the ref()/source() graph."""
    p = load_project()
    models, srcs = p["models"], p["sources"]
    out = ["flowchart LR"]

    out.append(f'  subgraph SRC["{LAYER_TITLE["sources"]}"]')
    out.append("    direction TB")
    for key, rec in sorted(srcs.items()):
        out.append(f'    {_node_id(key)}[("{rec["name"]}<br/><i>{_short(rec["description"], 40)}</i>")]')
    out.append("  end")

    for layer in ("staging", "intermediate", "marts"):
        members = sorted((r for r in models.values() if r["layer"] == layer), key=_sort_key)
        if not members:
            continue
        out.append(f'  subgraph {layer.upper()}["{LAYER_TITLE[layer]}"]')
        out.append("    direction TB")
        for rec in members:
            g = grain(rec)
            sub = f"1 row · {g}" if g else _short(rec["description"], 44)
            out.append(f'    {_node_id(rec["name"])}["{rec["name"]}<br/><i>{sub}</i>"]')
        out.append("  end")

    for rec in models.values():
        for src in rec["sources"]:
            out.append(f'  {_node_id(src)} --> {_node_id(rec["name"])}')
        for parent in rec["refs"]:
            out.append(f'  {_node_id(parent)} --> {_node_id(rec["name"])}')

    for layer, (fill, stroke) in LAYER_STYLE.items():
        out.append(f"  classDef {layer} fill:{fill},stroke:{stroke},color:{INK},rx:6,ry:6;")
    out.append(f"  classDef core stroke-width:3px,font-weight:bold;")
    for key in srcs:
        out.append(f"  class {_node_id(key)} sources;")
    for rec in models.values():
        cls = "viz" if rec["name"].startswith("viz_") else rec["layer"]
        out.append(f'  class {_node_id(rec["name"])} {cls};')
    for name in CORE_MODELS:
        if name in models:
            out.append(f"  class {_node_id(name)} core;")
    out.append(f"  linkStyle default stroke:{MUTED},stroke-width:1.4px;")
    return "\n".join(out)


# Star layout: dim_customer in the middle, everything that joins to it around the
# outside, on a 3x3 grid (col, row). Declared rather than solved — the schema is
# small and fixed, and a layout engine puts the hub at one edge because every arrow
# points away from it. Box *contents* still come from the dbt project.
#
# The two gaps-and-islands merges flank the hub and both land on viz_active_users_daily,
# which is what they actually feed. Cells are not interchangeable: _pick_sides routes
# orthogonally and only around OTHER boxes, so a moved entity can force an edge through
# a box or push a label off-canvas. Re-render after editing this.
ERD_GRID = {
    "stg_voy__activity": (0, 0),
    "fct_customer_per_month_snapshot": (1, 0),
    "viz_cohort_retention": (2, 0),
    "dim_customer": (1, 1),                       # the hub
    "int_customer_continuous_subscriptions": (2, 1),
    "dim_subscription": (0, 2),
    "int_subscription_active_periods": (1, 2),
    "viz_active_users_daily": (2, 2),
}
_ROW_H, _HEAD_H, _PAD, _CHAR = 20, 30, 12, 6.9     # px; _CHAR = width of one 11.5px monospace glyph
_COL_GAP, _RANK_GAP = 130, 90                      # space between grid columns / rows
_MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"


def _entity_box(rec: dict) -> dict:
    """Measure one entity: its rows, column widths and overall size."""
    rows = [(r["Type"], r["Column"], r["Key"]) for r in columns_table(rec)]
    w_type = max(len(r[0]) for r in rows) * _CHAR
    w_name = max(len(r[1]) for r in rows) * _CHAR
    w_key = 2 * _CHAR
    title_w = len(rec["name"]) * 7.4 + 2 * _PAD
    width = max(_PAD * 2 + w_type + 10 + w_name + 10 + w_key, title_w)
    layer = "viz" if rec["name"].startswith("viz_") else rec["layer"]
    return {
        "name": rec["name"], "rows": rows, "w_type": w_type, "w_name": w_name,
        "width": width, "height": _HEAD_H + len(rows) * _ROW_H + 6,
        "fill": LAYER_STYLE[layer][0], "stroke": LAYER_STYLE[layer][1],
    }


def _anchor(box: dict, side: str, slot: float) -> tuple[float, float]:
    """A point on one side of a box; slot 0.5 = centred, other values spread edges apart."""
    x, y, w, h = box["x"], box["y"], box["width"], box["height"]
    return {
        "left": (x, y + h * slot), "right": (x + w, y + h * slot),
        "top": (x + w * slot, y), "bottom": (x + w * slot, y + h),
    }[side]


def _side_options(a: tuple[int, int], b: tuple[int, int]) -> list[tuple[str, str]]:
    """Which side each end leaves from — sideways along a row, vertically up a column.
    A diagonal spoke can turn either way round, so both shapes are offered and the caller
    picks one that doesn't drive through a box on the way."""
    (ac, ar), (bc, br) = a, b
    if ar == br:
        return [("right", "left") if ac < bc else ("left", "right")]
    if ac == bc:
        return [("bottom", "top") if ar < br else ("top", "bottom")]
    return [("right" if bc > ac else "left", "bottom" if br < ar else "top"),   # across, then up
            ("top" if br < ar else "bottom", "right" if bc < ac else "left")]   # up, then across


def _elbow(p1: tuple[float, float], s1: str, p2: tuple[float, float], s2: str) -> list[tuple[float, float]]:
    """Orthogonal route between two anchors, so every end meets its box at a right angle."""
    (x1, y1), (x2, y2) = p1, p2
    horiz1, horiz2 = s1 in ("left", "right"), s2 in ("left", "right")
    if horiz1 and horiz2:
        mx = (x1 + x2) / 2
        return [(x1, y1), (mx, y1), (mx, y2), (x2, y2)]
    if not horiz1 and not horiz2:
        my = (y1 + y2) / 2
        return [(x1, y1), (x1, my), (x2, my), (x2, y2)]
    return [(x1, y1), (x2, y1), (x2, y2)] if horiz1 else [(x1, y1), (x1, y2), (x2, y2)]


def _foot(x: float, y: float, side: str, many: bool) -> str:
    """Crow's foot (many) or a double tick (exactly one), drawn just outside the box edge."""
    dx, dy = {"left": (-1, 0), "right": (1, 0), "top": (0, -1), "bottom": (0, 1)}[side]
    px, py = -dy, dx                                   # unit vector along the box edge
    def pt(along, across):
        return f"{x + dx * along + px * across:.1f},{y + dy * along + py * across:.1f}"
    if many:
        return (f'<path d="M{pt(0, 7)} L{pt(14, 0)} M{pt(0, -7)} L{pt(14, 0)} M{pt(0, 0)} L{pt(14, 0)}" '
                f'fill="none" stroke="{MUTED}" stroke-width="1.3"/>')
    return (f'<path d="M{pt(8, 6)} L{pt(8, -6)} M{pt(14, 6)} L{pt(14, -6)}" '
            f'fill="none" stroke="{MUTED}" stroke-width="1.3"/>')


def _along(pts: list[tuple[float, float]], frac: float) -> tuple[float, float, bool]:
    """A point `frac` of the way along the whole route, and whether its leg runs horizontally."""
    segs = list(zip(pts, pts[1:]))
    total = sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in segs)
    walked = 0.0
    for (ax, ay), (bx, by) in segs:
        length = abs(ax - bx) + abs(ay - by)
        if walked + length >= total * frac:
            t = (total * frac - walked) / (length or 1)
            return ax + (bx - ax) * t, ay + (by - ay) * t, abs(ax - bx) >= abs(ay - by)
        walked += length
    return pts[-1][0], pts[-1][1], True


def _sides_of(x: float, y: float, horiz: bool) -> list[tuple[float, float, str]]:
    """Both placements for a label on that leg — above/below, or right/left of it."""
    if horiz:
        return [(x, y - 7, "middle"), (x, y + 15, "middle")]
    return [(x + 9, y + 4, "start"), (x - 9, y + 4, "end")]


def _rect(x: float, y: float, anchor: str, text: str) -> tuple[float, float, float, float]:
    w = len(text) * 6.6 + 6
    x0 = {"middle": x - w / 2, "start": x, "end": x - w}[anchor]
    return (x0, y - 12, x0 + w, y + 4)


def _hits(r: tuple[float, float, float, float], others) -> bool:
    return any(r[0] < o[2] and o[0] < r[2] and r[1] < o[3] and o[1] < r[3] for o in others)


def _place_label(pts, text: str, taken: list, blocked: list) -> tuple[float, float, str]:
    """Slide the label along the route until it clears the entity boxes, the other edges
    and every label already placed — geometry alone collides often enough."""
    blocked = taken + blocked
    for frac in (0.5, 0.36, 0.64, 0.26, 0.74):
        for x, y, anchor in _sides_of(*_along(pts, frac)):
            if not _hits(_rect(x, y, anchor, text), blocked):
                taken.append(_rect(x, y, anchor, text))
                return x, y, anchor
    x, y, anchor = _sides_of(*_along(pts, 0.5))[0]
    taken.append(_rect(x, y, anchor, text))
    return x, y, anchor


def _pick_sides(parent: str, child: str, boxes: dict) -> tuple[str, str]:
    """First route shape whose legs miss every box other than its own two ends."""
    options = _side_options(ERD_GRID[parent], ERD_GRID[child])
    obstacles = [(b["x"] - 6, b["y"] - 6, b["x"] + b["width"] + 6, b["y"] + b["height"] + 6)
                 for name, b in boxes.items() if name not in (parent, child)]
    for s1, s2 in options:
        pts = _elbow(_anchor(boxes[parent], s1, 0.5), s1, _anchor(boxes[child], s2, 0.5), s2)
        legs = [(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
                for a, b in zip(pts, pts[1:])]
        if not any(_hits(leg, obstacles) for leg in legs):
            return s1, s2
    return options[0]


def erd_svg() -> str:
    """Star-schema ERD as a self-contained SVG — entities, typed columns, keys, cardinality.

    Generated on every render (it is deterministic and costs microseconds), so unlike the
    auto-laid-out lineage diagram it needs no pre-rendering and can never go stale.
    """
    models = load_project()["models"]
    boxes = {n: _entity_box(models[n]) for n in ERD_GRID if n in models}

    # ---- edges: declared FKs, documented join keys, then the dashed rollups ----
    declared = [(col["relationship"][0], rec["name"], col["name"])
                for rec in models.values() for col in rec["columns"] if col["relationship"]]
    edges = ([(p, c, lbl, False) for p, c, lbl in declared]
             + [(p, c, lbl, False) for p, c, _col, lbl in JOIN_EDGES]
             + [(p, c, lbl, True) for p, c, lbl in ROLLUP_EDGES])
    edges = [e for e in edges if e[0] in boxes and e[1] in boxes]

    # Grid geometry: column widths and row heights are the largest box in each. A gap
    # between two columns also has to fit the label of any edge that crosses it.
    cols = {c: max(b["width"] for n, b in boxes.items() if ERD_GRID[n][0] == c) for c in (0, 1, 2)}
    rows = {r: max(b["height"] for n, b in boxes.items() if ERD_GRID[n][1] == r) for r in (0, 1, 2)}
    gap_x = {0: _COL_GAP, 1: _COL_GAP}
    for parent, child, label, _dashed in edges:
        (pc, pr), (cc, cr) = ERD_GRID[parent], ERD_GRID[child]
        if pr == cr and abs(pc - cc) == 1:
            gap_x[min(pc, cc)] = max(gap_x[min(pc, cc)], len(label) * _CHAR + 34)
    col_x = {0: _COL_GAP}
    col_x[1] = col_x[0] + cols[0] + gap_x[0]
    col_x[2] = col_x[1] + cols[1] + gap_x[1]
    row_y = {0: 40}
    row_y[1] = row_y[0] + rows[0] + _RANK_GAP
    row_y[2] = row_y[1] + rows[1] + _RANK_GAP
    for name, box in boxes.items():
        c, r = ERD_GRID[name]
        box["x"] = col_x[c] + (cols[c] - box["width"]) / 2       # centre in its grid cell
        box["y"] = row_y[r] + (rows[r] - box["height"]) / 2
    width = col_x[2] + cols[2] + _COL_GAP
    height = row_y[2] + rows[2] + 40

    used: dict[tuple[str, str], int] = {}                        # spread edges sharing a side
    routes = []
    for parent, child, label, dashed in edges:
        # Pick the route shape that keeps out of the other entities' boxes.
        s1, s2 = _pick_sides(parent, child, boxes)
        slots = []
        for node, side in ((parent, s1), (child, s2)):
            i = used.get((node, side), 0)
            used[(node, side)] = i + 1
            slots.append(0.5 if i == 0 else (0.28 if i == 1 else 0.72))
        p1, p2 = _anchor(boxes[parent], s1, slots[0]), _anchor(boxes[child], s2, slots[1])
        routes.append((label, dashed, p1, s1, p2, s2, _elbow(p1, s1, p2, s2)))

    box_rects = [(b["x"] - 4, b["y"] - 4, b["x"] + b["width"] + 4, b["y"] + b["height"] + 4)
                 for b in boxes.values()]
    seg_rects = [[(min(a[0], b[0]) - 3, min(a[1], b[1]) - 3, max(a[0], b[0]) + 3, max(a[1], b[1]) + 3)
                  for a, b in zip(r[6], r[6][1:])] for r in routes]

    taken: list[tuple[float, float, float, float]] = []           # label boxes already placed
    parts = []
    for i, (label, dashed, p1, s1, p2, s2, pts) in enumerate(routes):
        d = " ".join(("M" if j == 0 else "L") + f"{x:.1f},{y:.1f}" for j, (x, y) in enumerate(pts))
        parts.append(f'<path d="{d}" fill="none" stroke="{MUTED}" stroke-width="1.3"'
                     f'{" stroke-dasharray=\"6 5\"" if dashed else ""}/>')
        parts.append(_foot(*p1, s1, many=False))                 # parent: exactly one
        parts.append(_foot(*p2, s2, many=True))                  # child: zero or many
        others = [r for j, rects in enumerate(seg_rects) if j != i for r in rects]
        lx, ly, anchor = _place_label(pts, label, taken, box_rects + others)
        parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
                     f'font-size="11" fill="{MUTED}" stroke="{SURFACE}" stroke-width="3.5" '
                     f'paint-order="stroke" font-family="{_MONO}">{label}</text>')

    # ---- entity boxes ----
    for name, b in boxes.items():
        x, y, w = b["x"], b["y"], b["width"]
        parts.append(f'<g transform="translate({x:.1f},{y:.1f})">')
        parts.append(f'<rect width="{w:.1f}" height="{b["height"]}" rx="10" fill="#FFFFFF" '
                     f'stroke="{b["stroke"]}" stroke-width="1.4"/>')
        parts.append(f'<path d="M0,10 a10,10 0 0 1 10,-10 h{w - 20:.1f} a10,10 0 0 1 10,10 '
                     f'v{_HEAD_H - 10} h-{w:.1f} z" fill="{b["fill"]}" stroke="{b["stroke"]}" '
                     f'stroke-width="1.4"/>')
        parts.append(f'<text x="{w / 2:.1f}" y="20" text-anchor="middle" font-size="13" '
                     f'font-weight="700" fill="{GREEN}" font-family="{_MONO}">{name}</text>')
        for i, (typ, col, key) in enumerate(b["rows"]):
            ry = _HEAD_H + i * _ROW_H
            if i % 2:
                parts.append(f'<rect x="1.4" y="{ry}" width="{w - 2.8:.1f}" '
                             f'height="{_ROW_H}" fill="{SURFACE}"/>')
            parts.append(f'<text x="{_PAD}" y="{ry + 14}" font-size="11.5" fill="{MUTED}" '
                         f'font-family="{_MONO}">{typ}</text>')
            parts.append(f'<text x="{_PAD + b["w_type"] + 10:.1f}" y="{ry + 14}" font-size="11.5" '
                         f'fill="{INK}" font-family="{_MONO}">{col}</text>')
            if key:
                parts.append(f'<text x="{w - _PAD:.1f}" y="{ry + 14}" text-anchor="end" font-size="10.5" '
                             f'font-weight="700" fill="{GREEN if key == "PK" else TERRA}" '
                             f'font-family="{_MONO}">{key}</text>')
        parts.append("</g>")

    body = "\n  ".join(parts)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
            f'role="img" aria-label="Voy marts entity relationship diagram">\n  {body}\n</svg>')


# ---------------------------------------------------------------- rendering --
DIAGRAMS = {"lineage": lineage_mermaid}      # pre-rendered; the ERD is drawn directly

# Passed to mermaid-cli when regenerating; mirrors the dashboard palette.
MERMAID_CONFIG = {
    "theme": "base",
    "fontFamily": "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    "themeVariables": {
        "primaryColor": "#D7E3D5", "primaryTextColor": INK, "primaryBorderColor": GREEN,
        "lineColor": MUTED, "textColor": INK, "fontSize": "13px",
        "clusterBkg": "#FFFFFF", "clusterBorder": GRID,
        "attributeBackgroundColorOdd": "#FFFFFF", "attributeBackgroundColorEven": SURFACE,
    },
    "flowchart": {"htmlLabels": True, "curve": "basis", "nodeSpacing": 40, "rankSpacing": 80, "padding": 12},
    "er": {"layoutDirection": "LR", "entityPadding": 12, "minEntityWidth": 140, "useMaxWidth": False},
}


def diagram_source(name: str) -> str:
    """The Mermaid for a pre-rendered diagram, generated from the dbt project right now."""
    return DIAGRAMS[name]()


def diagram_svg(name: str) -> str | None:
    """The ERD is drawn on the spot; the lineage DAG comes from its committed render."""
    if name == "erd":
        return erd_svg()
    path = ASSETS / f"{name}.svg"
    return path.read_text() if path.exists() else None


def is_stale(name: str) -> bool:
    """True if a pre-rendered diagram was built from different Mermaid than we'd generate today."""
    committed = ASSETS / f"{name}.mmd"
    return name in DIAGRAMS and committed.exists() and (
        committed.read_text().strip() != diagram_source(name).strip())


def _fluid(svg: str) -> str:
    """Make the SVG fill its box: drop mermaid's fixed width / max-width, keep the viewBox."""
    head, sep, rest = svg.partition(">")
    head = re.sub(r'\s(?:width|height|style)="[^"]*"', "", head)
    return f'{head} style="width:100%;height:100%;display:block"{sep}{rest}'


_VIEWER_PAGE = """
<!doctype html>
<html><head><meta charset="utf-8"/><style>
  html, body { margin:0; padding:0; height:100%; background:transparent; }
  #wrap { position:relative; height:100%; box-sizing:border-box; overflow:hidden; cursor:grab;
          background:__SURFACE__; border:1px solid __GRID__; border-radius:16px; touch-action:none;
          font-family:'Manrope', system-ui, -apple-system, sans-serif; }
  #wrap.grabbing { cursor:grabbing; }
  #stage { position:absolute; inset:0; transform-origin:0 0; will-change:transform; }
  #ctl { position:absolute; left:12px; bottom:12px; display:flex; gap:6px; }
  #ctl button { min-width:30px; height:28px; padding:0 9px; border-radius:8px; cursor:pointer;
                border:1px solid __GRID__; background:rgba(255,255,255,.92); color:__GREEN__;
                font:600 13px/1 'Manrope', system-ui, sans-serif; }
  #ctl button:hover { background:#fff; border-color:__GREEN__; }
  #hint { position:absolute; top:10px; right:12px; padding:3px 10px; border-radius:999px;
          background:rgba(255,255,255,.85); border:1px solid __GRID__; color:__MUTED__;
          font-size:11px; font-weight:600; pointer-events:none; }
</style></head>
<body>
  <div id="wrap">
    <div id="stage">__SVG__</div>
    <div id="ctl">
      <button data-z="in" title="Zoom in">+</button>
      <button data-z="out" title="Zoom out">&minus;</button>
      <button data-z="reset" title="Fit to view">Reset</button>
    </div>
    <div id="hint">scroll to zoom · drag to pan</div>
  </div>
  <script>
    (function () {
      var wrap = document.getElementById('wrap'), stage = document.getElementById('stage');
      var s = 1, tx = 0, ty = 0, drag = null, ox = 0, oy = 0;
      function apply() { stage.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + s + ')'; }
      function zoomAt(cx, cy, k) {                      // keep the point under the cursor fixed
        var ns = Math.min(24, Math.max(0.4, s * k));
        tx = cx - (cx - tx) * (ns / s); ty = cy - (cy - ty) * (ns / s); s = ns; apply();
      }
      wrap.addEventListener('wheel', function (e) {
        e.preventDefault();
        var r = wrap.getBoundingClientRect();
        zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * 0.0015));
      }, { passive: false });
      wrap.addEventListener('pointerdown', function (e) {
        if (e.button !== 0 || e.target.closest('#ctl')) return;   // capture would eat the button click
        drag = e.pointerId; ox = e.clientX - tx; oy = e.clientY - ty;
        wrap.setPointerCapture(drag); wrap.classList.add('grabbing');
      });
      wrap.addEventListener('pointermove', function (e) {
        if (e.pointerId !== drag) return;
        tx = e.clientX - ox; ty = e.clientY - oy; apply();
      });
      function end(e) {
        if (e.pointerId !== drag) return;
        wrap.releasePointerCapture(drag); drag = null; wrap.classList.remove('grabbing');
      }
      wrap.addEventListener('pointerup', end);
      wrap.addEventListener('pointercancel', end);
      wrap.addEventListener('dblclick', function (e) {
        var r = wrap.getBoundingClientRect();
        zoomAt(e.clientX - r.left, e.clientY - r.top, 1.6);
      });
      document.getElementById('ctl').addEventListener('click', function (e) {
        var b = e.target.closest('button'); if (!b) return;
        var r = wrap.getBoundingClientRect();
        if (b.dataset.z === 'reset') { s = 1; tx = 0; ty = 0; apply(); }
        else zoomAt(r.width / 2, r.height / 2, b.dataset.z === 'in' ? 1.35 : 1 / 1.35);
      });
    })();
  </script>
</body></html>
"""


def render_diagram(name: str, height: int = 560) -> None:
    """Inline the committed SVG in an iframe with scroll-zoom, drag-pan and +/−/reset."""
    svg = diagram_svg(name)
    if svg is None:
        st.warning(f"`streamlit/assets/{name}.svg` is missing — regenerate it with "
                   f"`python streamlit/data_model.py`.")
        return
    if is_stale(name):
        st.warning("This diagram is out of date with the dbt project — regenerate it with "
                   "`python streamlit/data_model.py`.")
    page = (_VIEWER_PAGE
            .replace("__SURFACE__", SURFACE).replace("__GRID__", GRID)
            .replace("__MUTED__", MUTED).replace("__GREEN__", GREEN)
            .replace("__SVG__", _fluid(svg)))
    if hasattr(st, "iframe"):                      # st.components.v1.html is deprecated
        st.iframe(page, height=height)
    else:                                          # streamlit < 1.55 (requirements floor is 1.40)
        components.html(page, height=height, scrolling=False)


if __name__ == "__main__":
    # Regenerate assets/<name>.mmd + <name>.svg from the current dbt project.
    #   python streamlit/data_model.py          (needs node — renders via npx mermaid-cli)
    import subprocess
    import tempfile

    ASSETS.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as cfg:
        json.dump(MERMAID_CONFIG, cfg)
    for diagram, build in DIAGRAMS.items():
        (ASSETS / f"{diagram}.mmd").write_text(build() + "\n")
        subprocess.run(
            ["npx", "-y", "@mermaid-js/mermaid-cli",
             "-i", str(ASSETS / f"{diagram}.mmd"), "-o", str(ASSETS / f"{diagram}.svg"),
             "-c", cfg.name, "-b", "transparent"],
            check=True,
        )
        print(f"✓ assets/{diagram}.svg")
