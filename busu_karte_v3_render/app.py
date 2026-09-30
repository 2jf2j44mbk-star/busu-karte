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

        if u.path in ["/", "/index.html"]:
            try:
                body = (ROOT / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type","text/html; charset=utf-8")
                self.send_header("Cache-Control","no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma","no-cache")
                self.send_header("Expires","0")
                self.send_header("X-Content-Type-Options","nosniff")
                self.send_header("Content-Length",str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            except Exception as e:
                return self.send_json({"error":"Neizdevās ielādēt sākumlapu","detail":str(e)},500)

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


        if u.path == "/api/geolatvija-wfs":
            try:
                import re
                import xml.etree.ElementTree as ET
                candidates = [
                    "https://geolatvija.lv/geoserver/ows?service=WFS&request=GetCapabilities",
                    "https://geolatvija.lv/geoserver/wfs?service=WFS&request=GetCapabilities"
                ]
                results = []
                for target in candidates:
                    try:
                        req = Request(target, headers={"User-Agent":"Mozilla/5.0"})
                        with urlopen(req, timeout=30) as r:
                            raw = r.read()
                            ctype = r.headers.get("Content-Type")
                        textxml = raw.decode("utf-8", errors="ignore")
                        layers = []
                        try:
                            root = ET.fromstring(raw)
                            for ft in root.iter():
                                if ft.tag.endswith("FeatureType"):
                                    name = title = abstract = None
                                    for ch in list(ft):
                                        if ch.tag.endswith("Name"): name = (ch.text or "").strip()
                                        elif ch.tag.endswith("Title"): title = (ch.text or "").strip()
                                        elif ch.tag.endswith("Abstract"): abstract = (ch.text or "").strip()
                                    blob = " ".join([x for x in [name,title,abstract] if x]).lower()
                                    if any(k in blob for k in ["dziļ","dzil","depth","bathym","batim","jūra","jura"]):
                                        layers.append({"name":name,"title":title,"abstract":abstract})
                        except Exception:
                            pass
                        results.append({
                            "url": target,
                            "content_type": ctype,
                            "length": len(raw),
                            "preview": textxml[:1000],
                            "matching_layers": layers[:100]
                        })
                    except Exception as e:
                        results.append({"url":target,"error":str(e)})
                return self.send_json({"results":results})
            except Exception as e:
                return self.send_json({"error":"Neizdevās pārbaudīt GeoLatvija WFS","detail":str(e)},502)


        if u.path == "/api/geolatvija-routes":
            try:
                import re
                target = "https://geolatvija.lv/static/js/main.db9060ea.js"
                req = Request(target, headers={"User-Agent":"Mozilla/5.0"})
                with urlopen(req, timeout=30) as r:
                    js = r.read().decode("utf-8", errors="ignore")

                terms = ["geoProductId","geoProduct","downloadService","download","WFS","GetCapabilities","serviceUrl","infoMapAccessService"]
                snippets = {}
                for term in terms:
                    arr = []
                    start = 0
                    while True:
                        i = js.find(term, start)
                        if i < 0:
                            break
                        arr.append(js[max(0,i-350):min(len(js),i+700)])
                        start = i + len(term)
                        if len(arr) >= 20:
                            break
                    snippets[term] = arr

                api_paths = []
                for m in re.findall(r'["\\\'](/[^"\\\']{2,180})["\\\']', js):
                    low = m.lower()
                    if any(k in low for k in ["geoproduct","download","service","metadata","wfs","ows"]):
                        if m not in api_paths:
                            api_paths.append(m)

                return self.send_json({
                    "api_paths": api_paths[:300],
                    "snippets": snippets
                })
            except Exception as e:
                return self.send_json({"error":"Neizdevās analizēt GeoLatvija route","detail":str(e)},502)


        if u.path == "/api/vraa-wfs":
            try:
                import xml.etree.ElementTree as ET
                targets = [
                    "https://geolatvija.lv/geoserver/vraa/wfs?service=WFS&request=GetCapabilities",
                    "https://geolatvija.lv/geoserver/vraa/ows?service=WFS&request=GetCapabilities"
                ]
                results = []
                for target in targets:
                    try:
                        req = Request(target, headers={"User-Agent":"Mozilla/5.0"})
                        with urlopen(req, timeout=30) as r:
                            raw = r.read()
                            ctype = r.headers.get("Content-Type")
                        layers = []
                        root = ET.fromstring(raw)
                        for ft in root.iter():
                            if ft.tag.endswith("FeatureType"):
                                name = title = abstract = None
                                for ch in list(ft):
                                    if ch.tag.endswith("Name"): name = (ch.text or "").strip()
                                    elif ch.tag.endswith("Title"): title = (ch.text or "").strip()
                                    elif ch.tag.endswith("Abstract"): abstract = (ch.text or "").strip()
                                blob = " ".join([x for x in [name,title,abstract] if x]).lower()
                                if any(k in blob for k in ["dziļ","dzil","depth","bathym","batim","jūra","jura"]):
                                    layers.append({"name":name,"title":title,"abstract":abstract})
                        results.append({
                            "url":target,
                            "content_type":ctype,
                            "layer_count":sum(1 for ft in root.iter() if ft.tag.endswith("FeatureType")),
                            "matching_layers":layers[:200]
                        })
                    except Exception as e:
                        results.append({"url":target,"error":str(e)})
                return self.send_json({"results":results})
            except Exception as e:
                return self.send_json({"error":"Neizdevās pārbaudīt VRAA WFS","detail":str(e)},502)


        if u.path == "/api/vraa-depth-samples":
            try:
                layers = [
                    "vraa:msp_dziluma_apgabali",
                    "vraa:msp_dzilumatizmes",
                    "vraa:zm_dzilrakumi_103"
                ]
                bbox = "21.30,56.95,21.48,57.08,EPSG:4326"
                out = []
                for layer in layers:
                    item = {"layer":layer}
                    try:
                        desc_url = "https://geolatvija.lv/geoserver/vraa/wfs?" + urlencode({
                            "service":"WFS",
                            "version":"2.0.0",
                            "request":"DescribeFeatureType",
                            "typeNames":layer
                        })
                        req = Request(desc_url, headers={"User-Agent":"Mozilla/5.0"})
                        with urlopen(req, timeout=30) as r:
                            desc = r.read().decode("utf-8", errors="ignore")
                        item["describe_preview"] = desc[:4000]
                    except Exception as e:
                        item["describe_error"] = str(e)

                    try:
                        feat_url = "https://geolatvija.lv/geoserver/vraa/wfs?" + urlencode({
                            "service":"WFS",
                            "version":"2.0.0",
                            "request":"GetFeature",
                            "typeNames":layer,
                            "bbox":bbox,
                            "count":"10",
                            "outputFormat":"application/json"
                        })
                        req = Request(feat_url, headers={"User-Agent":"Mozilla/5.0"})
                        with urlopen(req, timeout=30) as r:
                            data = json.loads(r.read().decode("utf-8"))
                        item["numberReturned"] = data.get("numberReturned")
                        item["features"] = data.get("features", [])[:10]
                    except Exception as e:
                        item["feature_error"] = str(e)

                    out.append(item)
                return self.send_json({"bbox":bbox,"layers":out})
            except Exception as e:
                return self.send_json({"error":"Neizdevās iegūt Latvijas dziļumu slāņu paraugus","detail":str(e)},502)


        if u.path == "/api/vraa-depth-compact":
            try:
                import xml.etree.ElementTree as ET
                layers = [
                    "vraa:msp_dziluma_apgabali",
                    "vraa:msp_dzilumatizmes",
                    "vraa:zm_dzilrakumi_103"
                ]
                bbox = "21.30,56.95,21.48,57.08,EPSG:4326"
                out = []

                for layer in layers:
                    item = {"layer": layer}

                    # Field names/types from DescribeFeatureType.
                    try:
                        desc_url = "https://geolatvija.lv/geoserver/vraa/wfs?" + urlencode({
                            "service":"WFS",
                            "version":"2.0.0",
                            "request":"DescribeFeatureType",
                            "typeNames":layer
                        })
                        req = Request(desc_url, headers={"User-Agent":"Mozilla/5.0"})
                        with urlopen(req, timeout=30) as r:
                            raw = r.read()
                        root = ET.fromstring(raw)
                        fields = []
                        for el in root.iter():
                            if el.tag.endswith("element") and el.attrib.get("name"):
                                fields.append({
                                    "name": el.attrib.get("name"),
                                    "type": el.attrib.get("type"),
                                    "nillable": el.attrib.get("nillable")
                                })
                        item["fields"] = fields
                    except Exception as e:
                        item["describe_error"] = str(e)

                    # Small sample. Strip geometry down to type + first coordinate.
                    try:
                        feat_url = "https://geolatvija.lv/geoserver/vraa/wfs?" + urlencode({
                            "service":"WFS",
                            "version":"2.0.0",
                            "request":"GetFeature",
                            "typeNames":layer,
                            "bbox":bbox,
                            "count":"5",
                            "outputFormat":"application/json",
                            "srsName":"EPSG:4326"
                        })
                        req = Request(feat_url, headers={"User-Agent":"Mozilla/5.0"})
                        with urlopen(req, timeout=30) as r:
                            data = json.loads(r.read().decode("utf-8"))
                        compact = []
                        for ft in data.get("features", [])[:5]:
                            geom = ft.get("geometry") or {}
                            coords = geom.get("coordinates")
                            sample_coord = None
                            cur = coords
                            try:
                                while isinstance(cur, list) and cur and isinstance(cur[0], list):
                                    cur = cur[0]
                                if isinstance(cur, list) and len(cur) >= 2:
                                    sample_coord = cur[:2]
                            except Exception:
                                pass
                            compact.append({
                                "id": ft.get("id"),
                                "properties": ft.get("properties", {}),
                                "geometry_type": geom.get("type"),
                                "sample_coord": sample_coord
                            })
                        item["numberReturned"] = data.get("numberReturned")
                        item["samples"] = compact
                    except Exception as e:
                        item["feature_error"] = str(e)

                    out.append(item)

                return self.send_json({"bbox":bbox,"layers":out})
            except Exception as e:
                return self.send_json({"error":"Neizdevās iegūt kompaktos dziļumu slāņu datus","detail":str(e)},502)


        if u.path == "/api/vraa-depth-crs-test":
            try:
                import xml.etree.ElementTree as ET

                caps_url = "https://geolatvija.lv/geoserver/vraa/wfs?service=WFS&request=GetCapabilities&version=2.0.0"
                req = Request(caps_url, headers={"User-Agent":"Mozilla/5.0"})
                with urlopen(req, timeout=30) as r:
                    raw = r.read()
                root = ET.fromstring(raw)

                wanted = {
                    "vraa:msp_dziluma_apgabali",
                    "vraa:msp_dzilumatizmes",
                    "vraa:zm_dzilrakumi_103"
                }

                meta = {}
                for ft in root.iter():
                    if ft.tag.endswith("FeatureType"):
                        vals = {}
                        for ch in list(ft):
                            tag = ch.tag.split("}")[-1]
                            txt = (ch.text or "").strip()
                            if tag in ["Name","Title","DefaultCRS","DefaultSRS","OtherCRS","OtherSRS"]:
                                vals.setdefault(tag, []).append(txt)
                            if tag in ["WGS84BoundingBox","LatLongBoundingBox"]:
                                vals.setdefault(tag, []).append(ET.tostring(ch, encoding="unicode"))
                        names = vals.get("Name", [])
                        if names and names[0] in wanted:
                            meta[names[0]] = vals

                tests = []
                layer = "vraa:msp_dziluma_apgabali"
                # Test bbox with explicit CRS84 and EPSG:4326 axis variants.
                requests_to_try = [
                    ("wfs2_crs84", "2.0.0", "21.30,56.95,21.48,57.08,urn:ogc:def:crs:OGC:1.3:CRS84"),
                    ("wfs2_epsg4326_lonlat", "2.0.0", "21.30,56.95,21.48,57.08,EPSG:4326"),
                    ("wfs2_epsg4326_latlon", "2.0.0", "56.95,21.30,57.08,21.48,EPSG:4326"),
                    ("wfs11_epsg4326", "1.1.0", "21.30,56.95,21.48,57.08,EPSG:4326")
                ]
                for label, ver, bbox in requests_to_try:
                    try:
                        params = {
                            "service":"WFS","version":ver,"request":"GetFeature",
                            ("typeNames" if ver.startswith("2") else "typeName"):layer,
                            "bbox":bbox,
                            ("count" if ver.startswith("2") else "maxFeatures"):"3",
                            "outputFormat":"application/json"
                        }
                        url = "https://geolatvija.lv/geoserver/vraa/wfs?" + urlencode(params)
                        req = Request(url, headers={"User-Agent":"Mozilla/5.0"})
                        with urlopen(req, timeout=30) as r:
                            data = json.loads(r.read().decode("utf-8"))
                        compact=[]
                        for ft in data.get("features", [])[:3]:
                            geom=ft.get("geometry") or {}
                            coords=geom.get("coordinates")
                            cur=coords
                            try:
                                while isinstance(cur,list) and cur and isinstance(cur[0],list):
                                    cur=cur[0]
                            except Exception:
                                pass
                            compact.append({
                                "id":ft.get("id"),
                                "props":ft.get("properties",{}),
                                "geom_type":geom.get("type"),
                                "sample_coord":cur[:2] if isinstance(cur,list) and len(cur)>=2 else None
                            })
                        tests.append({"label":label,"url":url,"numberReturned":data.get("numberReturned"),"features":compact})
                    except Exception as e:
                        tests.append({"label":label,"error":str(e)})

                # Try depth-soundings layer on both WFS versions without bbox first.
                sounding_tests=[]
                for lname in ["vraa:msp_dzilumatizmes","vraa:zm_dzilrakumi_103"]:
                    for ver in ["2.0.0","1.1.0"]:
                        try:
                            params={
                                "service":"WFS","version":ver,"request":"GetFeature",
                                ("typeNames" if ver.startswith("2") else "typeName"):lname,
                                ("count" if ver.startswith("2") else "maxFeatures"):"3",
                                "outputFormat":"application/json"
                            }
                            url="https://geolatvija.lv/geoserver/vraa/wfs?"+urlencode(params)
                            req=Request(url,headers={"User-Agent":"Mozilla/5.0"})
                            with urlopen(req,timeout=30) as r:
                                txt=r.read().decode("utf-8",errors="ignore")
                            data=json.loads(txt)
                            compact=[]
                            for ft in data.get("features",[])[:3]:
                                compact.append({"id":ft.get("id"),"properties":ft.get("properties",{}),"geometry":ft.get("geometry")})
                            sounding_tests.append({"layer":lname,"version":ver,"numberReturned":data.get("numberReturned"),"features":compact})
                        except Exception as e:
                            sounding_tests.append({"layer":lname,"version":ver,"error":str(e)})

                return self.send_json({"metadata":meta,"bbox_tests":tests,"sounding_tests":sounding_tests})
            except Exception as e:
                return self.send_json({"error":"CRS/BBOX tests neizdevās","detail":str(e)},502)


        if u.path == "/api/vraa-depth-3059":
            try:
                layers = [
                    "vraa:msp_dziluma_apgabali",
                    "vraa:msp_dzilumatizmes",
                    "vraa:zm_dzilrakumi_103"
                ]

                # Jūrkalnes apkārtne, pārrēķināta no WGS84 uz EPSG:3059.
                bbox_3059 = "335785.44,315064.29,347264.13,329111.44,EPSG:3059"
                out = []

                for layer in layers:
                    item = {"layer":layer}
                    for ver in ["2.0.0","1.1.0"]:
                        try:
                            params = {
                                "service":"WFS",
                                "version":ver,
                                "request":"GetFeature",
                                ("typeNames" if ver.startswith("2") else "typeName"):layer,
                                "bbox":bbox_3059,
                                ("count" if ver.startswith("2") else "maxFeatures"):"10",
                                "outputFormat":"application/json",
                                "srsName":"EPSG:3059"
                            }
                            url = "https://geolatvija.lv/geoserver/vraa/wfs?" + urlencode(params)
                            req = Request(url, headers={"User-Agent":"Mozilla/5.0"})
                            with urlopen(req, timeout=30) as r:
                                data = json.loads(r.read().decode("utf-8"))

                            compact=[]
                            for ft in data.get("features",[])[:10]:
                                compact.append({
                                    "id":ft.get("id"),
                                    "properties":ft.get("properties",{}),
                                    "geometry_type":(ft.get("geometry") or {}).get("type")
                                })

                            item["wfs_"+ver] = {
                                "numberReturned":data.get("numberReturned"),
                                "samples":compact
                            }
                        except Exception as e:
                            item["wfs_"+ver] = {"error":str(e)}
                    out.append(item)

                return self.send_json({"bbox_3059":bbox_3059,"layers":out})
            except Exception as e:
                return self.send_json({"error":"EPSG:3059 dziļumu tests neizdevās","detail":str(e)},502)


        if u.path == "/api/lv-depth-zones":
            q = parse_qs(u.query)
            key = q.get("place", ["Jurkalne"])[0]
            p = PLACES.get(key)
            if not p:
                return self.send_json({"error":"Nezināma vieta"},404)

            def point_in_ring(x, y, ring):
                inside = False
                if not ring or len(ring) < 3:
                    return False
                j = len(ring) - 1
                for i in range(len(ring)):
                    xi, yi = ring[i][0], ring[i][1]
                    xj, yj = ring[j][0], ring[j][1]
                    if ((yi > y) != (yj > y)):
                        xinters = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
                        if x < xinters:
                            inside = not inside
                    j = i
                return inside

            def point_in_geom(x, y, geom):
                if not geom:
                    return False
                gtype = geom.get("type")
                coords = geom.get("coordinates") or []
                polys = coords if gtype == "MultiPolygon" else [coords] if gtype == "Polygon" else []
                for poly in polys:
                    if not poly:
                        continue
                    if point_in_ring(x, y, poly[0]):
                        in_hole = any(point_in_ring(x, y, hole) for hole in poly[1:])
                        if not in_hole:
                            return True
                return False

            try:
                url = "https://geolatvija.lv/geoserver/vraa/wfs?" + urlencode({
                    "service":"WFS","version":"2.0.0","request":"GetFeature",
                    "typeNames":"vraa:msp_dziluma_apgabali",
                    "count":"20",
                    "outputFormat":"application/json",
                    "srsName":"EPSG:4326"
                })
                req = Request(url, headers={"User-Agent":"Mozilla/5.0"})
                with urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode("utf-8"))
                features = data.get("features", [])
            except Exception as e:
                return self.send_json({"error":"Latvijas dziļuma joslas nav pieejamas","detail":str(e)},502)

            samples=[]
            for d in range(0,201,25):
                total = p["sea_offset_m"] + d
                lat, lon = west_point(p["coast_lat"], p["coast_lon"], total)
                zones=[]
                for ft in features:
                    if point_in_geom(lon, lat, ft.get("geometry")):
                        pr = ft.get("properties", {})
                        to_val = pr.get("dzil1_lidz")
                        if to_val is None:
                            to_val = pr.get("dzil_lidz")
                        if to_val is None:
                            for k,v in pr.items():
                                if "lidz" in k.lower() and v is not None:
                                    to_val = v
                                    break
                        zones.append({
                            "from":pr.get("dzilums_no"),
                            "to":to_val,
                            "id":ft.get("id")
                        })
                samples.append({
                    "distance_m":d,
                    "approx_from_coast_m":total,
                    "zones":zones,
                    "lat":lat,
                    "lon":lon
                })

            return self.send_json({
                "place":key,
                "source":"GeoLatvija VRAA WFS vraa:msp_dziluma_apgabali",
                "method":"local point-in-polygon EPSG:4326",
                "samples":samples
            })


        if u.path == "/api/lja-shp-probe":
            try:
                import io, zipfile, struct

                zip_url = "https://data.gov.lv/dati/lv/dataset/ecf2e9f0-01a5-43e1-a143-0a7d6d7a6ab5/resource/c3686a42-9b9c-41f1-8288-dbaed9e28190/download/ajd_2026.zip"
                req = Request(zip_url, headers={"User-Agent":"Mozilla/5.0"})
                with urlopen(req, timeout=60) as r:
                    raw = r.read()

                zf = zipfile.ZipFile(io.BytesIO(raw))
                names = zf.namelist()
                wanted = [n for n in names if any(k in n.lower() for k in [
                    "dzil", "dziļ", "isolin", "izolin", "sound", "depth"
                ])]

                def parse_dbf(name):
                    data = zf.read(name)
                    if len(data) < 32:
                        return {"error":"too short"}
                    num_records = int.from_bytes(data[4:8], "little")
                    header_len = int.from_bytes(data[8:10], "little")
                    record_len = int.from_bytes(data[10:12], "little")
                    fields = []
                    pos = 32
                    while pos + 32 <= header_len:
                        if data[pos] == 0x0D:
                            break
                        desc = data[pos:pos+32]
                        fname = desc[:11].split(b"\\x00",1)[0].decode("latin1", errors="ignore")
                        ftype = chr(desc[11])
                        flen = desc[16]
                        fdec = desc[17]
                        fields.append({"name":fname,"type":ftype,"length":flen,"decimals":fdec})
                        pos += 32

                    samples=[]
                    rec_start=header_len
                    for i in range(min(num_records,5)):
                        rec=data[rec_start+i*record_len:rec_start+(i+1)*record_len]
                        if not rec or rec[0:1] == b"*":
                            continue
                        off=1
                        row={}
                        for f in fields:
                            b=rec[off:off+f["length"]]
                            off += f["length"]
                            row[f["name"]] = b.decode("latin1", errors="ignore").strip()
                        samples.append(row)
                    return {"num_records":num_records,"fields":fields,"samples":samples}

                dbf_info={}
                for n in wanted:
                    if n.lower().endswith(".dbf"):
                        try:
                            dbf_info[n]=parse_dbf(n)
                        except Exception as e:
                            dbf_info[n]={"error":str(e)}

                return self.send_json({
                    "zip_bytes": len(raw),
                    "matching_files": wanted,
                    "dbf": dbf_info
                })
            except Exception as e:
                return self.send_json({"error":"Neizdevās nolasīt LJA SHP ZIP","detail":str(e)},502)


        if u.path == "/api/lja-soundings-geom":
            try:
                import io, zipfile, struct

                zip_url = "https://data.gov.lv/dati/lv/dataset/ecf2e9f0-01a5-43e1-a143-0a7d6d7a6ab5/resource/c3686a42-9b9c-41f1-8288-dbaed9e28190/download/ajd_2026.zip"
                req = Request(zip_url, headers={"User-Agent":"Mozilla/5.0"})
                with urlopen(req, timeout=60) as r:
                    raw = r.read()

                zf = zipfile.ZipFile(io.BytesIO(raw))
                shp_name = next((n for n in zf.namelist() if n.lower().endswith("soundg(pz).shp")), None)
                prj_name = next((n for n in zf.namelist() if n.lower().endswith("soundg(pz).prj")), None)
                if not shp_name:
                    return self.send_json({"error":"SOUNDG(PZ).shp nav atrasts ZIP failā"},404)

                shp = zf.read(shp_name)
                prj = zf.read(prj_name).decode("utf-8", errors="ignore") if prj_name else None

                if len(shp) < 100:
                    return self.send_json({"error":"SHP fails pārāk īss"},502)

                header_shape_type = struct.unpack("<i", shp[32:36])[0]
                xmin, ymin, xmax, ymax = struct.unpack("<4d", shp[36:68])
                zmin, zmax = struct.unpack("<2d", shp[68:84])

                records=[]
                pos=100
                while pos+8 <= len(shp) and len(records) < 20:
                    rec_no, content_words = struct.unpack(">2i", shp[pos:pos+8])
                    content_len = content_words * 2
                    content = shp[pos+8:pos+8+content_len]
                    pos += 8 + content_len
                    if len(content) < 4:
                        continue
                    st = struct.unpack("<i", content[:4])[0]
                    rec={"record":rec_no,"shape_type":st}

                    # PointZ
                    if st == 11 and len(content) >= 36:
                        x,y,z,m = struct.unpack("<4d", content[4:36])
                        rec.update({"x":x,"y":y,"z":z,"m":m})

                    # MultiPointZ
                    elif st == 18 and len(content) >= 40:
                        bxmin,bymin,bxmax,bymax = struct.unpack("<4d", content[4:36])
                        npts = struct.unpack("<i", content[36:40])[0]
                        off=40
                        pts=[]
                        for i in range(min(npts,50)):
                            if off+16>len(content): break
                            x,y=struct.unpack("<2d",content[off:off+16])
                            pts.append([x,y])
                            off+=16
                        zvals=[]
                        if off+16<=len(content):
                            zrmin,zrmax=struct.unpack("<2d",content[off:off+16]); off+=16
                            for i in range(min(npts,50)):
                                if off+8>len(content): break
                                zvals.append(struct.unpack("<d",content[off:off+8])[0])
                                off+=8
                        rec.update({
                            "num_points":npts,
                            "bbox":[bxmin,bymin,bxmax,bymax],
                            "points_xyz":[[pts[i][0],pts[i][1],zvals[i] if i<len(zvals) else None] for i in range(min(len(pts),len(zvals) or len(pts)))]
                        })
                    records.append(rec)

                return self.send_json({
                    "shp_name":shp_name,
                    "prj_name":prj_name,
                    "prj":prj,
                    "header_shape_type":header_shape_type,
                    "bbox":[xmin,ymin,xmax,ymax],
                    "z_range":[zmin,zmax],
                    "records":records
                })
            except Exception as e:
                return self.send_json({"error":"Neizdevās nolasīt LJA SOUNDG ģeometriju","detail":str(e)},502)


        if u.path == "/api/lja-jurkalne-nearest":
            try:
                import io, zipfile, struct, math

                zip_url = "https://data.gov.lv/dati/lv/dataset/ecf2e9f0-01a5-43e1-a143-0a7d6d7a6ab5/resource/c3686a42-9b9c-41f1-8288-dbaed9e28190/download/ajd_2026.zip"
                req = Request(zip_url, headers={"User-Agent":"Mozilla/5.0"})
                with urlopen(req, timeout=60) as r:
                    raw = r.read()

                zf = zipfile.ZipFile(io.BytesIO(raw))
                shp_name = next((n for n in zf.namelist() if n.lower().endswith("soundg(pz).shp")), None)
                if not shp_name:
                    return self.send_json({"error":"SOUNDG(PZ).shp nav atrasts"},404)

                shp = zf.read(shp_name)

                # WGS84 World Mercator / Mercator variant A (ellipsoidal), matching the PRJ.
                a_ell = 6378137.0
                e = 0.08181919084262149
                def lonlat_to_world_mercator(lon, lat):
                    x = a_ell * math.radians(lon)
                    phi = math.radians(lat)
                    sinp = math.sin(phi)
                    y = a_ell * math.log(
                        math.tan(math.pi/4 + phi/2) *
                        ((1 - e*sinp)/(1 + e*sinp))**(e/2)
                    )
                    return x,y

                p = PLACES["Jurkalne"]
                coast_x, coast_y = lonlat_to_world_mercator(p["coast_lon"], p["coast_lat"])

                pts=[]
                pos=100
                while pos+8 <= len(shp):
                    rec_no, content_words = struct.unpack(">2i", shp[pos:pos+8])
                    content_len = content_words * 2
                    content = shp[pos+8:pos+8+content_len]
                    pos += 8 + content_len
                    if len(content) < 28:
                        continue
                    st = struct.unpack("<i", content[:4])[0]
                    if st != 11:
                        continue
                    x,y,z = struct.unpack("<3d", content[4:28])
                    m = None
                    if len(content) >= 36:
                        try:
                            m = struct.unpack("<d", content[28:36])[0]
                        except Exception:
                            pass
                    dx=x-coast_x; dy=y-coast_y
                    dist=math.hypot(dx,dy)
                    pts.append({
                        "record":rec_no,
                        "x":x,"y":y,"z":z,"m":m,
                        "distance_from_coast_m":dist,
                        "west_of_coast_m":coast_x-x,
                        "north_offset_m":y-coast_y
                    })

                pts.sort(key=lambda r:r["distance_from_coast_m"])
                nearby=[r for r in pts if r["distance_from_coast_m"] <= 5000][:100]

                return self.send_json({
                    "place":"Jūrkalne",
                    "coast_lon":p["coast_lon"],
                    "coast_lat":p["coast_lat"],
                    "coast_world_mercator":[coast_x,coast_y],
                    "total_soundings":len(pts),
                    "nearest_20":pts[:20],
                    "within_5km_count":len([r for r in pts if r["distance_from_coast_m"] <= 5000]),
                    "within_5km_first_100":nearby
                })
            except Exception as e:
                return self.send_json({"error":"Neizdevās atrast Jūrkalnei tuvākās LJA dziļumatzīmes","detail":str(e)},502)


        if u.path == "/api/lja-soundings-files":
            try:
                import io, zipfile
                zip_url = "https://data.gov.lv/dati/lv/dataset/ecf2e9f0-01a5-43e1-a143-0a7d6d7a6ab5/resource/c3686a42-9b9c-41f1-8288-dbaed9e28190/download/ajd_2026.zip"
                req = Request(zip_url, headers={"User-Agent":"Mozilla/5.0"})
                with urlopen(req, timeout=60) as r:
                    raw = r.read()

                zf = zipfile.ZipFile(io.BytesIO(raw))
                names = zf.namelist()

                sound_files = [n for n in names if "soundg" in n.lower()]
                depth_files = [n for n in names if any(k in n.lower() for k in [
                    "soundg","depth","depare","depcnt","drgare","dregare","m_srel","isolin","contour"
                ])]

                shp_only = [n for n in sound_files if n.lower().endswith(".shp")]

                return self.send_json({
                    "zip_file_count": len(names),
                    "soundg_files": sound_files,
                    "soundg_shp_files": shp_only,
                    "other_depth_related_files": depth_files
                })
            except Exception as e:
                return self.send_json({"error":"Neizdevās uzskaitīt LJA dziļuma failus","detail":str(e)},502)


        if u.path == "/api/lja-notice-probe":
            try:
                import re
                base = "https://notice.lja.lv/"
                req = Request(base, headers={"User-Agent":"Mozilla/5.0"})
                with urlopen(req, timeout=30) as r:
                    html = r.read().decode("utf-8", errors="ignore")

                scripts = re.findall(r'<script[^>]+src=["\\\']([^"\\\']+)["\\\']', html, re.I)
                links = re.findall(r'<link[^>]+href=["\\\']([^"\\\']+)["\\\']', html, re.I)

                def absurl(s):
                    if s.startswith("http"):
                        return s
                    if s.startswith("//"):
                        return "https:" + s
                    return base.rstrip("/") + "/" + s.lstrip("/")

                script_urls = [absurl(s) for s in scripts]
                candidates = []
                snippets = {}

                terms = ["primar","enc","s-57","s57","s-100","s100","wms","wmts","tile","tiles","leaflet","maplibre","arcgis","geoserver"]

                # Also inspect inline HTML.
                lowhtml = html.lower()
                for term in terms:
                    if term in lowhtml:
                        idx = lowhtml.find(term)
                        snippets.setdefault("html", []).append(html[max(0,idx-300):idx+700])

                for su in script_urls[:25]:
                    try:
                        req2 = Request(su, headers={"User-Agent":"Mozilla/5.0"})
                        with urlopen(req2, timeout=30) as r2:
                            js = r2.read().decode("utf-8", errors="ignore")

                        # URLs
                        for m in re.findall(r'https?://[^"\\\'\\s)]+', js):
                            ml = m.lower()
                            if any(t in ml for t in terms):
                                if m not in candidates:
                                    candidates.append(m)

                        # Relative API/tile/service paths.
                        for m in re.findall(r'["\\\'](/[^"\\\']{2,220})["\\\']', js):
                            ml = m.lower()
                            if any(t in ml for t in terms):
                                au = absurl(m)
                                if au not in candidates:
                                    candidates.append(au)

                        # Context around key terms.
                        low = js.lower()
                        for term in terms:
                            start=0
                            found=[]
                            while True:
                                i=low.find(term,start)
                                if i<0: break
                                found.append(js[max(0,i-250):min(len(js),i+650)])
                                start=i+len(term)
                                if len(found)>=8: break
                            if found:
                                snippets[su+"::"+term]=found
                    except Exception as e:
                        snippets[su+"::error"]=[str(e)]

                return self.send_json({
                    "page": base,
                    "scripts": script_urls,
                    "links": [absurl(x) for x in links],
                    "candidates": candidates[:300],
                    "snippets": snippets
                })
            except Exception as e:
                return self.send_json({"error":"Neizdevās izpētīt notice.lja.lv","detail":str(e)},502)


        if u.path == "/api/lja-primar-wms":
            try:
                import re
                base = "https://notice.lja.lv/"
                req0 = Request(base, headers={"User-Agent":"Mozilla/5.0"})
                with urlopen(req0, timeout=30) as r0:
                    html = r0.read().decode("utf-8", errors="ignore")

                scripts = re.findall(r'<script[^>]+src=["\\\']([^"\\\']+)["\\\']', html, re.I)
                app_src = None
                for s in scripts:
                    if "app.js" in s:
                        app_src = s
                        break
                if not app_src:
                    return self.send_json({"error":"Aktuālais app.js nav atrasts","scripts":scripts},404)

                if app_src.startswith("http"):
                    target = app_src
                elif app_src.startswith("//"):
                    target = "https:" + app_src
                else:
                    target = base.rstrip("/") + "/" + app_src.lstrip("/")

                req = Request(target, headers={"User-Agent":"Mozilla/5.0"})
                with urlopen(req, timeout=30) as r:
                    js = r.read().decode("utf-8", errors="ignore")

                terms = [
                    "toggleS57Layer","toggleS100Layer","toggleENCLayer",
                    "PRIMAR","wms","tileLayer.wms","L.tileLayer.wms",
                    "layers:","GetMap","GetCapabilities"
                ]
                snippets={}
                low=js.lower()
                for term in terms:
                    t=term.lower()
                    arr=[]
                    startpos=0
                    while True:
                        i=low.find(t,startpos)
                        if i<0: break
                        arr.append(js[max(0,i-500):min(len(js),i+1800)])
                        startpos=i+len(t)
                        if len(arr)>=20: break
                    if arr:
                        snippets[term]=arr

                urls=[]
                for m in re.findall(r'https?://[^"\\\'\\s)]+', js):
                    ml=m.lower()
                    if any(k in ml for k in ["wms","primar","enc","s57","s100","map"]):
                        if m not in urls:
                            urls.append(m)

                return self.send_json({
                    "script":target,
                    "script_length":len(js),
                    "urls":urls[:100],
                    "snippets":snippets
                })
            except Exception as e:
                return self.send_json({"error":"Neizdevās izvilkt PRIMAR WMS konfigurāciju","detail":str(e)},502)


        if u.path == "/api/lja-inline-wms":
            try:
                import re
                base = "https://notice.lja.lv/"
                req = Request(base, headers={"User-Agent":"Mozilla/5.0"})
                with urlopen(req, timeout=30) as r:
                    html = r.read().decode("utf-8", errors="ignore")

                inline = re.findall(r'<script(?![^>]+src=)[^>]*>(.*?)</script>', html, re.I | re.S)
                combined = "\n\n".join(inline)

                terms = ["toggleS57Layer","toggleS100Layer","toggleENCLayer","PRIMAR","wms","tileLayer.wms","L.tileLayer.wms","GetMap","GetCapabilities"]
                snippets={}
                low=combined.lower()
                for term in terms:
                    t=term.lower()
                    arr=[]
                    start=0
                    while True:
                        i=low.find(t,start)
                        if i<0: break
                        arr.append(combined[max(0,i-800):min(len(combined),i+2500)])
                        start=i+len(t)
                        if len(arr)>=20: break
                    if arr:
                        snippets[term]=arr

                urls=[]
                for m in re.findall(r'https?://[^"\\\'\\s)]+', combined):
                    ml=m.lower()
                    if any(k in ml for k in ["wms","primar","enc","s57","s100","map"]):
                        if m not in urls:
                            urls.append(m)

                return self.send_json({
                    "inline_script_count":len(inline),
                    "combined_length":len(combined),
                    "urls":urls[:100],
                    "snippets":snippets
                })
            except Exception as e:
                return self.send_json({"error":"Neizdevās analizēt inline WMS skriptus","detail":str(e)},502)


        if u.path == "/api/lja-wms-featureinfo":
            try:
                import math
                p = PLACES["Jurkalne"]

                # Web Mercator helpers
                R=6378137.0
                def merc(lon,lat):
                    x=R*math.radians(lon)
                    y=R*math.log(math.tan(math.pi/4+math.radians(lat)/2))
                    return x,y

                cx,cy=merc(p["coast_lon"],p["coast_lat"])
                half=5000
                bbox=f"{cx-half},{cy-half},{cx+half},{cy+half}"

                base="https://notice.lja.lv/wms_proxy.php"
                tests=[]
                for style in ["style-id-263","style-id-3135"]:
                    common={
                        "service":"WMS",
                        "version":"1.1.1",
                        "request":"GetMap",
                        "layers":"cells",
                        "styles":style,
                        "srs":"EPSG:3857",
                        "bbox":bbox,
                        "width":"800",
                        "height":"800",
                        "format":"image/png",
                        "transparent":"true"
                    }
                    map_url=base+"?"+urlencode(common)
                    item={"style":style,"map_url":map_url}
                    try:
                        req=Request(map_url,headers={"User-Agent":"Mozilla/5.0"})
                        with urlopen(req,timeout=30) as r:
                            raw=r.read()
                            item["map_status"]=getattr(r,"status",200)
                            item["map_content_type"]=r.headers.get("Content-Type")
                            item["map_bytes"]=len(raw)
                    except Exception as e:
                        item["map_error"]=str(e)

                    fi=common.copy()
                    fi.update({
                        "request":"GetFeatureInfo",
                        "query_layers":"cells",
                        "info_format":"text/plain",
                        "x":"400","y":"400",
                        "feature_count":"20"
                    })
                    fi_url=base+"?"+urlencode(fi)
                    item["featureinfo_url"]=fi_url
                    try:
                        req=Request(fi_url,headers={"User-Agent":"Mozilla/5.0"})
                        with urlopen(req,timeout=30) as r:
                            txt=r.read().decode("utf-8",errors="ignore")
                            item["featureinfo_status"]=getattr(r,"status",200)
                            item["featureinfo_content_type"]=r.headers.get("Content-Type")
                            item["featureinfo_preview"]=txt[:5000]
                    except Exception as e:
                        item["featureinfo_error"]=str(e)
                    tests.append(item)

                return self.send_json({
                    "place":"Jūrkalne",
                    "bbox_3857":bbox,
                    "tests":tests
                })
            except Exception as e:
                return self.send_json({"error":"LJA WMS FeatureInfo tests neizdevās","detail":str(e)},502)


        if u.path == "/api/lja-wms-depth-scan":
            try:
                import math, re
                p = PLACES["Jurkalne"]
                R=6378137.0

                def merc(lon,lat):
                    x=R*math.radians(lon)
                    y=R*math.log(math.tan(math.pi/4+math.radians(lat)/2))
                    return x,y

                cx,cy=merc(p["coast_lon"],p["coast_lat"])

                # 2 km wide/high box centered on coast, 1000x1000 px => ~2 m/px.
                half=1000.0
                width=1000
                height=1000
                bbox=f"{cx-half},{cy-half},{cx+half},{cy+half}"

                base="https://notice.lja.lv/wms_proxy.php"
                keywords=[
                    "soundg","sounding","depth","depare","depcnt",
                    "valsou","drval1","drval2","quasou","tecsou"
                ]

                results=[]
                for style,label in [("style-id-263","S57"),("style-id-3135","S100")]:
                    hits=[]
                    raw_samples=[]

                    # Scan transect 40..340 m west of coast every 20 m.
                    for dist in range(40,341,20):
                        # west is smaller x
                        px = int(round(width/2 - dist/(2*half)*width))
                        py = height//2

                        params={
                            "service":"WMS",
                            "version":"1.1.1",
                            "request":"GetFeatureInfo",
                            "layers":"cells",
                            "query_layers":"cells",
                            "styles":style,
                            "srs":"EPSG:3857",
                            "bbox":bbox,
                            "width":str(width),
                            "height":str(height),
                            "format":"image/png",
                            "transparent":"true",
                            "info_format":"text/plain",
                            "x":str(px),
                            "y":str(py),
                            "feature_count":"50"
                        }
                        url=base+"?"+urlencode(params)
                        try:
                            req=Request(url,headers={"User-Agent":"Mozilla/5.0"})
                            with urlopen(req,timeout=20) as r:
                                txt=r.read().decode("utf-8",errors="ignore")
                            low=txt.lower()
                            matched=[k for k in keywords if k in low]
                            if matched:
                                hits.append({
                                    "distance_from_coast_m":dist,
                                    "pixel":[px,py],
                                    "matched_keywords":matched,
                                    "preview":txt[:4000]
                                })
                            elif len(raw_samples)<4 and txt.strip():
                                raw_samples.append({
                                    "distance_from_coast_m":dist,
                                    "preview":txt[:1200]
                                })
                        except Exception as e:
                            if len(raw_samples)<4:
                                raw_samples.append({
                                    "distance_from_coast_m":dist,
                                    "error":str(e)
                                })

                    results.append({
                        "style":style,
                        "label":label,
                        "hits":hits,
                        "nondepth_samples":raw_samples
                    })

                return self.send_json({
                    "place":"Jūrkalne",
                    "bbox_3857":bbox,
                    "scan":"40–340 m west of coast, step 20 m",
                    "results":results
                })
            except Exception as e:
                return self.send_json({"error":"LJA WMS dziļuma skenēšana neizdevās","detail":str(e)},502)

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
