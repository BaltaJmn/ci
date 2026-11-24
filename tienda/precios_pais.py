#!/usr/bin/env python3
"""Precio por pais segun poder adquisitivo (regla P6 de gk/docs/ESTANDAR-APPS.md), en App Store y Play. Solo bajadas.

    precios_pais.py [--dry] [<bundle> ...]    sin apps: todas las de la cuenta de App Store y las de PLAY
    precios_pais.py --tabla                    rehace precios_pais.json con los datos del Banco Mundial

Factor de un pais: raiz cuadrada de su nivel de precios (factor PPA / tipo de cambio, Banco Mundial, EE. UU. = 1), en pasos
de 0,05 entre 0,35 y 1; desde 0,85 se queda en 1. La raiz suaviza: Mexico 0,54 da 0,75 e India 0,23 da 0,5, asi quien ya
compra a precio entero (Drumwell en MX y BR) no baja a la mitad. Fuera: EE. UU., China y todo lo que se cobra en euros
(mismo numero que Espana). El precio nuevo de un pais es el que la tienda pone alli al precio de EE. UU. por el factor; se
cambia solo si baja mas de un 3 %, asi que volver a correrlo no cambia nada.

App Store: compras unicas con precio manual en esos paises dentro de su calendario (Espana sigue de base), al momento;
suscripciones desde pasado manana con preserveCurrentPrice false, para que quien ya paga baje tambien (las no aprobadas,
como precio inicial). Play: precio por region de compras unicas y planes base, al momento; los suscriptores de antes
conservan el suyo (casi no hay, no se migran). Consumibles (propinas) fuera. Un fallo en un producto se apunta y se sigue.
"""
import json, math, pathlib, sys, time, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import appstore as A, play as P  # noqa: E402
from compra import A2  # noqa: E402

TABLA = HERE / "precios_pais.json"
PLAY = ("com.baltajmn.flowtime", "com.baltajmn.color", "com.baltajmn.habit", "com.baltajmn.mood",
        "com.baltajmn.bullet", "com.baltajmn.test4test", "com.baltajmn.sleeper")
A3 = {v: k for k, v in A2.items()}
MUESTRA = ("MEX", "BRA", "IND", "TUR", "JPN", "ARG")


def tabla():
    def wb(i):
        u = f"https://api.worldbank.org/v2/country/all/indicator/{i}?format=json&mrnev=1&per_page=400"
        return {x["countryiso3code"]: x["value"] for x in json.load(urllib.request.urlopen(u, timeout=60))[1]
                if x["value"] and x["countryiso3code"]}
    ppp, fx = wb("PA.NUS.PPP"), wb("PA.NUS.FCRF")
    out = {}
    for c in sorted(set(ppp) & set(fx) & set(A2)):
        f = min(1.0, max(0.35, round(math.sqrt(ppp[c] / fx[c]) / 0.05) * 0.05))
        if f < 0.85 and c not in ("USA", "CHN"):
            out[c] = round(f, 2)
    TABLA.write_text(json.dumps(out, indent=0, sort_keys=True) + "\n")
    print(f"{len(out)} paises con factor < 1 en {TABLA.name}")


F = json.loads(TABLA.read_text()) if TABLA.exists() else {}


def plan(us, cur, us_points, eq):
    """{territorio: (punto, precio nuevo, precio de ahora)} de los paises que bajan. `us`: precio de EE. UU.; `cur`:
    {territorio: precio de ahora}; `us_points`: [(precio, punto)] de EE. UU.; `eq(punto)`: {territorio: (punto, precio,
    moneda)}."""
    out, cache = {}, {}
    for t, f in F.items():
        p = max((x for x in us_points if x[0] <= us * f + 1e-9), default=None)
        if t not in cur or not p:
            continue
        if p[1] not in cache:
            cache[p[1]] = eq(p[1])
        e = cache[p[1]].get(t)
        if e and e[2] != "EUR" and e[1] < cur[t] * 0.97:
            out[t] = (e[0], e[1], cur[t])
    return out


def _eq(path):
    out = {}
    while path:
        d = A.api("GET", path)
        cur = {t["id"]: t["attributes"]["currency"] for t in d.get("included", []) if t["type"] == "territories"}
        for x in d["data"]:
            t = x["relationships"]["territory"]["data"]["id"]
            out[t] = (x["id"], float(x["attributes"]["customerPrice"]), cur[t])
        path = d.get("links", {}).get("next")
    return out


