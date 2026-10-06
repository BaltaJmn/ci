#!/usr/bin/env python3
"""Google Play from the terminal and from CI, the twin of appstore.py. The service account that
publishes comes from PLAY_SERVICE_ACCOUNT_JSON (its content, in CI) or ~/keys/play-service-account.json.
Nothing secret is printed. Production releases never start here: that stays a click in Play Console.

    play.py estado <package>                                tracks, AABs and listing languages (read only)
    play.py ficha  <package> <repo> <web> [--dry]           listings, graphics, screenshots and contact
    play.py aab    <package> <aab> <track> <repo> [draft]   AAB to internal, alpha or beta, notes from
                                                            <repo>/store/whatsnew

Every write goes in one edit, which is atomic: either the whole listing changes or nothing does.
`--dry` builds the edit, has Play validate it and throws it away. The layout `ficha` reads is the one
README.md of BaltaJmn/ci describes: store/listings/<locale>, store/play/icon-512.png,
store/play/feature/<locale>.png and store/screenshots/play/<locale>/*.png. Purchases: compra.py,
which gives a purchase the same price and texts in both stores.
"""
import base64, json, os, pathlib, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request

API = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications/"
UPLOAD = "https://androidpublisher.googleapis.com/upload/androidpublisher/v3/applications/"
# The family's store contract: English is the default language in both stores, one contact address.
DEFAULT_LANGUAGE = "en-US"
CONTACT_EMAIL = "baltajmn@gmail.com"
_tok = None


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _token():
    global _tok
    if _tok:
        return _tok
    raw = os.environ.get("PLAY_SERVICE_ACCOUNT_JSON") or pathlib.Path(
        os.path.expanduser("~/keys/play-service-account.json")).read_text()
    sa = json.loads(raw)
    now = int(time.time())
    h = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    c = _b64(json.dumps({"iss": sa["client_email"], "scope": "https://www.googleapis.com/auth/androidpublisher",
                         "aud": "https://oauth2.googleapis.com/token", "iat": now, "exp": now + 3600}).encode())
    # mkstemp creates the file 0600, and it is gone before this returns.
    with tempfile.NamedTemporaryFile("w", suffix=".pem") as key:
        key.write(sa["private_key"]); key.flush()
        sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key.name], input=f"{h}.{c}".encode(),
                             capture_output=True, check=True).stdout
    body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                   "assertion": f"{h}.{c}.{_b64(sig)}"}).encode()
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", body, timeout=60) as r:
        _tok = json.load(r)["access_token"]
    return _tok


def call(method, url, body=None, data=None, ctype="application/json"):
    if body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": "Bearer " + _token(), "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            out = r.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {url.split('/applications/')[-1]} -> {e.code}: "
                         f"{e.read().decode(errors='replace')[:800]}")
    return json.loads(out) if out else {}


class Edit:
    """An edit that is committed only if the block finishes, and deleted otherwise."""

    def __init__(self, package, dry=False):
        self.base, self.up, self.dry = API + package, UPLOAD + package, dry

    def __enter__(self):
        self.id = call("POST", self.base + "/edits")["id"]
        self.url = f"{self.base}/edits/{self.id}"
        return self

    def __exit__(self, kind, *_):
        if kind is None and not self.dry:
            call("POST", f"{self.url}:commit")
            print("  edición confirmada")
            return
        if kind is None:
            call("POST", f"{self.url}:validate")
            print("  --dry: Play valida la edición; descartada sin cambiar nada")
        call("DELETE", self.url)

    def image(self, language, kind, png):
        call("POST", f"{self.up}/edits/{self.id}/listings/{language}/{kind}?uploadType=media",
             data=png.read_bytes(), ctype="image/png")


def _read(p):
    return p.read_text(encoding="utf-8").strip()


def _dirs(p):
    return sorted(d for d in p.iterdir() if d.is_dir()) if p.is_dir() else []


def estado(package):
    url = f"{API}{package}/edits/" + call("POST", API + package + "/edits")["id"]
    try:
        for t in call("GET", url + "/tracks").get("tracks", []):
            print(t["track"], [(r.get("status"), r.get("versionCodes"), r.get("name")) for r in t.get("releases", [])])
        print("AAB:", [b["versionCode"] for b in call("GET", url + "/bundles").get("bundles", [])])
        print("fichas:", [l["language"] for l in call("GET", url + "/listings").get("listings", [])])
        print("detalles:", call("GET", url + "/details"))
    finally:
        call("DELETE", url)


