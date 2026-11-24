#!/usr/bin/env python3
"""Oferta de salida (M10 de gk/docs/ESTANDAR-APPS.md): el de por vida un 40 % mas barato, `pro_lifetime_offer`, en las
tiendas y en RevenueCat. Se puede repetir: lo que ya existe se deja.

    oferta_salida.py [--dry] [<clave> ...]        sin claves: todas las de OFERTAS
    oferta_salida.py --captura <clave> <png>      captura de revision de Apple (la hoja de la oferta), cuando la app la ensena

App Store: compra no consumible con los textos del de por vida, sin China, Espana de base y EE. UU. fijado. Play (grandes):
compra unica copiada del de por vida, eurozona al precio de Espana y el resto con la conversion de Play del precio de EE. UU.
RevenueCat: el producto de cada tienda en el derecho `pro`; en las grandes, ademas, en el paquete `$rc_lifetime` de la
oferta `exit`, que la app pide por nombre (la actual sigue siendo `default`). Los precios por pais, despues, con
precios_pais.py. Apple revisa la compra con la version que la ensena (appstore.py enviar la manda a la vez).
"""
import pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import appstore as A, play as P, revenuecat as R  # noqa: E402

ID = "pro_lifetime_offer"
OFERTAS = {  # clave de RevenueCat: (bundle, de por vida sin el bundle, esta en Play, EUR, USD)
    "flowtime": ("com.baltajmn.flowtime", "pro_lifetime", True, "11.99", "10.99"),
    "quilt": ("com.baltajmn.habit", "pro_lifetime", True, "11.99", "10.99"),
    "chroma": ("com.baltajmn.color", "pro_lifetime", True, "11.99", "10.99"),
    "mood": ("com.baltajmn.mood", "mood_pro", True, "8.99", "7.99"),
    "drumwell": ("com.baltajmn.drumwell", "pro_lifetime", False, "7.99", "7.99"),
    "tilewell": ("com.baltajmn.tilewell", "pro_lifetime", False, "17.99", "17.99"),
}
NOTE = ("Discounted one-time purchase (40% off the regular lifetime plan). It is offered once, right after the user closes "
        "the Pro screen without buying, and stays on the Pro screen for 24 hours. It unlocks the same Pro as the other plans.")


def _maybe(path):
    try:
        return A.api("GET", path)
    except SystemExit as e:
        if "-> 404" in str(e):
            return None
        raise