def _rows(path, point_type):
    """[(territorio, punto, precio, fecha de inicio, atributos)] de una lista de precios con su punto incluido."""
    out = []
    while path:
        d = A.api("GET", path)
        pts = {x["id"]: float(x["attributes"]["customerPrice"]) for x in d.get("included", []) if x["type"] == point_type}
        for r in d["data"]:
            rel = r["relationships"]
            pid = next(v["data"]["id"] for k, v in rel.items() if k.endswith("PricePoint"))
            out.append((rel["territory"]["data"]["id"], pid, pts[pid], r["attributes"].get("startDate") or "", r["attributes"]))
        path = d.get("links", {}).get("next")
    return out


def _us_points(path):
    return sorted((float(x["attributes"]["customerPrice"]), x["id"]) for x in A.get_all(path))


def _show(name, new):
    m = ", ".join(f"{t} {new[t][2]:g} a {new[t][1]:g}" for t in MUESTRA if t in new)
    print(f"  {name}: bajan {len(new)} paises ({m})")


def apple_compra(iap, dry):
    pid, name = iap["id"], iap["attributes"]["productId"]
    base = A.api("GET", f"/v1/inAppPurchasePriceSchedules/{pid}/baseTerritory")["data"]["id"]
    rows = {k: [x for x in _rows(f"/v1/inAppPurchasePriceSchedules/{pid}/{k}Prices?include=inAppPurchasePricePoint,territory&limit=200",
                                 "inAppPurchasePricePoints") if not x[4].get("endDate")] for k in ("automatic", "manual")}
    manual = {t: p for t, p, _, _, _ in rows["manual"]}
    cur = {t: v for t, _, v, _, _ in rows["automatic"] + rows["manual"]}
    new = plan(cur["USA"], cur, _us_points(f"/v2/inAppPurchases/{pid}/pricePoints?filter[territory]=USA&limit=200"),
               lambda p: _eq(f"/v1/inAppPurchasePricePoints/{p}/equalizations?include=territory&limit=200"))
    _show(name, new)
    if dry or not new:
        return
    manual.update({t: p for t, (p, _, _) in new.items()})
    keys = {t: "${" + t + "}" for t in manual}
    A.api("POST", "/v1/inAppPurchasePriceSchedules", {
        "data": {"type": "inAppPurchasePriceSchedules", "relationships": {
            "inAppPurchase": {"data": {"type": "inAppPurchases", "id": pid}},
            "baseTerritory": {"data": {"type": "territories", "id": base}},
            "manualPrices": {"data": [{"type": "inAppPurchasePrices", "id": k} for k in keys.values()]}}},
        "included": [{"type": "inAppPurchasePrices", "id": keys[t], "attributes": {"startDate": None}, "relationships": {
            "inAppPurchaseV2": {"data": {"type": "inAppPurchases", "id": pid}},
            "inAppPurchasePricePoint": {"data": {"type": "inAppPurchasePricePoints", "id": p}}}} for t, p in manual.items()]})
    print(f"  {name}: guardado")


def apple_suscripcion(sub, dry):
    sid, name, state = sub["id"], sub["attributes"]["productId"], sub["attributes"]["state"]
    last = {}
    for t, _, v, start, a in _rows(f"/v1/subscriptions/{sid}/prices?include=subscriptionPricePoint,territory&limit=200",
                                   "subscriptionPricePoints"):
        if not a.get("preserved") and (t not in last or start >= last[t][0]):
            last[t] = (start, v)
    cur = {t: v for t, (_, v) in last.items()}
    new = plan(cur["USA"], cur, _us_points(f"/v1/subscriptions/{sid}/pricePoints?filter[territory]=USA&limit=200"),
               lambda p: _eq(f"/v1/subscriptionPricePoints/{p}/equalizations?include=territory&limit=200"))
    _show(f"{name} ({state})", new)
    if dry or not new:
        return
    approved = state == "APPROVED"
    attrs = {"startDate": time.strftime("%Y-%m-%d", time.localtime(time.time() + 2 * 86400)),
             "preserveCurrentPrice": False} if approved else {}
    for t, (p, _, _) in new.items():
        A.api("POST", "/v1/subscriptionPrices", {"data": {"type": "subscriptionPrices", "attributes": attrs, "relationships": {
            "subscription": {"data": {"type": "subscriptions", "id": sid}},
            "subscriptionPricePoint": {"data": {"type": "subscriptionPricePoints", "id": p}},
            "territory": {"data": {"type": "territories", "id": t}}}}})
    print(f"  {name}: guardado{', desde ' + attrs['startDate'] if approved else ''}")


