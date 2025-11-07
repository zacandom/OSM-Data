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

OUTPUT_DIR = "europe_religious_historic_sites_tiled"
os.makedirs(OUTPUT_DIR, exist_ok=True)

COUNTRIES = [
    "Austria", "Belgium", "Bulgaria", "Czechia", "Denmark", "Finland",
    "France", "Germany", "Greece", "Hungary", "Italy", "Luxembourg",
    "Netherlands", "Norway", "Poland", "Portugal", "Romania", "Slovakia",
    "Spain", "Sweden", "Switzerland", "United Kingdom"
]

RELIGIOUS_BUILDINGS = [
    "church", "chapel", "cathedral", "monastery", "abbey",
    "basilica", "mosque", "synagogue", "temple", "shrine"
]

RELIGIOUS_HISTORIC = [
    "church", "chapel", "cathedral", "monastery", "abbey",
    "basilica", "mosque", "synagogue", "temple",
    "wayside_shrine", "wayside_cross", "religious"
]

CIV_KEYS = [
    "historic:civilization",
    "civilization",
    "archaeological_site:civilization",
    "culture",
]

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
        "historic": RELIGIOUS_HISTORIC + ["yes", "1", "true"],
        "amenity": "place_of_worship",
        "building": RELIGIOUS_BUILDINGS,
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


def extract_civilization_from_row(row):
    for key in CIV_KEYS:
        if key in row and pd.notna(row[key]):
            val = row[key]
            if isinstance(val, (list, tuple)) and val:
                val = val[0]
            val = str(val).strip()
            if val:
                return val
    return None


def tidy_religious_historic(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    if gdf.empty:
        return pd.DataFrame(columns=[
            "name", "site_type", "civilization",
            "lat", "lon", "osm_id", "wikidata", "wikipedia"
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

    hist = gdf.get("historic", "").astype(str).str.lower()
    bldg = gdf.get("building", "").astype(str).str.lower()
    amen = gdf.get("amenity", "").astype(str).str.lower()

    hist_yes = hist.isin(["yes", "1", "true"])
    hist_rel = hist.isin(RELIGIOUS_HISTORIC)
    bldg_rel = bldg.isin(RELIGIOUS_BUILDINGS)
    amen_pow = (amen == "place_of_worship")

    site_type = pd.Series(index=gdf.index, dtype="object")
    site_type[hist_rel] = "historic:" + hist[hist_rel]
    site_type[amen_pow & hist_yes] = "historic:place_of_worship"
    site_type[bldg_rel & hist_yes] = "historic_building:" + bldg[bldg_rel & hist_yes]

    mask = site_type.notna()
    if not mask.any():
        return pd.DataFrame(columns=[
            "name", "site_type", "civilization",
            "lat", "lon", "osm_id", "wikidata", "wikipedia"
        ])

    gdf = gdf[mask].copy()
    gdf["site_type"] = site_type[mask]
    gdf["civilization"] = gdf.apply(extract_civilization_from_row, axis=1)

    if "osmid" in gdf.columns:
        gdf = gdf.drop_duplicates(subset=["osmid"])
        osm_id = gdf["osmid"].astype(str)
    else:
        osm_id = gdf.index.astype(str)

    out = pd.DataFrame({
        "name": gdf["name"],
        "site_type": gdf["site_type"],
        "civilization": gdf["civilization"],
        "lat": gdf["lat"],
        "lon": gdf["lon"],
        "osm_id": osm_id,
        "wikidata": gdf.get("wikidata", pd.Series(index=gdf.index, dtype="object")),
        "wikipedia": gdf.get("wikipedia", pd.Series(index=gdf.index, dtype="object")),
    })

    out["__has_name"] = out["name"].notna() & (out["name"].astype(str) != "")
    out = out.sort_values(
        ["__has_name", "site_type", "name"],
        ascending=[False, True, True]
    ).drop(columns="__has_name")

    return out.reset_index(drop=True)


def run_country(country: str):
    qname = ALIAS.get(country, country)
    try:
        country_gdf = retry(ox_geo.geocode_to_gdf, qname)
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
    df = tidy_religious_historic(all_gdf)
    fname = f"{sanitize(country)}_religious_historic_sites_tiled.xlsx"
    path = os.path.join(OUTPUT_DIR, fname)
    df.to_excel(path, index=False)


def main():
    for c in COUNTRIES:
        run_country(c)


if __name__ == "__main__":
    main()
