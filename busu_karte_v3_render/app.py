#!/usr/bin/env python3
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.request import urlopen, Request
from pathlib import Path
import json, math, os

ROOT = Path(__file__).resolve().parent

PLACES = {
    "Jurkalne": {"label":"Jūrkalne","lat":57.03282,"lon":21.39916},
    "Uzava": {"label":"Užava","lat":57.246963,"lon":21.414654},
    "Pavilosta": {"label":"Pāvilosta","lat":56.89276,"lon":21.18073},
    "Ventspils": {"label":"Ventspils","lat":57.38874,"lon":21.52611}
}

def get_json(url):
    req = Request(url, headers={"User-Agent":"BusuKartePrototype/0.3"})
    with urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

def offshore_endpoint(lat, lon, distance_m):
    meters_per_deg_lon = 111320 * math.cos(math.radians(lat))
    return lat, lon - distance_m / meters_per_deg_lon

class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        rel = urlparse(path).path.lstrip("/") or "index.html"
        return str(ROOT / rel)

    def send_json(self, obj, code=200):
        body=json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u=urlparse(self.path)

        if u.path == "/health":
            return self.send_json({"ok":True})

        if u.path == "/api/weather":
            q=parse_qs(u.query)
            key=q.get("place",["Jurkalne"])[0]
            p=PLACES.get(key)
            if not p:
                return self.send_json({"error":"Nezināma vieta"},404)

            weather = "https://api.open-meteo.com/v1/forecast?" + urlencode({
                "latitude":p["lat"],"longitude":p["lon"],
                "current":"temperature_2m,wind_speed_10m,wind_direction_10m",
                "wind_speed_unit":"ms","timezone":"Europe/Riga"
            })
            marine = "https://marine-api.open-meteo.com/v1/marine?" + urlencode({
                "latitude":p["lat"],"longitude":p["lon"],
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
            q=parse_qs(u.query)
            key=q.get("place",["Jurkalne"])[0]
            p=PLACES.get(key)
            if not p:
                return self.send_json({"error":"Nezināma vieta"},404)
            distance=200
            lat2,lon2=offshore_endpoint(p["lat"],p["lon"],distance)
            geom=f"LINESTRING({p['lon']} {p['lat']},{lon2} {lat2})"
            endpoint="https://rest.emodnet-bathymetry.eu/depth_profile?" + urlencode({"geom":geom})
            try:
                vals=get_json(endpoint)
                if not isinstance(vals,list):
                    raise ValueError("Negaidīta EMODnet atbilde")
                n=len(vals)
                profile=[{
                    "distance_m":0 if n<=1 else round(distance*i/(n-1),1),
                    "depth_m":v
                } for i,v in enumerate(vals)]
                return self.send_json({"place":p["label"],"profile":profile,"source":"EMODnet"})
            except Exception as e:
                return self.send_json({"error":"Dziļumu profils nav pieejams","detail":str(e)},502)

        return super().do_GET()

if __name__ == "__main__":
    os.chdir(ROOT)
    port=int(os.environ.get("PORT","8765"))
    ThreadingHTTPServer(("0.0.0.0",port),Handler).serve_forever()