def apple(bundle, dry):
    a = A.app_of(bundle)["id"]
    print(f"{bundle} App Store")
    todo = [(apple_compra, p) for p in A.get_all(f"/v1/apps/{a}/inAppPurchasesV2?limit=50")
            if p["attributes"]["inAppPurchaseType"] == "NON_CONSUMABLE"]
    todo += [(apple_suscripcion, s) for g in A.get_all(f"/v1/apps/{a}/subscriptionGroups?limit=50")
             for s in A.get_all(f"/v1/subscriptionGroups/{g['id']}/subscriptions?limit=50")
             if s["attributes"]["state"] not in ("DEVELOPER_REMOVED_FROM_SALE", "REMOVED_FROM_SALE", "REJECTED")]
    for fn, x in todo:
        try:
            fn(x, dry)
        except SystemExit as e:
            print(f"  {x['attributes']['productId']}: FALLO {str(e)[:300]}")


def _f(m):
    return int(m.get("units") or 0) + m.get("nanos", 0) / 1e9


def play_plan(pkg, cur):
    out, conv = {}, {}
    for r, m in cur.items():
        f = F.get(A3.get(r, ""))
        if not f or m["currencyCode"] == "EUR":
            continue
        if f not in conv:
            conv[f] = P.convert(pkg, P.money(round(_f(cur["US"]) * f, 2), "USD"))
        n = conv[f].get(r)
        if n and n["currencyCode"] == m["currencyCode"] and _f(n) < _f(m) * 0.97:
            out[r] = n
    return out


def _show_play(name, cur, new):
    m = ", ".join(f"{A2[t]} {_f(cur[A2[t]]):g} a {_f(new[A2[t]]):g}" for t in MUESTRA if A2[t] in new)
    print(f"  {name}: bajan {len(new)} regiones ({m})")


def play(pkg, dry):
    print(f"{pkg} Play")
    for s in P.call("GET", f"{P.API}{pkg}/subscriptions").get("subscriptions", []):
        for bp in s["basePlans"]:
            if bp.get("state") != "ACTIVE":
                continue
            name = f"{s['productId']}/{bp['basePlanId']}"
            try:
                cur = {r["regionCode"]: r["price"] for r in bp["regionalConfigs"] if "price" in r}
                new = play_plan(pkg, cur)
                _show_play(name, cur, new)
                if new and not dry:
                    P.precio_suscripcion(pkg, s["productId"], bp["basePlanId"], {**cur, **new}, dry)
            except SystemExit as e:
                print(f"  {name}: FALLO {str(e)[:300]}")
    for o in P.call("GET", f"{P.API}{pkg}/oneTimeProducts").get("oneTimeProducts", []):
        name = o["productId"]
        po = [x for x in o.get("purchaseOptions", []) if x.get("state") == "ACTIVE"]
        if name.startswith(("tip_", "support_")) or not po:
            continue
        try:
            cur = {r["regionCode"]: r["price"] for r in po[0]["regionalPricingAndAvailabilityConfigs"] if "price" in r}
            new = play_plan(pkg, cur)
            _show_play(name, cur, new)
            if new and not dry:
                P.compra(pkg, name, {**cur, **new}, None, dry)
        except SystemExit as e:
            print(f"  {name}: FALLO {str(e)[:300]}")


if __name__ == "__main__":
    if "--tabla" in sys.argv:
        tabla()
        sys.exit()
    dry = "--dry" in sys.argv
    apps = [x for x in sys.argv[1:] if not x.startswith("--")]
    asc = {x["attributes"]["bundleId"] for x in A.get_all("/v1/apps?limit=200")}
    for b in apps or sorted(asc | set(PLAY)):
        if b in asc:
            apple(b, dry)
        if b in PLAY:
            play(b, dry)
