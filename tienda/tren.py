#!/usr/bin/env python3
"""The weekly release train (PUBLICAR.md, section 7). Run by .github/workflows/tren.yml, which each
app calls from its own tren.yml:

    tren.py domingo <repo>   Sunday: is there a new version? numbers it (minor + 1), commits and tags it,
                             and says what to upload: Play alpha and TestFlight
    tren.py aviso            Sunday, after the uploads: the ntfy with the "Parar esta semana" button
    tren.py eco              night: posts the stop orders again (ntfy keeps 12 h: one pressed on Sunday
                             at 20:00 would be gone by Monday at 9:00)
    tren.py lunes <repo>     Monday: Sunday's build to production on Play and to App Review, unless stopped
                             or that store still has the previous version in review, rejected or pending
    tren.py fallo <paso>     notice when a step broke before its own notice
    tren.py --prueba         checks without network

Everything comes from the environment: APP (FlowTime), CLAVE (flowtime), PAQUETE, BUNDLE, VERSION_FILE,
NTFY_TOPIC (notices), NTFY_TOPIC_APROBAR (secret topic: stop orders, the Play state the GK35 reads
from Play Console and the train's own messages for semana.baltajmn.dev), SECO=true for a dry run, and
the credentials play.py and appstore.py read. Dry: nothing is pushed to main, no tag, Play only
validates, nothing changes in App Store Connect, notices say PRUEBA and stop orders say "parar prueba".
"""
import datetime, json, os, pathlib, re, subprocess, sys, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import appstore, play  # noqa: E402

NOTAS, GENERICAS = "store/whatsnew", "store/whatsnew-generic"
# A week with only these changes makes no version: documentation, workflows and IDE files.
FUERA = [":(exclude)*.md", ":(exclude).github", ":(exclude).idea"]
FRESCO = 3 * 3600  # the GK35 reads Play Console 20 minutes before each train; older than 3 h, unknown
# App Store states that leave the way free. Anything else on any version blocks (in review, pending
# release, rejected, processing...): the train never piles a version on top of another.
LIBRE_IOS = {"READY_FOR_SALE", "READY_FOR_DISTRIBUTION", "REPLACED_WITH_NEW_VERSION", "REMOVED_FROM_SALE",
             "DEVELOPER_REMOVED_FROM_SALE", "PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED"}
SIN_ENVIAR = {"PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED"}  # prepared but never sent: reused
QUE_HACER_IOS = {
    "WAITING_FOR_REVIEW": "nada: el primer lunes con esa ya aprobada, el tren manda la nueva (este mismo, si Apple la aprueba antes de las 9:00).",
    "IN_REVIEW": "nada: el primer lunes con esa ya aprobada, el tren manda la nueva (este mismo, si Apple la aprueba antes de las 9:00).",
    "PENDING_DEVELOPER_RELEASE": "publicala (App Store Connect > la app > la version > Publicar esta version); el lunes siguiente sale la nueva.",
    "REJECTED": "lee el motivo en App Store Connect > la app > Resolution Center, contesta o arregla y reenviala; mientras siga rechazada, el tren no envia otra.",
    "METADATA_REJECTED": "lee el motivo en App Store Connect > Resolution Center y corrige la ficha; mientras siga rechazada, el tren no envia otra.",
    "INVALID_BINARY": "mira el correo de Apple con el motivo; el tren no envia otra hasta que se resuelva.",
}


def env(n, por=None):
    v = os.environ.get(n, por)
    if v is None:
        sys.exit(f"falta {n} en el entorno")
    return v


SECO = os.environ.get("SECO") == "true"


# ---------- numbers ----------
NOMBRE = re.compile(r'(versionName\s*=\s*")(\d+)\.(\d+)(?:\.(\d+))?(")')
CODIGO = re.compile(r"(versionCode\s*=\s*)(\d+)")


def version(texto):
    n, c = NOMBRE.search(texto), CODIGO.search(texto)
    if not n or not c:
        sys.exit("no encuentro versionName o versionCode en el fichero de version")
    return f"{n[2]}.{n[3]}.{n[4] or 0}", int(c[2])


