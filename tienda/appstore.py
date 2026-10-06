#!/usr/bin/env python3
"""App Store Connect from the terminal and from CI, the twin of play.py. Key id, issuer and key come
from APPSTORE_KEY_ID, APPSTORE_ISSUER_ID and APPSTORE_PRIVATE_KEY (CI), or else from the Keychain
(dev.baltajmn.appstore-*) and ~/keys/appstore-api.p8. Nothing is printed that is secret.

    appstore.py estado  <bundle id>                    what is missing before sending to review
    appstore.py ficha   <bundle id> <repo> [--dry]     store/app-store/<locale>/*.txt to the version
    appstore.py capturas <bundle id> <repo> [--dry]    store/screenshots/iphone/<locale>/*.png, 6,9"
    appstore.py captura-compra <bundle id> <product id> <png>   review screenshot of a purchase
    appstore.py adjunto <bundle id> <file>             attachment for App Review (screen recording)
    appstore.py sin-china <bundle id>                  mainland China off (it asks for an ICP number)
    appstore.py version <bundle id> <x.y.z>            version string of the version in preparation
    appstore.py build <bundle id> <build number>       attach a processed build to that version
    Purchases: compra.py, which sets the same price and texts in both stores.

`ficha` creates the missing locales and patches the ones that differ; support and privacy URLs are
the ones the en-US locale already has. `capturas` only fills locales that have no 6,9" screenshots,
it never deletes. Folder names may carry a region Apple does not use (it-IT is `it`). Sending to
review stays in the web: the first non-consumable purchase of an app can only go in the same
submission as a version, and the API does not add it.
"""
import base64, hashlib, io, json, os, pathlib, subprocess, sys, tempfile, time, urllib.error, urllib.request

BASE = "https://api.appstoreconnect.apple.com"
_tok, _exp = None, 0
ASC_LOCALES = {"ar-SA", "ca", "cs", "da", "de-DE", "el", "en-AU", "en-CA", "en-GB", "en-US", "es-ES",
               "es-MX", "fi", "fr-CA", "fr-FR", "he", "hi", "hr", "hu", "id", "it", "ja", "ko", "ms",
               "nl-NL", "no", "pl", "pt-BR", "pt-PT", "ro", "ru", "sk", "sv", "th", "tr", "uk", "vi",
               "zh-Hans", "zh-Hant"}


def asc_locale(folder):
    return folder if folder in ASC_LOCALES else folder.split("-")[0]


def _sec(name):
    env = os.environ.get(name.upper().replace("-", "_"))  # appstore-key-id is APPSTORE_KEY_ID
    if env:
        return env
    return subprocess.run(["security", "find-generic-password", "-a", os.environ["USER"], "-s",
                           "dev.baltajmn." + name, "-w"], capture_output=True, text=True,
                          check=True).stdout.strip()


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _token():
    global _tok, _exp
    if _tok and time.time() < _exp - 60:
        return _tok
    now = int(time.time())
    h = _b64(json.dumps({"alg": "ES256", "kid": _sec("appstore-key-id"), "typ": "JWT"}).encode())
    c = _b64(json.dumps({"iss": _sec("appstore-issuer-id"), "iat": now, "exp": now + 1200,
                         "aud": "appstoreconnect-v1"}).encode())
    # mkstemp creates the file 0600, and it is gone before the token leaves this function.
    with tempfile.NamedTemporaryFile("w", suffix=".p8") as key:
        key.write(os.environ.get("APPSTORE_PRIVATE_KEY")
                  or pathlib.Path(os.path.expanduser("~/keys/appstore-api.p8")).read_text())
        key.flush()
        d = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key.name],
                           input=f"{h}.{c}".encode(), capture_output=True, check=True).stdout
    # ES256 wants r||s, openssl gives DER.
    i = 2 if d[1] < 0x80 else 2 + (d[1] & 0x7F)
    r = d[i + 2:i + 2 + d[i + 1]]; i += 2 + d[i + 1]
    s = d[i + 2:i + 2 + d[i + 1]]
    sig = r[-32:].rjust(32, b"\0") + s[-32:].rjust(32, b"\0")
    _tok, _exp = f"{h}.{c}.{_b64(sig)}", now + 1200
    return _tok


