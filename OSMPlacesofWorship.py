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

OUTPUT_DIR = "europe_places_of_worship_tiled"
os.makedirs(OUTPUT_DIR, exist_ok=True)

COUNTRIES = [
    "Austria", "Belgium", "Bulgaria", "Czechia", "Denmark", "Finland",
    "France", "Germany", "Greece", "Hungary", "Italy", "Luxembourg",
    "Netherlands", "Norway", "Poland", "Portugal", "Romania", "Slovakia",
    "Spain", "Sweden", "Switzerland", "United Kingdom"
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
        "amenity": "place_of_worship"
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


def tidy_worship_sites(gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    if gdf.empty:
        return pd.DataFrame(columns=[
            "name", "denomination", "religion",
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

    denom_cols = [c for c in ("denomination", "religion:denomination") if c in gdf.columns]
    if denom_cols:
        gdf["denomination"] = gdf[denom_cols].bfill(axis=1).iloc[:, 0]
    else:
        gdf["denomination"] = None

    if "religion" in gdf.columns:
        gdf["religion"] = gdf["religion"]
    else:
        gdf["religion"] = None

    mask = gdf.get("amenity", "").astype(str).str.lower().eq("place_of_worship")
    gdf = gdf[mask].copy()
    if gdf.empty:
        return pd.DataFrame(columns=[
            "name", "denomination", "religion",
            "lat", "lon", "osm_id", "wikidata", "wikipedia"
        ])

    if "osmid" in gdf.columns:
        gdf = gdf.drop_duplicates(subset=["osmid"])
        osm_id = gdf["osmid"].astype(str)
    else:
        osm_id = gdf.index.astype(str)

    out = pd.DataFrame({
        "name": gdf["name"],
        "denomination": gdf["denomination"],
        "religion": gdf["religion"],
        "lat": gdf["lat"],
        "lon": gdf["lon"],
        "osm_id": osm_id,
        "wikidata": gdf.get("wikidata", pd.Series(index=gdf.index, dtype="object")),
        "wikipedia": gdf.get("wikipedia", pd.Series(index=gdf.index, dtype="object")),
    })

    out["__has_name"] = out["name"].notna() & (out["name"].astype(str) != "")
    out = out.sort_values(
        ["religion", "denomination", "__has_name", "name"],
        ascending=[True, True, False, True]
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
    df = tidy_worship_sites(all_gdf)
    fname = f"{sanitize(country)}_places_of_worship_tiled.xlsx"
    path = os.path.join(OUTPUT_DIR, fname)
    df.to_excel(path, index=False)


def main():
    for c in COUNTRIES:
        run_country(c)


if __name__ == "__main__":
    main()
