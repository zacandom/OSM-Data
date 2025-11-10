import os
import re
import time
import random
import concurrent.futures as futures

import pandas as pd
import geopandas as gpd
from shapely.geometry import box

import osmnx as ox
from osmnx import geocoder as ox_geo
from osmnx import features as ox_features

OUTPUT_DIR = "europe_historic_conflict_preWW_sites_tiled"
os.makedirs(OUTPUT_DIR, exist_ok=True)

COUNTRIES = [
    "Austria", "Belgium", "Bulgaria", "Czechia", "Denmark", "Finland",
    "France", "Germany", "Greece", "Hungary", "Italy", "Luxembourg",
    "Netherlands", "Norway", "Poland", "Portugal", "Romania", "Slovakia",
    "Spain", "Sweden", "Switzerland", "United Kingdom"
]

CONFLICT_HISTORIC_VALUES = [
    "battlefield", "battle_site", "battle",
    "war_memorial", "memorial", "monument",
    "fort", "castle", "bunker", "trench", "pillbox",
    "tank", "aircraft", "ship", "ruins", "bomb_crater",
]

OLD_CONFLICT_PATTERN = re.compile(
    r"(crusade|crusader|holy\s*war|templar|teutonic|hospitaller|"
    r"reconquista|reconquest|byzantine|ottoman|turkish\s+war|austro[-\s]*turkish|"
    r"habsburg[-\s]*ottoman|thirty\s*years'? war|hundred\s*years'? war|"
    r"napoleonic|napoleon|medieval|middle\s+ages|roman|frankish|carolingian|saxon\s+war)",
    re.IGNORECASE,
)

MODERN_EXCLUDE_PATTERN = re.compile(
    r"(world\s*war|ww1|wwi|ww2|wwii|191[4-9]|1939|194[0-5]|cold\s*war|"
    r"korean\s*war|vietnam\s*war|gulf\s*war|iraq\s*war|afghanistan\s*war|nato)",
    re.IGNORECASE,
)

TILE_SIZE_DEG = 0.8
MAX_WORKERS = 4

ox.settings.timeout = 90
ox.settings.use_cache = False
ox.settings.log_console = False


def pause(a=0.3, b=0.8):
    time.sleep(random.uniform(a, b))