def ficha(package, repo, web, dry):
    store = pathlib.Path(repo) / "store"
    with Edit(package, dry) as e:
        for d in _dirs(store / "listings"):
            call("PUT", f"{e.url}/listings/{d.name}", {
                "language": d.name, "title": _read(d / "title.txt"),
                "shortDescription": _read(d / "short.txt"), "fullDescription": _read(d / "full.txt")})
            print(f"  {d.name}: textos")
        # PUT and not PATCH: anything not listed here (a phone number) comes off the listing.
        call("PUT", e.url + "/details", {"defaultLanguage": DEFAULT_LANGUAGE,
                                        "contactEmail": CONTACT_EMAIL, "contactWebsite": web})
        print(f"  detalles: {DEFAULT_LANGUAGE}, {CONTACT_EMAIL}, {web}")
        call("DELETE", f"{e.url}/listings/{DEFAULT_LANGUAGE}/icon")
        e.image(DEFAULT_LANGUAGE, "icon", store / "play/icon-512.png")
        for png in sorted((store / "play/feature").glob("*.png")):
            call("DELETE", f"{e.url}/listings/{png.stem}/featureGraphic")
            e.image(png.stem, "featureGraphic", png)
            print(f"  {png.stem}: gráfico de funciones")
        for d in _dirs(store / "screenshots/play"):
            pngs = sorted(d.glob("*.png"))
            if pngs:
                call("DELETE", f"{e.url}/listings/{d.name}/phoneScreenshots")
                for png in pngs:
                    e.image(d.name, "phoneScreenshots", png)
                print(f"  {d.name}: {len(pngs)} capturas")


def aab(package, path, track, repo, status="completed"):
    if track not in ("internal", "alpha", "beta"):
        sys.exit(f"Canal {track} no permitido: producción se publica desde Play Console.")
    notes = [{"language": f.name.removeprefix("whatsnew-"), "text": _read(f)}
             for f in sorted((pathlib.Path(repo) / "store/whatsnew").glob("whatsnew-*"))]
    with Edit(package) as e:
        vc = call("POST", f"{e.up}/edits/{e.id}/bundles?uploadType=media", data=pathlib.Path(path).read_bytes(),
                  ctype="application/octet-stream")["versionCode"]
        print(f"  AAB subido: versionCode {vc}")
        call("PUT", f"{e.url}/tracks/{track}", {"track": track, "releases": [
            {"versionCodes": [str(vc)], "status": status, "releaseNotes": notes}]})
        print(f"  canal {track}, {status}, notas en {len(notes)} idiomas")


def money(amount, currency):
    units, _, cents = f"{float(amount):.2f}".partition(".")
    return {"currencyCode": currency, "units": units, "nanos": int(cents) * 10**7}


def convert(package, price):
    """Play's own conversion of a price before tax, rounded the way Play rounds each currency."""
    d = call("POST", API + package + "/pricing:convertRegionPrices", {"price": price})
    return {r: v["price"] for r, v in d["convertedRegionPrices"].items()}


def compra(package, product, prices, listings, dry):
    """`prices`: region code to tax-included price, in the currency Play uses in that region, for
    every region the product is sold in; nothing is sent unless all of them are there. `listings`:
    language to (title, description). Either can be None to leave it as it is."""
    p = call("GET", f"{API}{package}/oneTimeProducts/{product}")
    mask = []
    if listings:
        mask.append("listings")
        p["listings"] = [{"languageCode": l, "title": t, "description": d} for l, (t, d) in sorted(listings.items())]
        print(f"  {product}: textos en {len(listings)} idiomas, {' '.join(sorted(listings))}")
    if prices:
        mask.append("purchaseOptions")
    for po in p["purchaseOptions"] if prices else []:
        cfg = po["regionalPricingAndAvailabilityConfigs"]
        for r in cfg:
            new = prices.get(r["regionCode"])
            if not new or new["currencyCode"] != r["price"]["currencyCode"]:
                sys.exit(f"{r['regionCode']}: Play cobra en {r['price']['currencyCode']} y el precio es {new}")
            r["price"] = new
        # The price Play gives regions it opens in the future: before tax, like Play Console asks.
        if "newRegionsConfig" in po:
            es = float(prices["ES"]["units"]) + prices["ES"]["nanos"] / 1e9
            po["newRegionsConfig"]["eurPrice"] = money(es / 1.21, "EUR")
            po["newRegionsConfig"]["usdPrice"] = prices["US"]
        print(f"  {product}/{po['purchaseOptionId']}: ES {prices['ES']['units']}.{prices['ES']['nanos'] // 10**7:02d} EUR, "
              f"US {prices['US']['units']}.{prices['US']['nanos'] // 10**7:02d} USD, {len(cfg)} regiones")
    if dry or not mask:
        print("  --dry: no se cambia nada" if dry else "  nada que cambiar")
        return
    call("PATCH", f"{API}{package}/oneTimeProducts/{product}?updateMask={','.join(mask)}"
                  f"&regionsVersion.version={p['regionsVersion']['version']}", p)
    print(f"  {product}: guardado en Play")


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    a = [x for x in sys.argv[1:] if x != "--dry"]
    if len(a) == 2 and a[0] == "estado":
        estado(a[1])
    elif len(a) == 4 and a[0] == "ficha":
        ficha(a[1], a[2], a[3], dry)
    elif len(a) in (5, 6) and a[0] == "aab":
        aab(a[1], a[2], a[3], a[4], "draft" if a[5:] == ["draft"] else "completed")
    else:
        print(__doc__)
