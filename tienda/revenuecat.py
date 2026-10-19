#!/usr/bin/env python3
"""RevenueCat from the terminal: the monthly subscription of an app next to its lifetime purchase.

    revenuecat.py suscripcion <app> <package> <Play product> <base plan> [--dry]

`app` is the Keychain name of the project's secret key (dev.baltajmn.revenuecat-<app>: chroma,
flowtime, mood, quilt). It creates the subscription product in the Play and App Store apps of the
project, attaches both to the `pro` entitlement and puts them in the package `$rc_monthly` of the
`default` offering. What already exists is left alone. The secret key is read inside the call and
never printed.
"""
import json, subprocess, sys, urllib.error, urllib.request

API = "https://api.revenuecat.com/v2"


def _key(app):
    return subprocess.run(["security", "find-generic-password", "-a", subprocess.os.environ["USER"], "-s",
                           f"dev.baltajmn.revenuecat-{app}", "-w"], capture_output=True, text=True, check=True).stdout.strip()


def call(app, method, path, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + _key(app), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = r.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')[:800]}")
    return json.loads(out) if out else {}


def items(app, path):
    out, d = [], call(app, "GET", path + ("&" if "?" in path else "?") + "limit=100")
    out += d["items"]
    while d.get("next_page"):
        d = call(app, "GET", d["next_page"].removeprefix("/v2"))
        out += d["items"]
    return out


def suscripcion(app, package, play_product, base_plan, dry):
    p = items(app, "/projects")[0]["id"]
    apps = items(app, f"/projects/{p}/apps")
    stores = {"play_store": f"{play_product}:{base_plan}", "app_store": f"{package}.{play_product}"}
    products = items(app, f"/projects/{p}/products")
    ids = []
    for a in apps:
        ident = stores.get(a["type"])
        if not ident:
            continue
        prod = next((x for x in products if x["app_id"] == a["id"] and x["store_identifier"] == ident), None)
        print(f"  {a['type']}: {ident} {'ya existe' if prod else 'se crea'}")
        if not prod and not dry:
            prod = call(app, "POST", f"/projects/{p}/products", {
                "store_identifier": ident, "app_id": a["id"], "type": "subscription",
                "display_name": f"Pro monthly ({a['type']})"})
        if prod:
            ids.append(prod["id"])
    if dry:
        return
    ent = next(e for e in items(app, f"/projects/{p}/entitlements") if e["lookup_key"] == "pro")
    have = {x["id"] for x in items(app, f"/projects/{p}/entitlements/{ent['id']}/products")}
    new = [i for i in ids if i not in have]
    if new:
        call(app, "POST", f"/projects/{p}/entitlements/{ent['id']}/actions/attach_products", {"product_ids": new})
    off = next(o for o in items(app, f"/projects/{p}/offerings") if o["lookup_key"] == "default")
    pack = next((k for k in items(app, f"/projects/{p}/offerings/{off['id']}/packages") if k["lookup_key"] == "$rc_monthly"), None)
    if not pack:
        pack = call(app, "POST", f"/projects/{p}/offerings/{off['id']}/packages",
                    {"lookup_key": "$rc_monthly", "display_name": "Monthly", "position": 2})
    have = {x["product"]["product_id"] if "product" in x else x.get("product_id") for x in
            items(app, f"/projects/{p}/packages/{pack['id']}/products")}
    new = [i for i in ids if i not in have]
    if new:
        call(app, "POST", f"/projects/{p}/packages/{pack['id']}/actions/attach_products",
             {"products": [{"product_id": i, "eligibility_criteria": "all"} for i in new]})
    print(f"  {app}: producto(s) en `pro` y en $rc_monthly de `default`")


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if x != "--dry"]
    if len(a) != 5 or a[0] != "suscripcion":
        sys.exit(__doc__)
    suscripcion(a[1], a[2], a[3], a[4], "--dry" in sys.argv)