def sanitize(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return re.sub(r"\s+", "_", name.strip())


def retry(func, *args, max_tries=3, **kwargs):
    err = None
    for _ in range(max_tries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            err = e
            pause()
    raise err


def make_tiles(geom, size_deg=TILE_SIZE_DEG):
    minx, miny, maxx, maxy = geom.bounds
    xs, x = [], minx
    while x < maxx:
        xs.append(x)
        x += size_deg
    xs.append(maxx)
    ys, y = [], miny
    while y < maxy:
        ys.append(y)
        y += size_deg
    ys.append(maxy)
    tiles = []
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            t = box(xs[i], ys[j], xs[i + 1], ys[j + 1])
            if t.intersects(geom):
                sub = t.intersection(geom)
                if not sub.is_empty:
                    tiles.append(sub)
    return tiles


def query_tile(tile_geom, idx, total):
    tags = {
        "historic": CONFLICT_HISTORIC_VALUES + ["yes", "1", "true"],
        "military": True,
        "landuse": "military",
    }
    try:
        gdf = ox_features.features_from_polygon(tile_geom, tags)
    except Exception:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    if gdf is None or gdf.empty:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    if not isinstance(gdf, gpd.GeoDataFrame):
        gdf = gpd.GeoDataFrame(gdf, geometry="geometry")
    if gdf.crs is None:
        gdf.set_crs(4326, inplace=True)
    return gdf.to_crs(4326)


def normalize_text_fields(row, keys):
    parts = []
    for k in keys:
        if k in row and pd.notna(row[k]):
            val = row[k]
            if isinstance(val, (list, tuple)):
                val = " ".join(str(v) for v in val)
            parts.append(str(val))
    return " ".join(parts).lower()


def classify_conflict_type(row):
    hist = str(row.get("historic", "")).lower()
    mil = str(row.get("military", "")).lower()
    landuse = str(row.get("landuse", "")).lower()

    text = normalize_text_fields(
        row,
        [
            "name", "name:en", "alt_name", "description", "inscription",
            "note", "memorial", "memorial:conflict", "subject",
            "subject:wikidata", "wikidata", "wikipedia",
        ],
    )

    if MODERN_EXCLUDE_PATTERN.search(text):
        return None

    has_old_conflict = bool(OLD_CONFLICT_PATTERN.search(text))

    if hist in {"battlefield", "battle_site", "battle"}:
        if has_old_conflict or not MODERN_EXCLUDE_PATTERN.search(hist):
            return "pre_modern_battlefield"

    if hist == "war_memorial":
        if has_old_conflict:
            return "pre_modern_war_memorial"
        return None

    if hist in {"memorial", "monument"}:
        if has_old_conflict:
            return "pre_modern_memorial_or_monument"
        return None

    if hist in {"fort", "castle", "bunker", "trench", "pillbox", "ruins"}:
        if has_old_conflict or (landuse == "military" and not MODERN_EXCLUDE_PATTERN.search(text)):
            return "pre_modern_fortification_or_military_site"

    if mil:
        if has_old_conflict and not MODERN_EXCLUDE_PATTERN.search(text):
            return f"pre_modern_military_site:{mil}"
        return None

    if landuse == "military":
        if has_old_conflict and not MODERN_EXCLUDE_PATTERN.search(text):
            return "pre_modern_military_landuse"
        return None

    if hist in {"tank", "aircraft", "ship", "bomb_crater"}:
        if has_old_conflict and not MODERN_EXCLUDE_PATTERN.search(text):
            return "pre_modern_war_object"
        return None

    if hist in {"yes", "1", "true"} and has_old_conflict:
        return "pre_modern_historic_conflict_feature"

    return None


def tidy_conflict_sites(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    if gdf.empty:
        return pd.DataFrame(columns=[
            "name", "conflict_type", "lat", "lon", "osm_id", "wikidata", "wikipedia"
        ])

    geom = gdf.geometry
    cent = geom.where(geom.geom_type == "Point", geom.centroid)
    gdf["lon"] = cent.x
    gdf["lat"] = cent.y

    name_cols = [
        c for c in ("name", "name:en", "alt_name", "old_name",
                    "loc_name", "official_name")
        if c in gdf.columns
    ]
    if name_cols:
        gdf["name"] = gdf[name_cols].bfill(axis=1).iloc[:, 0]
    else:
        gdf["name"] = None

    conflict_type = gdf.apply(classify_conflict_type, axis=1)
    mask = conflict_type.notna()
    if not mask.any():
        return pd.DataFrame(columns=[
            "name", "conflict_type", "lat", "lon", "osm_id", "wikidata", "wikipedia"
        ])

    gdf = gdf[mask].copy()
    gdf["conflict_type"] = conflict_type[mask]

    if "osmid" in gdf.columns:
        gdf = gdf.drop_duplicates(subset=["osmid"])
        osm_id = gdf["osmid"].astype(str)
    else:
        osm_id = gdf.index.astype(str)

    out = pd.DataFrame({
        "name": gdf["name"],
        "conflict_type": gdf["conflict_type"],
        "lat": gdf["lat"],
        "lon": gdf["lon"],
        "osm_id": osm_id,
        "wikidata": gdf.get("wikidata", pd.Series(index=gdf.index, dtype="object")),
        "wikipedia": gdf.get("wikipedia", pd.Series(index=gdf.index, dtype="object")),
    })

    out["__has_name"] = out["name"].notna() & (out["name"].astype(str) != "")
    out = out.sort_values(
        ["__has_name", "conflict_type", "name"],
        ascending=[False, True, True]
    ).drop(columns="__has_name")

    return out.reset_index(drop=True)


def run_country(country: str):
    try:
        country_gdf = retry(ox_geo.geocode_to_gdf, country)
    except Exception:
        return
    geom = country_gdf.geometry.iloc[0]
    tiles = make_tiles(geom)
    if not tiles:
        return
    frames = []
    with futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        tasks = (
            ex.submit(query_tile, tile_geom, idx, len(tiles))
            for idx, tile_geom in enumerate(tiles)
        )
        for fut in futures.as_completed(tasks):
            g = fut.result()
            if g is not None and not g.empty:
                frames.append(g)
    if not frames:
        return
    all_gdf = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True),
        geometry="geometry",
        crs="EPSG:4326"
    )
    df = tidy_conflict_sites(all_gdf)
    fname = f"{sanitize(country)}_historic_conflict_preWW_sites_tiled.xlsx"
    path = os.path.join(OUTPUT_DIR, fname)
    df.to_excel(path, index=False)


def main():
    for c in COUNTRIES:
        run_country(c)


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
