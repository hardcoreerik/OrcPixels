# Offline ham-radio location data (FT8/FT4 Live Data screens)

Used by `orcpixels/sources/hamgeo.py`; nothing here is fetched at run time.

| File | What | Source / licence |
|---|---|---|
| `grid_places.tsv.gz` | 4-character Maidenhead square -> largest city in it (or, marked `~`, the nearest city within 160 km), US/CA/AU state code, ISO country | Built from GeoNames `cities15000` + `admin1CodesASCII` (CC BY 4.0, https://www.geonames.org/), 2026-10-08 |
| `countries.tsv` | ISO 3166 alpha-2 -> country name | GeoNames `countryInfo.txt` (CC BY 4.0) |
| `cty.dat.gz` | Callsign prefix -> DXCC entity, continent, lat/lon (AD1C country file, used when a decode carries no grid) | Jim Reisert AD1C, https://www.country-files.com/ (free to use and redistribute), downloaded 2026-10-08 |
| `../../../assets/na_land_40x16.png` | North America land mask, lon -135..-55, lat 20..60 | Natural Earth 1:110m land (public domain) |

Refresh: re-run the snippet in the commit message that added these files (download, bucket cities per square, gzip).