def apple(bundle, life, eur, usd, dry):
    aid = A.app_of(bundle)["id"]
    iaps = {p["attributes"]["productId"]: p for p in A.get_all(f"/v1/apps/{aid}/inAppPurchasesV2")}
    base, name = iaps[f"{bundle}.{life}"], f"{bundle}.{ID}"
    texts = A.purchase_texts(base["id"])
    iap = iaps.get(name)
    print(f"  App Store {name}: {'ya existe' if iap else 'se crea'}, {eur} EUR / {usd} USD, textos en {len(texts)} idiomas")
    if dry:
        return
    if not iap:
        iap = A.api("POST", "/v2/inAppPurchases", {"data": {"type": "inAppPurchases", "attributes": {
            "name": "Pro Lifetime Offer", "productId": name, "inAppPurchaseType": "NON_CONSUMABLE",
            "familySharable": base["attributes"].get("familySharable", False), "reviewNote": NOTE},
            "relationships": {"app": {"data": {"type": "apps", "id": aid}}}}})["data"]
    pid = iap["id"]
    have = {l["attributes"]["locale"] for l in A.get_all(f"/v2/inAppPurchases/{pid}/inAppPurchaseLocalizations")}
    for loc, (n, d) in texts.items():
        if loc not in have:
            A.api("POST", "/v1/inAppPurchaseLocalizations", {"data": {"type": "inAppPurchaseLocalizations",
                  "attributes": {"locale": loc, "name": n, "description": d},
                  "relationships": {"inAppPurchaseV2": {"data": {"type": "inAppPurchases", "id": pid}}}}})
    if not _maybe(f"/v2/inAppPurchases/{pid}/inAppPurchaseAvailability"):
        terr = [t["id"] for t in A.get_all("/v1/territories?limit=200") if t["id"] != "CHN"]
        A.api("POST", "/v1/inAppPurchaseAvailabilities", {"data": {"type": "inAppPurchaseAvailabilities",
              "attributes": {"availableInNewTerritories": True}, "relationships": {
                  "inAppPurchase": {"data": {"type": "inAppPurchases", "id": pid}},
                  "availableTerritories": {"data": [{"type": "territories", "id": t} for t in terr]}}}})
    sched = _maybe(f"/v2/inAppPurchases/{pid}/iapPriceSchedule?include=manualPrices")
    if not (sched and sched.get("included")):
        def point(territory, price):
            pts = A.get_all(f"/v2/inAppPurchases/{pid}/pricePoints?filter[territory]={territory}&limit=200")
            return next(x["id"] for x in pts if float(x["attributes"]["customerPrice"]) == float(price))
        prices = {"esp": point("ESP", eur), "usa": point("USA", usd)}
        A.api("POST", "/v1/inAppPurchasePriceSchedules", {
            "data": {"type": "inAppPurchasePriceSchedules", "relationships": {
                "inAppPurchase": {"data": {"type": "inAppPurchases", "id": pid}},
                "baseTerritory": {"data": {"type": "territories", "id": "ESP"}},
                "manualPrices": {"data": [{"type": "inAppPurchasePrices", "id": "${" + k + "}"} for k in prices]}}},
            "included": [{"type": "inAppPurchasePrices", "id": "${" + k + "}", "attributes": {"startDate": None},
                          "relationships": {
                              "inAppPurchaseV2": {"data": {"type": "inAppPurchases", "id": pid}},
                              "inAppPurchasePricePoint": {"data": {"type": "inAppPurchasePricePoints", "id": v}}}}
                         for k, v in prices.items()]})
    print(f"  App Store {name}: lista (falta la captura de revision: --captura)")


def play(pkg, life, eur, usd, dry):
    try:
        o = P.call("GET", f"{P.API}{pkg}/oneTimeProducts/{ID}")
        print(f"  Play {ID}: ya existe ({o['purchaseOptions'][0].get('state')})")
        return
    except SystemExit as e:
        if "404" not in str(e):
            raise
    o = P.call("GET", f"{P.API}{pkg}/oneTimeProducts/{life}")
    conv = P.convert(pkg, P.money(usd, "USD"))
    po = next(x for x in o["purchaseOptions"] if x.get("state") == "ACTIVE")
    po.pop("state", None)
    for r in po["regionalPricingAndAvailabilityConfigs"]:
        cur = r["price"]["currencyCode"]
        new = P.money(eur, "EUR") if cur == "EUR" else conv.get(r["regionCode"])
        if not new or new["currencyCode"] != cur:
            sys.exit(f"{r['regionCode']}: Play cobra en {cur} y la conversion da {new}")
        r["price"] = new
    po["newRegionsConfig"]["usdPrice"] = P.money(usd, "USD")
    po["newRegionsConfig"]["eurPrice"] = P.money(float(eur) / 1.21, "EUR")
    body = {**o, "productId": ID, "purchaseOptions": [po]}
    print(f"  Play {ID}: se crea, {len(po['regionalPricingAndAvailabilityConfigs'])} regiones, textos en {len(o['listings'])} idiomas")
    if dry:
        return
    P.call("PATCH", f"{P.API}{pkg}/onetimeproducts/{ID}?allowMissing=true&updateMask=listings,purchaseOptions"
                    f"&regionsVersion.version={o['regionsVersion']['version']}", body)
    # camelCase here: the lowercase path that PATCH needs answers 404 to this one.
    P.call("POST", f"{P.API}{pkg}/oneTimeProducts/{ID}/purchaseOptions:batchUpdateStates", {"requests": [
        {"activatePurchaseOptionRequest": {"packageName": pkg, "productId": ID, "purchaseOptionId": po["purchaseOptionId"]}}]})
    print(f"  Play {ID}: creada y activa")