def subir_minor(texto):
    """Every train: minor + 1 and patch 0 (the patch is for urgent fixes, by hand); versionCode + 1."""
    _, code = version(texto)
    texto = NOMBRE.sub(lambda m: f"{m[1]}{m[2]}.{int(m[3]) + 1}.0{m[5]}", texto, count=1)
    texto = CODIGO.sub(lambda m: f"{m[1]}{code + 1}", texto, count=1)
    return (texto, *version(texto))


# ---------- git ----------
def git(repo, *a, check=True):
    r = subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"git {' '.join(a)}: {r.stderr.strip()[:400]}")
    return r.stdout.strip()


def ultima_etiqueta(repo, ref="HEAD"):
    return git(repo, "describe", "--tags", "--abbrev=0", "--match", "v*", ref)


def version_en(repo, ref):
    return version(git(repo, "show", f"{ref}:{env('VERSION_FILE')}"))


def etiqueta_de(repo, code):
    """The tag whose version file has that versionCode (v2.4.0-b63 is the 63 of 2.4.0)."""
    for t in git(repo, "tag", "-l", "v*", "--sort=-creatordate").split():
        try:
            if version_en(repo, t)[1] == code:
                return t
        except SystemExit:
            continue
    return None


def cambia(repo, desde, hasta, ruta):
    return bool(git(repo, "diff", "--name-only", desde, hasta, "--", ruta))


def notas_para(repo, desde_code, hasta):
    """store/whatsnew, or the generic notes if nobody touched it since the version that store has.
    Per store: one may be a week behind, and its users have not read the last notes."""
    base = etiqueta_de(repo, desde_code)
    generica = bool(base) and not cambia(repo, base, hasta, NOTAS)
    carpeta = GENERICAS if generica else NOTAS
    f = next((pathlib.Path(repo) / carpeta / f"whatsnew-{l}" for l in ("es-ES", "en-US")
              if (pathlib.Path(repo) / carpeta / f"whatsnew-{l}").exists()), None)
    return {"carpeta": carpeta, "generica": generica, "base": base,
            "texto": f.read_text(encoding="utf-8").strip() if f else ""}