def api(method, path, body=None):
    req = urllib.request.Request(path if path.startswith("http") else BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + _token(),
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            out = r.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')[:1500]}")
    return json.loads(out) if out else {}


def get_all(path):
    out = []
    while path:
        d = api("GET", path)
        out += d.get("data", [])
        path = d.get("links", {}).get("next")
    return out


def app_of(bundle):
    apps = get_all(f"/v1/apps?filter[bundleId]={bundle}")
    if not apps:
        sys.exit(f"{bundle}: no está en App Store Connect")
    return apps[0]


def editable(app_id):
    vs = get_all(f"/v1/apps/{app_id}/appStoreVersions?filter[platform]=IOS&filter[appStoreState]="
                 "PREPARE_FOR_SUBMISSION,DEVELOPER_REJECTED,REJECTED,METADATA_REJECTED")
    infos = [i for i in get_all(f"/v1/apps/{app_id}/appInfos")
             if i["attributes"].get("appStoreState") != "READY_FOR_DISTRIBUTION"]
    if not vs:
        sys.exit("No hay versión en preparación: créala en App Store Connect o con la API")
    return vs[0], infos[0]


def estado(bundle):
    app = app_of(bundle); a = app["id"]
    print(f"{app['attributes']['name']} ({a}), idioma principal {app['attributes']['primaryLocale']}")
    for v in get_all(f"/v1/apps/{a}/appStoreVersions?filter[platform]=IOS"):
        x = v["attributes"]
        b = api("GET", f"/v1/appStoreVersions/{v['id']}/build?fields[builds]=version").get("data")
        print(f"  versión {x['versionString']}: {x['appStoreState']}, build "
              f"{b['attributes']['version'] if b else 'NINGUNA'}, publicación {x['releaseType']}")
    v, info = editable(a) if any(s["attributes"]["appStoreState"] == "PREPARE_FOR_SUBMISSION"
                                 for s in get_all(f"/v1/apps/{a}/appStoreVersions?filter[platform]=IOS")) else (None, None)
    if v:
        locs = get_all(f"/v1/appStoreVersions/{v['id']}/appStoreVersionLocalizations")
        shots = [l["attributes"]["locale"] for l in locs
                 if get_all(f"/v1/appStoreVersionLocalizations/{l['id']}/appScreenshotSets")]
        print(f"  idiomas {len(locs)}, sin descripción {[l['attributes']['locale'] for l in locs if not l['attributes'].get('description')]}"
              f", con capturas {shots or 'NINGUNO'}")
    builds = get_all(f"/v1/builds?filter[app]={a}&fields[builds]=version,processingState&limit=10")
    print("  builds:", [(b["attributes"]["version"], b["attributes"]["processingState"]) for b in builds] or "ninguna")
    for p in get_all(f"/v1/apps/{a}/inAppPurchasesV2?fields[inAppPurchases]=productId,state"):
        n = len(get_all(f"/v2/inAppPurchases/{p['id']}/inAppPurchaseLocalizations"))
        print(f"  compra {p['attributes']['productId']}: {p['attributes']['state']}, {n} idiomas")
    for s in get_all(f"/v1/reviewSubmissions?filter[app]={a}"):
        items = get_all(f"/v1/reviewSubmissions/{s['id']}/items")
        print(f"  envío {s['attributes']['state']} {s['attributes'].get('submittedDate') or ''}, {len(items)} elementos")
    ta = get_all(f"/v2/appAvailabilities/{a}/territoryAvailabilities?fields[territoryAvailabilities]="
                 "available,contentStatuses,territory&include=territory&limit=200")
    off = [t["relationships"]["territory"]["data"]["id"] for t in ta if not t["attributes"]["available"]]
    eu = sum("TRADER_STATUS_NOT_PROVIDED" in (t["attributes"].get("contentStatuses") or []) for t in ta)
    print(f"  países: {len(ta)}, no disponibles {off or 'ninguno'}"
          + (f", {eu} sin estado de comerciante (UE)" if eu else ""))


def _read(p):
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


def _upsert(kind, parent, parent_id, existing, locale, attrs, dry):
    cur = existing.get(locale)
    if cur:
        diff = {k: v for k, v in attrs.items() if (cur["attributes"].get(k) or None) != v}
        if diff:
            print(f"  {locale} {kind}: cambia {sorted(diff)}")
            if not dry:
                api("PATCH", f"/v1/{kind}/{cur['id']}", {"data": {"type": kind, "id": cur["id"], "attributes": diff}})
    else:
        print(f"  {locale} {kind}: nuevo")
        if not dry:
            api("POST", f"/v1/{kind}", {"data": {"type": kind, "attributes": {"locale": locale, **attrs},
                                                 "relationships": {parent: {"data": {"type": parent + "s", "id": parent_id}}}}})


def ficha(bundle, repo, dry):
    v, info = editable(app_of(bundle)["id"])
    vlocs = {l["attributes"]["locale"]: l for l in get_all(f"/v1/appStoreVersions/{v['id']}/appStoreVersionLocalizations")}
    ilocs = {l["attributes"]["locale"]: l for l in get_all(f"/v1/appInfos/{info['id']}/appInfoLocalizations")}
    base_v = (vlocs.get("en-US") or next(iter(vlocs.values())))["attributes"]
    base_i = (ilocs.get("en-US") or next(iter(ilocs.values())))["attributes"]
    for d in sorted(p for p in (pathlib.Path(repo) / "store/app-store").iterdir() if p.is_dir()):
        loc = asc_locale(d.name)
        texts = {"name": _read(d / "name.txt"), "subtitle": _read(d / "subtitle.txt"),
                 "privacyPolicyUrl": base_i.get("privacyPolicyUrl")}
        _upsert("appInfoLocalizations", "appInfo", info["id"], ilocs, loc,
                {k: x for k, x in texts.items() if x}, dry)
        # A new app info locale makes Apple create the version locale too, empty.
        if loc not in vlocs and not dry:
            vlocs = {l["attributes"]["locale"]: l for l in get_all(f"/v1/appStoreVersions/{v['id']}/appStoreVersionLocalizations")}
        texts = {"description": _read(d / "description.txt"), "keywords": _read(d / "keywords.txt"),
                 "promotionalText": _read(d / "promo.txt"), "supportUrl": base_v.get("supportUrl")}
        _upsert("appStoreVersionLocalizations", "appStoreVersion", v["id"], vlocs, loc,
                {k: x for k, x in texts.items() if x}, dry)


def _png(path):
    """Apple refuses images with an alpha channel, and simulator captures have one."""
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[25] in (4, 6):  # PNG colour types with alpha
        from PIL import Image
        buf = io.BytesIO()
        Image.open(path).convert("RGB").save(buf, "PNG")
        data = buf.getvalue()
    return data


def upload(kind, relationships, path):
    """Reserve the asset, PUT the parts Apple asks for, and commit it with its MD5."""
    data = _png(path)
    r = api("POST", f"/v1/{kind}", {"data": {"type": kind, "attributes": {"fileName": path.name, "fileSize": len(data)},
                                             "relationships": relationships}})["data"]
    for op in r["attributes"]["uploadOperations"]:
        req = urllib.request.Request(op["url"], data=data[op["offset"]:op["offset"] + op["length"]], method=op["method"],
                                     headers={h["name"]: h["value"] for h in op["requestHeaders"]})
        urllib.request.urlopen(req, timeout=300).read()
    api("PATCH", f"/v1/{kind}/{r['id']}", {"data": {"type": kind, "id": r["id"], "attributes": {
        "uploaded": True, "sourceFileChecksum": hashlib.md5(data).hexdigest()}}})


def capturas(bundle, repo, dry):
    v, _ = editable(app_of(bundle)["id"])
    locs = {l["attributes"]["locale"]: l for l in get_all(f"/v1/appStoreVersions/{v['id']}/appStoreVersionLocalizations")}
    for d in sorted(p for p in (pathlib.Path(repo) / "store/screenshots/iphone").iterdir() if p.is_dir()):
        pngs, loc = sorted(d.glob("*.png")), locs.get(asc_locale(d.name))
        if not pngs or not loc:
            print(f"  {d.name}: {'sin capturas' if not pngs else 'la versión no tiene ese idioma'}")
            continue
        sets = {s["attributes"]["screenshotDisplayType"]: s
                for s in get_all(f"/v1/appStoreVersionLocalizations/{loc['id']}/appScreenshotSets")}
        s = sets.get("APP_IPHONE_67")
        if s and get_all(f"/v1/appScreenshotSets/{s['id']}/appScreenshots"):
            print(f"  {d.name}: ya tiene capturas de 6,9\", no se tocan")
            continue
        print(f"  {d.name}: sube {len(pngs)}")
        if dry:
            continue
        if not s:
            s = api("POST", "/v1/appScreenshotSets", {"data": {
                "type": "appScreenshotSets", "attributes": {"screenshotDisplayType": "APP_IPHONE_67"},
                "relationships": {"appStoreVersionLocalization": {"data": {"type": "appStoreVersionLocalizations", "id": loc["id"]}}}}})["data"]
        for p in pngs:
            upload("appScreenshots", {"appScreenshotSet": {"data": {"type": "appScreenshotSets", "id": s["id"]}}}, p)


def captura_compra(bundle, product, png):
    a = app_of(bundle)["id"]
    p = next((x for x in get_all(f"/v1/apps/{a}/inAppPurchasesV2") if x["attributes"]["productId"] == product), None)
    if not p:
        sys.exit(f"{product}: no existe en {bundle}")
    if api("GET", f"/v2/inAppPurchases/{p['id']}/appStoreReviewScreenshot")["data"]:
        print(f"{product}: ya tiene captura para la revisión")
        return
    upload("inAppPurchaseAppStoreReviewScreenshots",
           {"inAppPurchaseV2": {"data": {"type": "inAppPurchases", "id": p["id"]}}}, pathlib.Path(png))
    print(f"{product}: captura para la revisión subida")


def adjunto(bundle, path):
    """A file for App Review, such as the screen recording a new account is asked for (2.1)."""
    v, _ = editable(app_of(bundle)["id"])
    rd = api("GET", f"/v1/appStoreVersions/{v['id']}/appStoreReviewDetail")["data"]
    upload("appStoreReviewAttachments",
           {"appStoreReviewDetail": {"data": {"type": "appStoreReviewDetails", "id": rd["id"]}}}, pathlib.Path(path))
    print(f"{bundle}: {pathlib.Path(path).name} adjunto a la revisión de {v['attributes']['versionString']}")


def sin_china(bundle):
    a = app_of(bundle)["id"]
    ta = get_all(f"/v2/appAvailabilities/{a}/territoryAvailabilities?fields[territoryAvailabilities]="
                 "available,territory&include=territory&limit=200")
    t = next(x for x in ta if x["relationships"]["territory"]["data"]["id"] == "CHN")
    if t["attributes"]["available"]:
        api("PATCH", f"/v1/territoryAvailabilities/{t['id']}", {"data": {
            "type": "territoryAvailabilities", "id": t["id"], "attributes": {"available": False}}})
    print(f"{bundle}: China continental no disponible")


def build(bundle, number):
    a = app_of(bundle)["id"]
    v, _ = editable(a)
    b = get_all(f"/v1/builds?filter[app]={a}&filter[version]={number}&filter[preReleaseVersion.version]="
                f"{v['attributes']['versionString']}&fields[builds]=processingState")
    if not b or b[0]["attributes"]["processingState"] != "VALID":
        sys.exit(f"build {number} de {v['attributes']['versionString']}: {b[0]['attributes']['processingState'] if b else 'no subida'}")
    api("PATCH", f"/v1/appStoreVersions/{v['id']}/relationships/build", {"data": {"type": "builds", "id": b[0]["id"]}})
    print(f"{bundle}: build {number} en la versión {v['attributes']['versionString']}")


def version(bundle, number):
    v, _ = editable(app_of(bundle)["id"])
    api("PATCH", f"/v1/appStoreVersions/{v['id']}",
        {"data": {"type": "appStoreVersions", "id": v["id"], "attributes": {"versionString": number}}})
    print(f"versión en preparación: {v['attributes']['versionString']} pasa a {number}")


def purchase(bundle, product):
    a = app_of(bundle)["id"]
    p = next((x for x in get_all(f"/v1/apps/{a}/inAppPurchasesV2") if x["attributes"]["productId"] == product), None)
    if not p:
        sys.exit(f"{product}: no existe en {bundle}")
    return p["id"]


def purchase_texts(purchase):
    """{locale: (name, description)} of a purchase."""
    return {l["attributes"]["locale"]: (l["attributes"]["name"], l["attributes"].get("description") or "")
            for l in get_all(f"/v2/inAppPurchases/{purchase}/inAppPurchaseLocalizations")}


def price_point(purchase, spain_price):
    """The price point that costs `spain_price` (with VAT) in Spain."""
    pts = get_all(f"/v2/inAppPurchases/{purchase}/pricePoints?filter[territory]=ESP&limit=200")
    pp = next((x for x in pts if float(x["attributes"]["customerPrice"]) == float(spain_price)), None)
    if not pp:
        sys.exit(f"Apple no tiene un precio de {spain_price} EUR en España")
    return pp["id"]


def equalized(point):
    """What Apple charges in every territory for that price point: {ESP: ('1.99', 'EUR'), ...}."""
    out, path = {}, f"/v1/inAppPurchasePricePoints/{point}/equalizations?include=territory&limit=200"
    while path:
        d = api("GET", path)
        cur = {t["id"]: t["attributes"]["currency"] for t in d.get("included", []) if t["type"] == "territories"}
        for x in d["data"]:
            t = x["relationships"]["territory"]["data"]["id"]
            out[t] = (x["attributes"]["customerPrice"], cur[t])
        path = d.get("links", {}).get("next")
    return out


def set_price(purchase, point):
    """Spain as the base territory at that price point; Apple equalizes every other territory."""
    api("POST", "/v1/inAppPurchasePriceSchedules", {
        "data": {"type": "inAppPurchasePriceSchedules", "relationships": {
            "inAppPurchase": {"data": {"type": "inAppPurchases", "id": purchase}},
            "baseTerritory": {"data": {"type": "territories", "id": "ESP"}},
            "manualPrices": {"data": [{"type": "inAppPurchasePrices", "id": "${base}"}]}}},
        "included": [{"type": "inAppPurchasePrices", "id": "${base}", "attributes": {"startDate": None},
                      "relationships": {
                          "inAppPurchaseV2": {"data": {"type": "inAppPurchases", "id": purchase}},
                          "inAppPurchasePricePoint": {"data": {"type": "inAppPurchasePricePoints", "id": point}}}}]})


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if x != "--dry"]
    if len(args) >= 2 and args[0] == "estado":
        estado(args[1])
    elif len(args) >= 3 and args[0] == "ficha":
        ficha(args[1], args[2], "--dry" in sys.argv)
    elif len(args) >= 3 and args[0] == "capturas":
        capturas(args[1], args[2], "--dry" in sys.argv)
    elif len(args) >= 4 and args[0] == "captura-compra":
        captura_compra(args[1], args[2], args[3])
    elif len(args) >= 3 and args[0] == "adjunto":
        adjunto(args[1], args[2])
    elif len(args) >= 2 and args[0] == "sin-china":
        sin_china(args[1])
    elif len(args) >= 3 and args[0] == "version":
        version(args[1], args[2])
    elif len(args) >= 3 and args[0] == "build":
        build(args[1], args[2])
    else:
        print(__doc__)