def revenuecat(key, bundle, life, in_play, dry):
    p = R.items(key, "/projects")[0]["id"]
    apps = {a["id"]: a["type"] for a in R.items(key, f"/projects/{p}/apps")}
    products = R.items(key, f"/projects/{p}/products")
    ids = []
    for aid, kind in apps.items():
        if kind == "app_store":
            ident = f"{bundle}.{ID}"
        elif kind == "play_store" and in_play:
            # Same shape as the lifetime product already there ("id" or "id:option").
            old = next((x["store_identifier"] for x in products if x["app_id"] == aid and x["store_identifier"].split(":")[0] == life), life)
            ident = ID + (old[len(life):] if old.startswith(life) else "")
        else:
            continue
        prod = next((x for x in products if x["app_id"] == aid and x["store_identifier"] == ident), None)
        print(f"  RevenueCat {kind}: {ident} {'ya existe' if prod else 'se crea'}")
        if not prod and not dry:
            prod = R.call(key, "POST", f"/projects/{p}/products", {"store_identifier": ident, "app_id": aid,
                          "type": "non_consumable", "display_name": f"Pro lifetime offer ({kind})"})
        if prod:
            ids.append(prod["id"])
    if dry:
        return
    ent = next(e for e in R.items(key, f"/projects/{p}/entitlements") if e["lookup_key"] == "pro")
    have = {x["id"] for x in R.items(key, f"/projects/{p}/entitlements/{ent['id']}/products")}
    if [i for i in ids if i not in have]:
        R.call(key, "POST", f"/projects/{p}/entitlements/{ent['id']}/actions/attach_products",
               {"product_ids": [i for i in ids if i not in have]})
    if not in_play:   # mini apps sell with StoreKit; RevenueCat only observes, the entitlement is what counts
        print(f"  RevenueCat: en `pro`")
        return
    off = next((o for o in R.items(key, f"/projects/{p}/offerings") if o["lookup_key"] == "exit"), None) or \
        R.call(key, "POST", f"/projects/{p}/offerings", {"lookup_key": "exit", "display_name": "Exit offer"})
    pack = next((k for k in R.items(key, f"/projects/{p}/offerings/{off['id']}/packages") if k["lookup_key"] == "$rc_lifetime"), None) or \
        R.call(key, "POST", f"/projects/{p}/offerings/{off['id']}/packages", {"lookup_key": "$rc_lifetime", "display_name": "Lifetime offer", "position": 1})
    have = {x["product"]["product_id"] if "product" in x else x.get("product_id") for x in
            R.items(key, f"/projects/{p}/packages/{pack['id']}/products")}
    if [i for i in ids if i not in have]:
        R.call(key, "POST", f"/projects/{p}/packages/{pack['id']}/actions/attach_products",
               {"products": [{"product_id": i, "eligibility_criteria": "all"} for i in ids if i not in have]})
    print(f"  RevenueCat: en `pro` y en $rc_lifetime de la oferta `exit`")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--captura"]:
        k, png = sys.argv[2], sys.argv[3]
        A.captura_compra(OFERTAS[k][0], f"{OFERTAS[k][0]}.{ID}", png, replace=True)
        sys.exit()
    dry = "--dry" in sys.argv
    for k in [x for x in sys.argv[1:] if not x.startswith("--")] or OFERTAS:
        bundle, life, in_play, eur, usd = OFERTAS[k]
        print(k)
        apple(bundle, life, eur, usd, dry)
        if in_play:
            play(bundle, life, eur, usd, dry)
        revenuecat(k, bundle, life, in_play, dry)