# ---------- ntfy ----------
def ntfy(msg):
    body = json.dumps({"topic": env("NTFY_TOPIC"), **msg}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://ntfy.sh/", body, method="POST"), timeout=30) as r:
        r.read()


def publicar(texto):
    """A line on the secret topic (stop orders, the train's own messages)."""
    with urllib.request.urlopen(urllib.request.Request(f"https://ntfy.sh/{env('NTFY_TOPIC_APROBAR')}",
                                                       texto.encode(), method="POST"), timeout=30) as r:
        r.read()


def leer_topic():
    url = f"https://ntfy.sh/{env('NTFY_TOPIC_APROBAR')}/json?poll=1&since=12h"
    with urllib.request.urlopen(url, timeout=30) as r:
        return [m for m in map(json.loads, r.read().decode().splitlines()) if m.get("event") == "message"]


def ejecucion():
    s, r, i = (os.environ.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{s}/{r}/actions/runs/{i}" if i else ""


# ---------- states ----------
def orden(clave, nombre):
    return f"parar {'prueba' if SECO else 'tren'} {clave} {nombre}"


def parada(msgs, clave, nombre):
    return any((m.get("message") or "").strip() == orden(clave, nombre) for m in msgs)


def estado_play(msgs, paquete):
    """The newest "tren-play <package> <json>" the GK35 posted (navegador.mjs on Publishing overview)."""
    for m in sorted(msgs, key=lambda m: m.get("time", 0), reverse=True):
        t = (m.get("message") or "").strip()
        if t.startswith(f"tren-play {paquete} "):
            try:
                return json.loads(t.split(" ", 2)[2])
            except ValueError:
                return None
    return None


def bloqueo_play(est, ahora, code=None):
    """(reason, cancel) for Play. `code`: the train's own alpha release, which may still be in review on
    Monday morning; if it is the only thing in review, promoting cancels and resends it with production."""
    que_hacer_gk = "en el GK35, node ~/AndroidStudioProjects/TikTok/versiones.mjs play (si dice 'Sin sesion', login de play por noVNC)."
    if not est:
        return f"no se en que estado esta Play: el GK35 no lo ha publicado en las ultimas 12 h. Que hacer: {que_hacer_gk}", False
    leido = datetime.datetime.fromisoformat(est["leido"].replace("Z", "+00:00")).timestamp()
    if ahora - leido > FRESCO:
        return f"no se en que estado esta Play: lo ultimo que leyo el GK35 es de hace {int((ahora - leido) / 3600)} h. Que hacer: {que_hacer_gk}", False
    filas = est.get("items") or []
    texto = "; ".join(" ".join(f) for f in filas) or est.get("detalle", "")
    if est.get("estado") == "libre":
        return None, False
    if est.get("estado") == "revision" and code and filas and all(
            not f[0].startswith("Producci") and f"{code} (" in " ".join(f) for f in filas):
        return None, True
    que = {"revision": ("tiene cambios en revision", "nada: cuando Google los apruebe, el lunes siguiente sale la nueva."),
           "rechazada": ("tiene cambios rechazados", "mira Play Console > Resumen de publicacion y la bandeja de Politicas, corrige y reenvia; el tren vuelve a intentarlo el lunes siguiente."),
           "pendiente": ("tiene cambios sin publicar o sin enviar", "publicalos o descartalos en Play Console > Resumen de publicacion."),
           }.get(est.get("estado"), ("no se lee bien en Play Console", que_hacer_gk))
    return f"{que[0]} ({texto or 'sin detalle'}). Que hacer: {que[1]}", False


def ios_versiones(bundle):
    a = appstore.app_of(bundle)["id"]
    d = appstore.api("GET", f"/v1/apps/{a}/appStoreVersions?filter[platform]=IOS&include=build&fields[builds]=version&limit=10")
    builds = {b["id"]: int(b["attributes"]["version"]) for b in d.get("included", []) if b["type"] == "builds"}
    return [(v["attributes"]["versionString"], v["attributes"].get("appVersionState") or v["attributes"]["appStoreState"],
             builds.get((v["relationships"]["build"].get("data") or {}).get("id"), 0)) for v in d["data"]]


def bloqueo_ios(vers):
    for nombre, estado, _ in vers:
        if estado not in LIBRE_IOS:
            return f"la {nombre} esta en {estado}. Que hacer: {QUE_HACER_IOS.get(estado, 'mira la version en App Store Connect.')}"
    return None


def enviada_ios(vers):
    """The newest build App Review has seen or is seeing (a version prepared but never sent does not count)."""
    return max([b for _, e, b in vers if e not in SIN_ENVIAR] or [0])


def ios_builds(bundle, nombre):
    a = appstore.app_of(bundle)["id"]
    return {int(b["attributes"]["version"]): b["attributes"]["processingState"] for b in appstore.get_all(
        f"/v1/builds?filter[app]={a}&filter[preReleaseVersion.version]={nombre}&fields[builds]=version,processingState&limit=50")}


def produccion_play(pistas):
    return max([int(c) for r in pistas.get("production", []) for c in r.get("versionCodes", [])] or [0])


def alpha_play(pistas):
    return {int(c) for r in pistas.get("alpha", []) for c in r.get("versionCodes", [])}


def salida(**kv):
    """Job outputs, only short flags: GitHub masks every line of a multi-line secret (also the "{" and "}"
    of the Play JSON) and its base64 forms, and drops any output that contains one. The plan travels as a
    file (PLAN_FILE, an artifact in tren.yml)."""
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as f:
        for k, v in kv.items():
            f.write(f"{k}={str(v).lower() if isinstance(v, bool) else v}\n")


# ---------- Sunday ----------
def domingo(repo):
    app, clave, paquete, bundle, vf = env("APP"), env("CLAVE"), env("PAQUETE"), env("BUNDLE"), env("VERSION_FILE")
    texto = (pathlib.Path(repo) / vf).read_text(encoding="utf-8")
    ultima = ultima_etiqueta(repo)
    cambios = git(repo, "diff", "--name-only", ultima, "HEAD", "--", ".", *FUERA).split()
    msgs, ahora = leer_topic(), datetime.datetime.now(datetime.timezone.utc).timestamp()
    pistas, vers = play.tracks(paquete), ios_versiones(bundle)
    prod_play, alpha, env_ios = produccion_play(pistas), alpha_play(pistas), enviada_ios(vers)
    nuevo = None
    if cambios or SECO:  # dry runs always build, to test the whole way
        nuevo, nombre, code = subir_minor(texto)
    else:
        nombre, code = version_en(repo, ultima)
    b_play, _ = bloqueo_play(estado_play(msgs, paquete), ahora, code)
    b_ios = bloqueo_ios(vers)
    falta_play, falta_ios = prod_play < code, env_ios < code
    sale_play, sale_ios = falta_play and not b_play, falta_ios and not b_ios
    if cambios and not SECO and not (sale_play or sale_ios):
        nuevo = None  # both stores blocked: no version is spent; next Sunday it is tried again
    tf = {} if nuevo else ios_builds(bundle, nombre)
    plan = {
        "app": app, "clave": clave, "version": nombre, "code": code, "nueva": bool(nuevo), "cambios": len(cambios),
        "ultima": ultima, "seco": SECO,
        "play": {"falta": falta_play, "sale": sale_play, "bloqueo": b_play, "produccion": prod_play,
                 "subir": SECO or (sale_play and code not in alpha),
                 "notas": notas_para(repo, prod_play, "HEAD")},
        "ios": {"falta": falta_ios, "sale": sale_ios, "bloqueo": b_ios, "enviada": env_ios,
                "subir": SECO or (falta_ios and code not in tf and (sale_play or sale_ios)),
                "notas": notas_para(repo, env_ios, "HEAD")},
    }
    ref = ultima
    if nuevo:
        (pathlib.Path(repo) / vf).write_text(nuevo, encoding="utf-8")
        quien = ["-c", "user.name=Tren de versiones", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com"]
        git(repo, *quien, "commit", "-qm", f"tren: {app} {nombre} ({code})", "--", vf)
        if SECO:
            ref = "tren-seco"
            git(repo, "push", "-qf", "origin", f"HEAD:refs/heads/{ref}")
        else:
            ref = f"v{nombre}"
            for intento in (1, 2):  # someone may have pushed to main since the checkout
                git(repo, "tag", "-f", ref)
                if not subprocess.run(["git", "-C", repo, "push", "-q", "--atomic", "origin", "HEAD:refs/heads/main",
                                       f"refs/tags/{ref}"]).returncode:
                    break
                if intento == 2:
                    sys.exit("no pude empujar el commit del tren a main")
                git(repo, *quien, "pull", "-q", "--rebase", "origin", "main")
    print(json.dumps(plan, ensure_ascii=False, indent=1))
    hay = plan["play"]["sale"] or plan["ios"]["sale"]
    pathlib.Path(env("PLAN_FILE", os.devnull)).write_text(json.dumps(plan), encoding="utf-8")
    salida(ref=ref, play=plan["play"]["subir"], ios=plan["ios"]["subir"], **{"notas-play": plan["play"]["notas"]["carpeta"]},
           aviso=SECO or bool(cambios) or hay)


def linea(tienda, t, resultado, code):
    if not t["falta"]:
        return f"{tienda}: ya tiene la {code}."
    if t["subir"] and resultado not in ("success", ""):
        return f"{tienda}: NO sale, la subida del domingo fallo ({resultado}). Que hacer: abre la ejecucion (tocar el aviso), mira el paso en rojo y relanzala con 'Re-run failed jobs' antes del lunes a las 9:00."
    if not t["sale"]:
        return f"{tienda}: NO sale, {t['bloqueo']}"
    return f"{tienda}: sale" + (" (subida hoy)" if t["subir"] else " (ya estaba subida)")


def aviso():
    global SECO
    p = json.loads(pathlib.Path(env("PLAN_FILE")).read_text(encoding="utf-8"))
    SECO = p["seco"]  # the stop button of a dry notice says "parar prueba"
    res = {"play": os.environ.get("PLAY_RESULT", ""), "ios": os.environ.get("IOS_RESULT", "")}
    lineas = [linea("Google Play", p["play"], res["play"], p["code"]), linea("iPhone", p["ios"], res["ios"], p["code"])]
    fallidas = [k for k in ("play", "ios") if p[k]["subir"] and res[k] not in ("success", "")]
    for k in fallidas:  # a failed upload means that store does not go out on Monday
        p[k]["sale"] = False
    n = {k: p[k]["notas"] for k in ("play", "ios")}
    nota = lambda x: (f" genericas, porque nadie cambio store/whatsnew desde la {x['base'].lstrip('v')}" if x["generica"] else "") + ":\n" + x["texto"]
    novedades = (f"Novedades{nota(n['play'])}" if n["play"]["carpeta"] == n["ios"]["carpeta"]
                 else f"Novedades en Play{nota(n['play'])}\n\nNovedades en iPhone{nota(n['ios'])}")
    pre = "PRUEBA, no sale nada de verdad. " if p["seco"] else ""
    if p["play"]["sale"] or p["ios"]["sale"]:
        que = [x for x, k in (("en Google Play, a produccion al 100 %", "play"), ("en iPhone, a revision de Apple, que la publica sola al aprobarla", "ios")) if p[k]["sale"]]
        msg = {"title": f"{pre}Mañana sale {p['app']} {p['version']}", "priority": 4,
               "message": f"{novedades}\n\n" + "\n".join(lineas) + "\n\n"
               f"Por que: es el tren semanal. El domingo se prepara lo que hay en main ({p['cambios']} ficheros cambiados desde la {p['ultima'].lstrip('v')}) "
               f"y el lunes a las 9:00 sale {' y '.join(que)}.\n"
               "Que hacer: nada si te parece bien. 'Parar esta semana' la frena en las dos tiendas hasta el domingo que viene; vale hasta el lunes a las 9:00. "
               "Las novedades ya estan fijadas: aunque se toque store/whatsnew antes del lunes, salen las de arriba.",
               "actions": [{"action": "http", "label": "Parar esta semana", "url": f"https://ntfy.sh/{env('NTFY_TOPIC_APROBAR')}",
                            "method": "POST", "body": orden(p["clave"], p["version"]), "clear": True}]}
    else:
        msg = {"title": f"{pre}{p['app']}: mañana no sale nada", "priority": 4,
               "message": "\n".join(lineas) + ("\n\nPor que: la subida del domingo fallo, y una tienda sin build del domingo no sale el lunes.\n"
               "Que hacer: lo que dice cada tienda arriba. Relanzada antes del lunes a las 9:00, llega otro aviso y sale; si no, el domingo que viene va con lo que haya en main."
               if fallidas else "\n\nPor que: el tren semanal no pone una version encima de otra que aun esta en revision, rechazada o sin publicar, y sin tienda libre no gasta numero.\n"
               "Que hacer: lo que dice cada tienda arriba. El domingo que viene se vuelve a intentar con lo que haya en main.")}
    msg["click"] = ejecucion()
    ntfy(msg)
    if not p["seco"]:
        publicar(f"tren-plan {p['clave']} " + json.dumps({"version": p["version"], "code": p["code"], "el": int(datetime.datetime.now().timestamp()),
                                                          "play": p["play"]["sale"] and (p["play"]["bloqueo"] or "sale"), "ios": p["ios"]["sale"] and (p["ios"]["bloqueo"] or "sale"),
                                                          "motivos": lineas}, ensure_ascii=False))
    print(msg["title"], "\n", msg["message"])


# ---------- night ----------
def eco():
    msgs, clave = leer_topic(), env("CLAVE")
    vistas = {(m.get("message") or "").strip() for m in msgs}
    for o in sorted(o for o in vistas if re.fullmatch(rf"parar {'prueba' if SECO else 'tren'} {re.escape(clave)} [\d.]+", o)):
        publicar(o)
        print("eco:", o)


# ---------- Monday ----------
def publicar_ios(repo, bundle, nombre, code, notas, base):
    appstore.nueva(bundle, nombre)
    v, _ = appstore.editable(appstore.app_of(bundle)["id"])
    if v["attributes"]["versionString"] != nombre:  # a prepared version that never went out: renamed
        appstore.version(bundle, nombre)
    appstore.ficha(bundle, repo, False)
    # The listing in the repo is the one that goes out; screenshots are replaced only if they changed since
    # the version this store has (replacing them every week is slow and changes nothing).
    appstore.capturas(bundle, repo, False, bool(base) and cambia(repo, base, f"v{nombre}", "store/screenshots/iphone"))
    appstore.novedades(bundle, repo, False, notas)
    appstore.notas(bundle, repo, False)
    appstore.build(bundle, code)
    appstore.enviar(bundle, False)


def lunes(repo):
    app, clave, paquete, bundle = env("APP"), env("CLAVE"), env("PAQUETE"), env("BUNDLE")
    tag = ultima_etiqueta(repo)
    nombre, code = version_en(repo, tag)
    git(repo, "checkout", "-q", tag)  # listing, screenshots and notes as they were built, not today's main
    msgs, ahora = leer_topic(), datetime.datetime.now(datetime.timezone.utc).timestamp()
    pre = "PRUEBA, no ha salido nada de verdad. " if SECO else ""
    # Dry, the stop order to look for is the one of the dry Sunday notice: the version it would have made
    anunciada = subir_minor((pathlib.Path(repo) / env("VERSION_FILE")).read_text(encoding="utf-8"))[1] if SECO else nombre
    if parada(msgs, clave, anunciada):
        ntfy({"title": f"{pre}{app} {anunciada} parada esta semana", "priority": 3, "click": ejecucion(),
              "message": "No sale en ninguna tienda.\n\nPor que: pulsaste 'Parar esta semana' en el aviso del domingo.\n"
                         "Que hacer: nada. El domingo que viene vuelve a salir, con lo que haya en main para entonces."})
        if not SECO:
            publicar(f"tren-lunes {clave} " + json.dumps({"version": nombre, "code": code, "el": int(ahora), "play": "parada", "ios": "parada"}))
        return
    res = {}
    try:
        pistas = play.tracks(paquete)
        prod, alpha = produccion_play(pistas), alpha_play(pistas)
        if prod >= code and not SECO:
            res["play"] = ("ya", f"ya tiene la {code}")
        elif code not in alpha and not SECO:
            res["play"] = ("no", f"la {nombre} ({code}) no esta en alpha: el domingo no se subio (lo decia el aviso del domingo).")
        else:
            b, cancelar = bloqueo_play(estado_play(msgs, paquete), ahora, code)
            if b and not SECO:
                res["play"] = ("no", b)
            else:
                play.promover(paquete, code if code in alpha else max(alpha), cancelar, dry=SECO)
                res["play"] = ("sale", "en produccion al 100 %; Google la revisa (suele tardar horas) y sale sola"
                               + (f". En seco, validado con la {code if code in alpha else max(alpha)}" + (f"; de verdad se saltaria: {b}" if b else "") if SECO else ""))
    except SystemExit as e:
        m = str(e.code)
        res["play"] = ("fallo", "Google tiene cambios en revision y no los piso. Que hacer: nada, el lunes siguiente." if "CHANGES_ALREADY_IN_REVIEW" in m
                       else f"{m[:300]}. Que hacer: mira la ejecucion (tocar el aviso); arreglado, gh workflow run tren.yml -R {os.environ.get('GITHUB_REPOSITORY')} -f paso=lunes -f seco=false")
    try:
        vers = ios_versiones(bundle)
        enviada, b = enviada_ios(vers), bloqueo_ios(vers)
        tf = ios_builds(bundle, nombre).get(code)
        if enviada >= code:
            res["ios"] = ("ya", f"ya tiene la {code}")
        elif b:
            res["ios"] = ("no", b)
        elif tf != "VALID":
            res["ios"] = ("no", f"la build {code} de la {nombre} no esta lista en TestFlight ({tf or 'no subida'}). Que hacer: si el domingo fallo la subida, el lunes siguiente sale la nueva.")
        else:
            n = notas_para(repo, enviada, tag)
            if SECO:
                res["ios"] = ("sale", f"en seco: crearia la {nombre} (publicacion al aprobarse), ficha y novedades ({n['carpeta']}), build {code} y la enviaria a revision")
            else:
                publicar_ios(repo, bundle, nombre, code, n["carpeta"], etiqueta_de(repo, enviada))
                res["ios"] = ("sale", "enviada a revision; Apple suele tardar 1 o 2 dias y la publica sola al aprobarla")
    except SystemExit as e:
        res["ios"] = ("fallo", f"{str(e.code)[:300]}. Que hacer: mira la ejecucion (tocar el aviso) y App Store Connect; arreglado, gh workflow run tren.yml -R {os.environ.get('GITHUB_REPOSITORY')} -f paso=lunes -f seco=false")
    lineas = [f"{t}: {res[k][1]}" for t, k in (("Google Play", "play"), ("iPhone", "ios"))]
    print("\n".join(lineas))
    if not SECO:
        publicar(f"tren-lunes {clave} " + json.dumps({"version": nombre, "code": code, "el": int(ahora), **{k: v[0] for k, v in res.items()}}))
    if all(v[0] == "ya" for v in res.values()) and not SECO:
        return  # nothing new this week (no train on Sunday): no notice
    salio = [t for t, k in (("Google Play", "play"), ("iPhone", "ios")) if res[k][0] == "sale"]
    mal = any(v[0] in ("no", "fallo") for v in res.values())
    ntfy({"title": f"{pre}{app} {nombre}: " + (f"sale en {' y '.join(salio)}" if salio else "no ha salido"),
          "priority": 4 if mal else 3, "click": ejecucion(),
          "message": "\n".join(lineas) + "\n\nPor que: es el tren de los lunes; una tienda con la version anterior en revision, rechazada o sin publicar se salta la semana.\n"
                     + ("Que hacer: lo que dice cada tienda arriba." if mal else "Que hacer: nada.")})


def fallo(paso):
    ntfy({"title": f"El tren de {env('APP')} fallo ({paso})", "priority": 4, "click": ejecucion(),
          "message": f"La ejecucion del {paso} se corto antes de acabar.\n\nPor que: un error de la API de una tienda, de ntfy o de git; "
                     "el detalle esta en el paso en rojo (tocar este aviso).\n"
                     f"Que hacer: pasale este aviso a Claude, o relanzala cuando se arregle: gh workflow run tren.yml -R {os.environ.get('GITHUB_REPOSITORY')} -f paso={paso} -f seco=false"})


# ---------- self-check ----------
def prueba():
    t = 'android {\n    versionCode = 24\n    versionName = "1.2.4"\n}'
    assert version(t) == ("1.2.4", 24) and subir_minor(t)[1:] == ("1.3.0", 25)
    assert subir_minor('const val versionCode = 66\nconst val versionName = "2.5.0"')[1:] == ("2.6.0", 67)
    assert subir_minor('versionCode = 9\nversionName = "1.8"')[1:] == ("1.9.0", 10)
    global SECO
    msgs = [{"message": "parar tren flowtime 2.6.0"}, {"message": "aprobar 2026-10-26 x todo"}]
    assert parada(msgs, "flowtime", "2.6.0") and not parada(msgs, "flowtime", "2.7.0") and not parada(msgs, "chroma", "2.6.0")
    SECO = True
    assert not parada(msgs, "flowtime", "2.6.0") and orden("chroma", "1.3.0") == "parar prueba chroma 1.3.0"
    SECO = False
    ahora = datetime.datetime(2026, 10, 26, 7, 7, tzinfo=datetime.timezone.utc).timestamp()
    est = lambda e, items=(), h=0.3: {"leido": datetime.datetime.fromtimestamp(ahora - h * 3600, datetime.timezone.utc).isoformat(),
                                      "estado": e, "items": [list(i) for i in items]}
    assert bloqueo_play(est("libre"), ahora) == (None, False)
    assert bloqueo_play(None, ahora)[0].startswith("no se") and bloqueo_play(est("libre", h=4), ahora)[0].startswith("no se")
    rev = est("revision", [("Producción", "66 (2.5.0)", "Iniciar lanzamiento completo")])
    assert "en revision" in bloqueo_play(rev, ahora, 67)[0]
    propia = est("revision", [("Prueba cerrada - Alpha", "67 (2.6.0)")])
    assert bloqueo_play(propia, ahora, 67) == (None, True) and bloqueo_play(propia, ahora)[0]  # Sunday: blocked
    mixta = est("revision", [("Prueba cerrada - Alpha", "67 (2.6.0)"), ("Ficha de Play Store", "es-ES")])
    assert bloqueo_play(mixta, ahora, 67)[0] and "rechazados" in bloqueo_play(est("rechazada"), ahora)[0]
    assert estado_play([{"time": 1, "message": 'tren-play com.x {"estado":"libre"}'}, {"time": 2, "message": 'tren-play com.x {"estado":"revision"}'},
                        {"time": 3, "message": 'tren-play com.y {"estado":"libre"}'}], "com.x")["estado"] == "revision"
    vers = [("2.4.0", "WAITING_FOR_REVIEW", 63), ("2.2.2", "READY_FOR_SALE", 60)]
    assert "WAITING_FOR_REVIEW" in bloqueo_ios(vers) and enviada_ios(vers) == 63
    assert bloqueo_ios([("2.6.0", "PREPARE_FOR_SUBMISSION", 67), ("2.4.0", "READY_FOR_SALE", 63)]) is None
    assert enviada_ios([("2.6.0", "PREPARE_FOR_SUBMISSION", 67), ("2.4.0", "READY_FOR_SALE", 63)]) == 63
    assert "Resolution Center" in bloqueo_ios([("1.2", "REJECTED", 20)])
    # The Sunday notice: the button of a dry run never posts a real stop order; a real one posts tren-plan
    import tempfile
    global ntfy, publicar
    enviado, guardados = [], (ntfy, publicar)
    ntfy, publicar = enviado.append, enviado.append
    t = {"falta": True, "sale": True, "subir": True, "bloqueo": None, "notas": {"carpeta": NOTAS, "generica": False, "base": None, "texto": "Nuevo"}}
    for seco, cuerpo, n in ((True, "parar prueba chroma 1.3.0", 1), (False, "parar tren chroma 1.3.0", 2)):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"app": "Chroma", "clave": "chroma", "version": "1.3.0", "code": 26, "cambios": 3, "ultima": "v1.2.4", "seco": seco, "play": t, "ios": t}, f)
        os.environ.update(PLAN_FILE=f.name, NTFY_TOPIC_APROBAR="T", PLAY_RESULT="success", IOS_RESULT="failure")
        enviado.clear(); aviso()
        assert enviado[0]["actions"][0]["body"] == cuerpo and len(enviado) == n and "iPhone: NO sale" in enviado[0]["message"]
        assert enviado[0]["message"].startswith("Novedades:\nNuevo\n")
    os.environ["PLAY_RESULT"] = "failure"  # nothing goes out because an upload failed: say relaunch, not "next Sunday"
    enviado.clear(); aviso()
    assert "no sale nada" in enviado[0]["title"] and "Relanzada" in enviado[0]["message"] and "sin tienda libre" not in enviado[0]["message"]
    ntfy, publicar = guardados
    SECO = False
    print("prueba: todo bien")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--prueba"]:
        prueba()
    elif len(a) == 2 and a[0] in ("domingo", "lunes"):
        {"domingo": domingo, "lunes": lunes}[a[0]](a[1])
    elif a == ["aviso"]:
        aviso()
    elif a == ["eco"]:
        eco()
    elif len(a) == 2 and a[0] == "fallo":
        fallo(a[1])
    else:
        sys.exit(__doc__)
