#!/usr/bin/env python3
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.request import urlopen, Request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import json, math, os

ROOT = Path(__file__).resolve().parent

PLACES = {
    "Jurkalne": {"label":"Jūrkalne","coast_lat":57.005449,"coast_lon":21.381410,"sea_offset_m":40},
    "Uzava": {"label":"Užava","coast_lat":57.246963,"coast_lon":21.414654,"sea_offset_m":40},
    "Pavilosta": {"label":"Pāvilosta","coast_lat":56.892760,"coast_lon":21.180730,"sea_offset_m":40},
    "Ventspils": {"label":"Ventspils","coast_lat":57.388740,"coast_lon":21.526110,"sea_offset_m":40}
}

def get_json(url, timeout=20):
    req = Request(url, headers={"User-Agent":"BusuKartePrototype/0.3.1"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def west_point(lat, lon, metres):
    m_per_deg_lon = 111320 * math.cos(math.radians(lat))
    return lat, lon - metres / m_per_deg_lon

def depth_sample(lat, lon):
    geom = f"POINT({lon} {lat})"
    url = "https://rest.emodnet-bathymetry.eu/depth_sample?" + urlencode({"geom": geom})
    data = get_json(url)
    depth = data.get("smoothed")
    if depth is None:
        depth = data.get("avg")
    if depth is None:
        return None
    depth = float(depth)
    return abs(depth)

class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        rel = urlparse(path).path.lstrip("/") or "index.html"
        return str(ROOT / rel)

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)

        if u.path == "/health":
            return self.send_json({"ok": True, "version":"3.1"})


        if u.path == "/api/dzilumi-resource":
            try:
                pkg = get_json("https://data.gov.lv/dati/api/3/action/package_show?id=dziumi-wfs")
                result = pkg.get("result", {})
                resources = []
                for r in result.get("resources", []):
                    resources.append({
                        "name": r.get("name"),
                        "format": r.get("format"),
                        "url": r.get("url"),
                        "resource_type": r.get("resource_type"),
                        "description": r.get("description")
                    })
                return self.send_json({
                    "title": result.get("title"),
                    "license_title": result.get("license_title"),
                    "resources": resources
                })
            except Exception as e:
                return self.send_json({"error":"Neizdevās nolasīt Dziļumi WFS CKAN metadatus","detail":str(e)},502)


        if u.path == "/api/geolatvija-probe":
            try:
                import re
                page_url = "https://geolatvija.lv/main?geoProductId=74"
                req = Request(page_url, headers={"User-Agent":"Mozilla/5.0"})
                with urlopen(req, timeout=20) as r:
                    html = r.read().decode("utf-8", errors="ignore")
                scripts = re.findall(r'<script[^>]+src=["\\\']([^"\\\']+)["\\\']', html, re.I)
                scripts = [s if s.startswith("http") else "https://geolatvija.lv" + ("" if s.startswith("/") else "/") + s for s in scripts]
                hits = []
                for s in scripts[:12]:
                    try:
                        req2 = Request(s, headers={"User-Agent":"Mozilla/5.0"})
                        with urlopen(req2, timeout=20) as r2:
                            js = r2.read().decode("utf-8", errors="ignore")
                        for pat in [r'https?://[^"\\\']+(?:wfs|WFS)[^"\\\']*',
                                    r'https?://[^"\\\']+GetCapabilities[^"\\\']*',
                                    r'https?://[^"\\\']+ows\\?[^"\\\']*']:
                            for m in re.findall(pat, js):
                                if m not in hits:
                                    hits.append(m)
                    except Exception:
                        pass
                return self.send_json({
                    "page": page_url,
                    "scripts": scripts,
                    "wfs_candidates": hits[:50]
                })
            except Exception as e:
                return self.send_json({"error":"Neizdevās izpētīt ĢEO Latvija lapu","detail":str(e)},502)


        if u.path == "/api/geolatvija-runtime":
            try:
                import re
                urls = [
                    "https://geolatvija.lv/runtime-config.js",
                    "https://geolatvija.lv/static/js/main.db9060ea.js"
                ]
                out = {}
                for target in urls:
                    req = Request(target, headers={"User-Agent":"Mozilla/5.0"})
                    with urlopen(req, timeout=20) as r:
                        txt = r.read().decode("utf-8", errors="ignore")
                    candidates = []
                    for pat in [
                        r'https?://[^"\\\'\\s)]+',
                        r'["\\\']([^"\\\']*(?:api|service|wfs|ows|geoProduct|metadata)[^"\\\']*)["\\\']'
                    ]:
                        for m in re.findall(pat, txt, re.I):
                            val = m if isinstance(m, str) else m[0]
                            if val and val not in candidates:
                                candidates.append(val)
                    out[target] = {
                        "length": len(txt),
                        "preview": txt[:4000],
                        "candidates": candidates[:200]
                    }
                return self.send_json(out)
            except Exception as e:
                return self.send_json({"error":"Neizdevās nolasīt GeoLatvija runtime konfigurāciju","detail":str(e)},502)

        if u.path == "/api/weather":
            q = parse_qs(u.query)
            key = q.get("place", ["Jurkalne"])[0]
            p = PLACES.get(key)
            if not p:
                return self.send_json({"error":"Nezināma vieta"},404)

            lat, lon = west_point(p["coast_lat"], p["coast_lon"], p["sea_offset_m"] + 100)

            weather = "https://api.open-meteo.com/v1/forecast?" + urlencode({
                "latitude":lat,"longitude":lon,
                "current":"temperature_2m,wind_speed_10m,wind_direction_10m",
                "wind_speed_unit":"ms","timezone":"Europe/Riga"
            })
            marine = "https://marine-api.open-meteo.com/v1/marine?" + urlencode({
                "latitude":lat,"longitude":lon,
                "current":"wave_height,wave_direction,wave_period,sea_surface_temperature",
                "timezone":"Europe/Riga","cell_selection":"sea"
            })

            try:
                return self.send_json({
                    "place":p["label"],
                    "weather":get_json(weather),
                    "marine":get_json(marine)
                })
            except Exception as e:
                return self.send_json({"error":"Prognožu dati nav pieejami","detail":str(e)},502)

        if u.path == "/api/depth":
            q = parse_qs(u.query)
            key = q.get("place", ["Jurkalne"])[0]
            p = PLACES.get(key)
            if not p:
                return self.send_json({"error":"Nezināma vieta"},404)

            distances = list(range(0, 201, 25))
            profile = [None] * len(distances)
            jobs = {}

            with ThreadPoolExecutor(max_workers=5) as ex:
                for i, d in enumerate(distances):
                    total = p["sea_offset_m"] + d
                    lat, lon = west_point(p["coast_lat"], p["coast_lon"], total)
                    fut = ex.submit(depth_sample, lat, lon)
                    jobs[fut] = (i, d, total, lat, lon)

                for fut in as_completed(jobs):
                    i, d, total, lat, lon = jobs[fut]
                    try:
                        dep = fut.result()
                        profile[i] = {"distance_m":d,"approx_from_coast_m":total,"depth_m":dep,"lat":lat,"lon":lon}
                    except Exception as e:
                        profile[i] = {"distance_m":d,"approx_from_coast_m":total,"depth_m":None,"lat":lat,"lon":lon,"error":str(e)}

            valid = [x for x in profile if x and x["depth_m"] is not None]
            if not valid:
                return self.send_json({"error":"EMODnet neatgrieza derīgus dziļuma punktus","profile":profile},502)

            return self.send_json({
                "place":p["label"],
                "profile":profile,
                "source":"EMODnet Bathymetry /depth_sample",
                "note":"0 m grafikā sākas aptuveni 40 m no krasta jūrā."
            })

        return super().do_GET()

if __name__ == "__main__":
    os.chdir(ROOT)
    port = int(os.environ.get("PORT","8765"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
