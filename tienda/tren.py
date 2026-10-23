#!/usr/bin/env python3
"""The weekly release train (PUBLICAR.md, section 7). Run by .github/workflows/tren.yml, which each
app calls from its own tren.yml:

    tren.py domingo <repo>   Sunday: is there a new version? numbers it (minor + 1), commits and tags it,
                             and says what to upload: Play alpha and TestFlight
    tren.py aviso            Sunday, after the uploads: the ntfy with the "Parar esta semana" button
    tren.py eco              night: posts the stop orders again (ntfy keeps 12 h: one pressed on Sunday
                             at 20:00 would be gone by Monday at 9:00)
    tren.py lunes <repo>     Monday: Sunday's build to production on Play and to App Review, unless stopped
                             or a store still has the previous version in review, rejected or pending:
                             both stores or neither (a_la_par), so the two always carry the same version
    tren.py fallo <paso>     notice when a step broke before its own notice
    tren.py --prueba         checks without network

Each scheduled step has a reserve run two or three hours later (PRIMERA): GitHub may drop a scheduled run,
and what failed for a passing reason (an upload, an API, an unknown Play state) gets a second try there
before it sounds. A reserve run after a good first one does nothing. Notices sound only for a rejection
(Apple or Google) or a failure the reserve run did not fix; the rest arrives silent (ntfy priority 2):
what goes out, and a store waiting for a review or a release, where there is nothing to do.

Everything comes from the environment: APP (FlowTime), CLAVE (flowtime), PAQUETE, BUNDLE, VERSION_FILE,
NTFY_TOPIC (notices), NTFY_TOPIC_APROBAR (secret topic: stop orders, the Play state the GK35 reads
from Play Console and the train's own messages for semana.baltajmn.dev), SECO=true for a dry run, and
the credentials play.py and appstore.py read. Dry: nothing is pushed to main, no tag, Play only
validates, nothing changes in App Store Connect, notices say PRUEBA and stop orders say "parar prueba".
"""
import datetime, json, os, pathlib, re, subprocess, sys, time, urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import appstore, play  # noqa: E402

NOTAS, GENERICAS = "store/whatsnew", "store/whatsnew-generic"
# A week with only these changes makes no version: documentation, workflows and IDE files.
FUERA = [":(exclude)*.md", ":(exclude).github", ":(exclude).idea"]
FRESCO = 3 * 3600  # the GK35 reads Play Console 20 minutes before each train; older than 3 h, unknown
DESCONOCIDO = "no se en que estado esta Play"
# The first scheduled run of each step (cron of the app's tren.yml) and the time of its reserve run.
PRIMERA = {"37 19 * * 0": "21:37", "17 1 * * 1": "4:17", "7 9 * * 1": "11:07"}
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


def subir_minor(texto, usado=0):
    """Every train: minor + 1 and patch 0 (the patch is for urgent fixes, by hand). versionCode: the highest
    one used anywhere (the file, Play, App Store Connect with TestFlight) + 1, so a TestFlight test build
    needs no bookkeeping."""
    code = max(version(texto)[1], usado) + 1
    texto = NOMBRE.sub(lambda m: f"{m[1]}{m[2]}.{int(m[3]) + 1}.0{m[5]}", texto, count=1)
    texto = CODIGO.sub(lambda m: f"{m[1]}{code}", texto, count=1)
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
def _ntfy(req):
    for intento in range(3):  # ntfy.sh fails now and then: two retries
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError) as e:
            if intento == 2 or (isinstance(e, urllib.error.HTTPError) and e.code < 500):
                raise
            time.sleep(10 * (intento + 1))


def ntfy(msg):
    _ntfy(urllib.request.Request("https://ntfy.sh/", json.dumps({"topic": env("NTFY_TOPIC"), **msg}).encode(), method="POST"))


def publicar(texto):
    """A line on the secret topic (stop orders, the train's own messages)."""
    _ntfy(urllib.request.Request(f"https://ntfy.sh/{env('NTFY_TOPIC_APROBAR')}", texto.encode(), method="POST"))


def leer_topic():
    out = _ntfy(urllib.request.Request(f"https://ntfy.sh/{env('NTFY_TOPIC_APROBAR')}/json?poll=1&since=12h"))
    return [m for m in map(json.loads, out.decode().splitlines()) if m.get("event") == "message"]


def del_tren(msgs, tipo, clave, nombre=None):
    """The newest "tren-<tipo> <clave> <json>" on the topic (of that version, if given), or None."""
    for m in sorted(msgs, key=lambda m: m.get("time", 0), reverse=True):
        t = (m.get("message") or "").strip()
        if t.startswith(f"tren-{tipo} {clave} "):
            try:
                d = json.loads(t.split(" ", 2)[2])
            except ValueError:
                continue
            if nombre is None or d.get("version") == nombre:
                return d
    return None


def reserva():
    """The reserve run's time when this is the first scheduled run of its step, else None."""
    try:
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
            return PRIMERA.get(json.load(f).get("schedule"))
    except (KeyError, OSError, ValueError):
        return None


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
    return del_tren(msgs, "play", paquete)


def bloqueo_play(est, ahora, code=None):
    """(reason, cancel) for Play. `code`: the train's own alpha release, which may still be in review on
    Monday morning; if it is the only thing in review, promoting cancels and resends it with production."""
    que_hacer_gk = "en el GK35, node ~/AndroidStudioProjects/TikTok/versiones.mjs play (si dice 'Sin sesion', login de play por noVNC)."
    if not est:
        return f"{DESCONOCIDO}: el GK35 no lo ha publicado en las ultimas 12 h. Que hacer: {que_hacer_gk}", False
    leido = datetime.datetime.fromisoformat(est["leido"].replace("Z", "+00:00")).timestamp()
    if ahora - leido > FRESCO:
        return f"{DESCONOCIDO}: lo ultimo que leyo el GK35 es de hace {int((ahora - leido) / 3600)} h. Que hacer: {que_hacer_gk}", False
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


def a_la_par(p, i):
    """Both stores or neither: the two always carry the same version (user, 23-10-2026). `p`, `i`: why Play
    and the iPhone cannot go out (None: they can); a free store waits for the blocked one."""
    if bool(p) == bool(i):
        return p, i
    return p or f"espera al iPhone, que no sale: {i}", i or f"espera a Google Play, que no sale: {p}"


def juntas(paquete, bundle, nombre, code, msgs, ahora):
    """Monday, before anything goes out: {store: why it waits}, or {} when both can go (or one of them
    already has this version, and the other catches up alone)."""
    pistas, vers = play.tracks(paquete), ios_versiones(bundle)
    if produccion_play(pistas) >= code or enviada_ios(vers) >= code:
        return {}
    p = bloqueo_play(estado_play(msgs, paquete), ahora, code)[0] or (
        None if code in alpha_play(pistas) else f"la {nombre} ({code}) no esta en alpha: el domingo no se subio.")
    i = bloqueo_ios(vers) or (
        None if ios_builds(bundle, nombre).get(code) == "VALID" else f"la build {code} de la {nombre} no esta lista en TestFlight.")
    p, i = a_la_par(p, i)
    return {"play": p, "ios": i} if p else {}


def grave(motivo):
    """A block that sounds: a rejection, or Play state the GK35 cannot read. In review or pending, silent."""
    return any(x in (motivo or "") for x in ("REJECTED", "INVALID_BINARY", "rechazad", DESCONOCIDO, "no se lee bien"))


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


def ios_max_build(bundle):
    """The highest build number App Store Connect has, TestFlight test builds included."""
    a = appstore.app_of(bundle)["id"]
    return max([int(v) for b in appstore.get_all(f"/v1/builds?filter[app]={a}&fields[builds]=version&limit=200")
                if (v := b["attributes"]["version"]).isdigit()] or [0])


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
    msgs, ahora = leer_topic(), datetime.datetime.now(datetime.timezone.utc).timestamp()
    pistas, vers = play.tracks(paquete), ios_versiones(bundle)
    prod_play, alpha, env_ios = produccion_play(pistas), alpha_play(pistas), enviada_ios(vers)
    # The reserve run of a Sunday whose first run already made the version: the same one, only what is
    # missing (an upload that failed, Play now readable), never a second version
    hecha = (not SECO and os.environ.get("GITHUB_EVENT_NAME") == "schedule"
             and ahora - int(git(repo, "log", "-1", "--format=%ct", ultima)) < 6 * 3600)
    hasta, base = (ultima, git(repo, "describe", "--tags", "--abbrev=0", "--match", "v*", f"{ultima}^", check=False) or ultima) if hecha else ("HEAD", ultima)
    cambios = git(repo, "diff", "--name-only", base, hasta, "--", ".", *FUERA).split()
    nuevo = None
    if (cambios and not hecha) or SECO:  # dry runs always build, to test the whole way
        usado = max([play.usados(paquete), ios_max_build(bundle)] + [int(c) for rs in pistas.values() for r in rs for c in r.get("versionCodes", [])])
        nuevo, nombre, code = subir_minor(texto, usado)
    else:
        nombre, code = version_en(repo, ultima)
    b_play, _ = bloqueo_play(estado_play(msgs, paquete), ahora, code)
    b_ios = bloqueo_ios(vers)
    falta_play, falta_ios = prod_play < code, env_ios < code
    if falta_play and falta_ios:
        b_play, b_ios = a_la_par(b_play, b_ios)
    sale_play, sale_ios = falta_play and not b_play, falta_ios and not b_ios
    if cambios and not SECO and not (sale_play or sale_ios):
        nuevo = None  # both stores blocked: no version is spent; next Sunday it is tried again
    tf = {} if nuevo else ios_builds(bundle, nombre)
    plan = {
        "app": app, "clave": clave, "version": nombre, "code": code, "nueva": bool(nuevo), "cambios": len(cambios),
        "ultima": base, "seco": SECO, "reintento": reserva(),
        "play": {"falta": falta_play, "sale": sale_play, "bloqueo": b_play, "produccion": prod_play,
                 "subir": SECO or (sale_play and code not in alpha),
                 "notas": notas_para(repo, prod_play, hasta)},
        "ios": {"falta": falta_ios, "sale": sale_ios, "bloqueo": b_ios, "enviada": env_ios,
                "subir": SECO or (falta_ios and code not in tf and (sale_play or sale_ios)),
                "notas": notas_para(repo, env_ios, hasta)},
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
    subir = plan["play"]["subir"] or plan["ios"]["subir"]
    # Announced already (the reserve run): again only if it uploads something or the first one said it would retry
    ya = None if SECO else del_tren(msgs, "plan", clave, nombre)
    pathlib.Path(env("PLAN_FILE", os.devnull)).write_text(json.dumps(plan), encoding="utf-8")
    salida(ref=ref, play=plan["play"]["subir"], ios=plan["ios"]["subir"], **{"notas-play": plan["play"]["notas"]["carpeta"]},
           aviso=SECO or subir or (not ya and (bool(cambios) or hay)) or bool(ya and ya.get("reintento")))


def linea(tienda, t, resultado, code, luego=None):
    if not t["falta"]:
        return f"{tienda}: ya tiene la {code}."
    if t["subir"] and resultado not in ("success", ""):
        if luego:
            return f"{tienda}: NO sale todavia, la subida del domingo fallo ({resultado}). Que hacer: nada, se reintenta sola a las {luego}."
        return f"{tienda}: NO sale, la subida del domingo fallo ({resultado}). Que hacer: abre la ejecucion (tocar el aviso), mira el paso en rojo y relanzala con 'Re-run failed jobs' antes del lunes a las 9:00."
    if not t["sale"]:
        return f"{tienda}: NO sale, {t['bloqueo']}" + (f" Se vuelve a mirar sola a las {luego}." if luego and DESCONOCIDO in t["bloqueo"] else "")
    return f"{tienda}: sale" + (" (subida hoy)" if t["subir"] else " (ya estaba subida)")


def aviso():
    global SECO
    p = json.loads(pathlib.Path(env("PLAN_FILE")).read_text(encoding="utf-8"))
    SECO = p["seco"]  # the stop button of a dry notice says "parar prueba"
    luego = p.get("reintento")  # the reserve run's time, when this is the first run of the Sunday
    res = {"play": os.environ.get("PLAY_RESULT", ""), "ios": os.environ.get("IOS_RESULT", "")}
    fallidas = [k for k in ("play", "ios") if p[k]["subir"] and res[k] not in ("success", "")]
    for k in fallidas:  # a failed upload means that store does not go out on Monday
        p[k]["sale"] = False
    if p["play"]["falta"] and p["ios"]["falta"] and p["play"]["sale"] != p["ios"]["sale"]:
        k = "play" if p["play"]["sale"] else "ios"  # the other one's upload failed: it waits (a_la_par)
        p[k]["sale"], p[k]["bloqueo"] = False, "espera al iPhone, que no sale." if k == "play" else "espera a Google Play, que no sale."
    lineas = [linea("Google Play", p["play"], res["play"], p["code"], luego), linea("iPhone", p["ios"], res["ios"], p["code"], luego)]
    # Sounds for a failed upload or a grave block (rejection, Play unreadable), but in the first run a
    # failed upload or an unreadable Play wait, silent, for the reserve run first
    mal = [k for k in ("play", "ios") if p[k]["falta"] and not p[k]["sale"]]
    pasajero = lambda k: k in fallidas or DESCONOCIDO in (p[k]["bloqueo"] or "")
    reintenta = bool(luego and any(pasajero(k) for k in mal))
    prioridad = 4 if any(not (luego and pasajero(k)) and (k in fallidas or grave(p[k]["bloqueo"])) for k in mal) else 2
    n = {k: p[k]["notas"] for k in ("play", "ios")}
    nota = lambda x: (f" genericas, porque nadie cambio store/whatsnew desde la {x['base'].lstrip('v')}" if x["generica"] else "") + ":\n" + x["texto"]
    novedades = (f"Novedades{nota(n['play'])}" if n["play"]["carpeta"] == n["ios"]["carpeta"]
                 else f"Novedades en Play{nota(n['play'])}\n\nNovedades en iPhone{nota(n['ios'])}")
    pre = "PRUEBA, no sale nada de verdad. " if p["seco"] else ""
    if p["play"]["sale"] or p["ios"]["sale"]:
        que = [x for x, k in (("en Google Play, a produccion al 100 %", "play"), ("en iPhone, a revision de Apple, que la publica sola al aprobarla", "ios")) if p[k]["sale"]]
        msg = {"title": f"{pre}Mañana sale {p['app']} {p['version']}", "priority": prioridad,
               "message": f"{novedades}\n\n" + "\n".join(lineas) + "\n\n"
               f"Por que: es el tren semanal. El domingo se prepara lo que hay en main ({p['cambios']} ficheros cambiados desde la {p['ultima'].lstrip('v')}) "
               f"y el lunes a las 9:00 sale {' y '.join(que)}.\n"
               "Que hacer: nada si te parece bien. 'Parar esta semana' la frena en las dos tiendas hasta el domingo que viene; vale hasta el lunes a las 9:00. "
               "Las novedades ya estan fijadas: aunque se toque store/whatsnew antes del lunes, salen las de arriba.",
               "actions": [{"action": "http", "label": "Parar esta semana", "url": f"https://ntfy.sh/{env('NTFY_TOPIC_APROBAR')}",
                            "method": "POST", "body": orden(p["clave"], p["version"]), "clear": True}]}
    else:
        msg = {"title": f"{pre}{p['app']}: mañana no sale nada" + (" (de momento)" if reintenta else ""), "priority": prioridad,
               "message": "\n".join(lineas) + (
                   f"\n\nPor que: algo pasajero, lo de arriba.\nQue hacer: lo que dice cada tienda arriba. Lo pasajero se reintenta solo a las {luego}; si sigue sin salir, llega otro aviso, con sonido."
                   if reintenta else
                   "\n\nPor que: la subida del domingo fallo, y una tienda sin build del domingo no sale el lunes.\n"
                   "Que hacer: lo que dice cada tienda arriba. Relanzada antes del lunes a las 9:00, llega otro aviso y sale; si no, el domingo que viene va con lo que haya en main."
                   if fallidas else
                   "\n\nPor que: el tren semanal no pone una version encima de otra que aun esta en revision, rechazada o sin publicar; las dos tiendas salen juntas o ninguna, y sin salida no gasta numero.\n"
                   "Que hacer: lo que dice cada tienda arriba. El domingo que viene se vuelve a intentar con lo que haya en main.")}
    msg["click"] = ejecucion()
    ntfy(msg)
    if not p["seco"]:
        publicar(f"tren-plan {p['clave']} " + json.dumps({"version": p["version"], "code": p["code"], "el": int(datetime.datetime.now().timestamp()),
                                                          "play": p["play"]["sale"] and (p["play"]["bloqueo"] or "sale"), "ios": p["ios"]["sale"] and (p["ios"]["bloqueo"] or "sale"),
                                                          "reintento": reintenta, "motivos": lineas}, ensure_ascii=False))
    print(msg["title"], "\n", msg["message"])


def eco():
    msgs, clave = leer_topic(), env("CLAVE")
    vistas = {(m.get("message") or "").strip() for m in msgs}
    for o in sorted(o for o in vistas if re.fullmatch(rf"parar {'prueba' if SECO else 'tren'} {re.escape(clave)} [\d.]+", o)):
        publicar(o)
        print("eco:", o)
    plan = None if SECO else del_tren(msgs, "plan", clave)
    if plan:  # Monday reads it to know what Sunday already said (no second sound for the same thing)
        publicar(f"tren-plan {clave} " + json.dumps(plan, ensure_ascii=False))
        print("eco: tren-plan", plan["version"])


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
    luego = reserva()  # passing troubles wait for the reserve run before they sound
    prev = None if SECO else del_tren(msgs, "lunes", clave, nombre)  # the 9:07 run, seen from the reserve one
    # Dry, the stop order to look for is the one of the dry Sunday notice: the version it would have made
    anunciada = subir_minor((pathlib.Path(repo) / env("VERSION_FILE")).read_text(encoding="utf-8"))[1] if SECO else nombre
    if parada(msgs, clave, anunciada):
        if prev and prev.get("play") == "parada":
            return
        ntfy({"title": f"{pre}{app} {anunciada} parada esta semana", "priority": 2, "click": ejecucion(),
              "message": "No sale en ninguna tienda.\n\nPor que: pulsaste 'Parar esta semana' en el aviso del domingo.\n"
                         "Que hacer: nada. El domingo que viene vuelve a salir, con lo que haya en main para entonces."})
        if not SECO:
            publicar(f"tren-lunes {clave} " + json.dumps({"version": nombre, "code": code, "el": int(ahora), "play": "parada", "ios": "parada"}))
        return
    res = {}
    if not SECO:  # both stores or neither (a_la_par); dry runs try both, to test the whole way
        try:
            res = {k: ("no", t) for k, t in juntas(paquete, bundle, nombre, code, msgs, ahora).items()}
        except SystemExit as e:
            res = {k: ("fallo", f"{str(e.code)[:300]}. Que hacer: mira la ejecucion (tocar el aviso); arreglado, gh workflow run tren.yml -R {os.environ.get('GITHUB_REPOSITORY')} -f paso=lunes -f seco=false") for k in ("play", "ios")}
    if not res:
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
            res["play"] = (("no", "Google tiene cambios en revision y no los piso. Que hacer: nada, el lunes siguiente.") if "CHANGES_ALREADY_IN_REVIEW" in m
                           else ("fallo", f"{m[:300]}. Que hacer: mira la ejecucion (tocar el aviso); arreglado, gh workflow run tren.yml -R {os.environ.get('GITHUB_REPOSITORY')} -f paso=lunes -f seco=false"))
        try:
            vers = ios_versiones(bundle)
            enviada, b = enviada_ios(vers), bloqueo_ios(vers)
            tf = ios_builds(bundle, nombre).get(code)
            if enviada >= code:
                res["ios"] = ("ya", f"ya tiene la {code}")
            elif res["play"][0] not in ("sale", "ya") and not SECO:  # Play failed after juntas() said yes
                res["ios"] = ("no", f"espera a Google Play, que no sale: {res['play'][1]}")
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
    if luego:
        for k, (e, t) in list(res.items()):
            if e == "fallo" or (e == "no" and DESCONOCIDO in t):
                res[k] = ("espera", f"{t} Se reintenta sola a las {luego}.")
    lineas = [f"{t}: {res[k][1]}" for t, k in (("Google Play", "play"), ("iPhone", "ios"))]
    print("\n".join(lineas))
    if not SECO:
        publicar(f"tren-lunes {clave} " + json.dumps({"version": nombre, "code": code, "el": int(ahora), **{k: v[0] for k, v in res.items()}}))
    nuevo, prioridad = avisar_lunes(res, prev, None if SECO else del_tren(msgs, "plan", clave, nombre))
    if not nuevo and not SECO:
        return  # nothing new: no train on Sunday, or the reserve run after a good 9:07 one
    salio = [t for t, k in (("Google Play", "play"), ("iPhone", "ios")) if res[k][0] == "sale"]
    mal = any(v[0] in ("no", "fallo") for v in res.values())
    ntfy({"title": f"{pre}{app} {nombre}: " + (f"sale en {' y '.join(salio)}" if salio else "no ha salido"),
          "priority": prioridad, "click": ejecucion(),
          "message": "\n".join(lineas) + "\n\nPor que: es el tren de los lunes; una tienda con la version anterior en revision, rechazada o sin publicar se salta la semana, y la otra la espera: las dos llevan siempre la misma version.\n"
                     + ("Que hacer: lo que dice cada tienda arriba." if mal else "Que hacer: nada.")})


def avisar_lunes(res, prev, plan):
    """What is new for the Monday notice, and its priority. New: what changed since the 9:07 run (prev),
    or everything but "ya" on the first one. It sounds for a failure, or for a grave block (rejection,
    Play unreadable) Sunday's plan did not know about (or there is no plan to know); a store in review
    or pending, one Sunday already said would not go out, what went out and what waits for the reserve
    run stay silent."""
    antes = prev or {}
    nuevo = {k: v for k, v in res.items() if antes.get(k) == "espera" or (v[0] != "ya" and antes.get(k) != v[0])}
    fuerte = any(e == "fallo" or (e == "no" and grave(t) and (not plan or plan.get(k))) for k, (e, t) in nuevo.items())
    return nuevo, 4 if fuerte else 2


def fallo(paso):
    luego = reserva()
    ntfy({"title": f"El tren de {env('APP')} fallo ({paso})", "priority": 2 if luego else 4, "click": ejecucion(),
          "message": f"La ejecucion del {paso} se corto antes de acabar.\n\nPor que: un error de la API de una tienda, de ntfy o de git; "
                     "el detalle esta en el paso en rojo (tocar este aviso).\n"
                     + (f"Que hacer: nada por ahora. A las {luego} se reintenta sola; si vuelve a fallar, llega otro aviso, con sonido." if luego else
                        f"Que hacer: pasale este aviso a Claude, o relanzala cuando se arregle: gh workflow run tren.yml -R {os.environ.get('GITHUB_REPOSITORY')} -f paso={paso} -f seco=false")})


# ---------- self-check ----------
def prueba():
    t = 'android {\n    versionCode = 24\n    versionName = "1.2.4"\n}'
    assert version(t) == ("1.2.4", 24) and subir_minor(t)[1:] == ("1.3.0", 25)
    assert subir_minor('const val versionCode = 66\nconst val versionName = "2.5.0"')[1:] == ("2.6.0", 67)
    assert subir_minor('versionCode = 9\nversionName = "1.8"')[1:] == ("1.9.0", 10)
    assert subir_minor(t, 30)[2] == 31 and subir_minor(t, 3)[2] == 25  # the highest used anywhere + 1
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
    assert a_la_par(None, None) == (None, None) and a_la_par("x", "y") == ("x", "y")
    assert a_la_par(None, "la 1.2 esta en REJECTED.") == ("espera al iPhone, que no sale: la 1.2 esta en REJECTED.", "la 1.2 esta en REJECTED.")
    p, i = a_la_par(DESCONOCIDO + ": x", None)
    assert i.startswith("espera a Google Play") and DESCONOCIDO in i and grave(i)  # unknown Play: the iPhone waits, and retries
    # The Sunday notice: the button of a dry run never posts a real stop order; a real one posts tren-plan
    import tempfile
    global ntfy, publicar
    enviado, guardados = [], (ntfy, publicar)
    ntfy, publicar = enviado.append, enviado.append
    t = {"falta": True, "sale": True, "subir": True, "bloqueo": None, "notas": {"carpeta": NOTAS, "generica": False, "base": None, "texto": "Nuevo"}}
    for seco, cuerpo, n in ((True, "parar prueba chroma 1.3.0", 1), (False, "parar tren chroma 1.3.0", 2)):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"app": "Chroma", "clave": "chroma", "version": "1.3.0", "code": 26, "cambios": 3, "ultima": "v1.2.4", "seco": seco, "play": t, "ios": t}, f)
        os.environ.update(PLAN_FILE=f.name, NTFY_TOPIC_APROBAR="T", PLAY_RESULT="success", IOS_RESULT="success")
        enviado.clear(); aviso()
        assert enviado[0]["actions"][0]["body"] == cuerpo and len(enviado) == n
        assert enviado[0]["message"].startswith("Novedades:\nNuevo\n")
    os.environ["IOS_RESULT"] = "failure"  # one upload failed: the other store waits for it (a_la_par)
    enviado.clear(); aviso()
    assert "no sale nada" in enviado[0]["title"] and "Google Play: NO sale, espera al iPhone" in enviado[0]["message"] and "actions" not in enviado[0]
    os.environ["PLAY_RESULT"] = "failure"  # nothing goes out because an upload failed: say relaunch, not "next Sunday"
    enviado.clear(); aviso()
    assert "no sale nada" in enviado[0]["title"] and "Relanzada" in enviado[0]["message"] and "sin tienda libre" not in enviado[0]["message"]
    assert enviado[0]["priority"] == 4 and '"reintento": false' in enviado[1]
    # The first run of the Sunday: a failed upload waits, silent, for the reserve run; a store in review sounds
    with open(f.name, "w") as g:
        json.dump({"app": "Chroma", "clave": "chroma", "version": "1.3.0", "code": 26, "cambios": 3, "ultima": "v1.2.4", "seco": False,
                   "reintento": "21:37", "play": t, "ios": t}, g)
    enviado.clear(); aviso()
    assert enviado[0]["priority"] == 2 and "21:37" in enviado[0]["message"] and '"reintento": true' in enviado[1]
    with open(f.name, "w") as g:
        json.dump({"app": "Chroma", "clave": "chroma", "version": "1.3.0", "code": 26, "cambios": 3, "ultima": "v1.2.4", "seco": False, "reintento": "21:37",
                   "play": {**t, "sale": False, "subir": False, "bloqueo": "espera al iPhone, que no sale: la 1.2 esta en WAITING_FOR_REVIEW."}, "ios": {**t, "sale": False, "subir": False, "bloqueo": "la 1.2 esta en WAITING_FOR_REVIEW."}}, g)
    enviado.clear(); aviso()
    assert enviado[0]["priority"] == 2 and '"reintento": false' in enviado[1]  # Apple in review: Play waits too, silent
    assert "no sale nada" in enviado[0]["title"] and "Google Play: NO sale, espera al iPhone" in enviado[0]["message"]
    with open(f.name, "w") as g:
        json.dump({"app": "Chroma", "clave": "chroma", "version": "1.3.0", "code": 26, "cambios": 3, "ultima": "v1.2.4", "seco": False, "reintento": "21:37",
                   "play": {**t, "sale": False, "subir": False, "bloqueo": "espera al iPhone, que no sale: la 1.2 esta en REJECTED."}, "ios": {**t, "sale": False, "subir": False, "bloqueo": "la 1.2 esta en REJECTED."}}, g)
    enviado.clear(); aviso()
    assert enviado[0]["priority"] == 4  # a rejection sounds
    with open(f.name, "w") as g:
        json.dump({"app": "Chroma", "clave": "chroma", "version": "1.3.0", "code": 26, "cambios": 3, "ultima": "v1.2.4", "seco": False, "play": t, "ios": t}, g)
    os.environ.update(PLAY_RESULT="success", IOS_RESULT="success")
    enviado.clear(); aviso()
    assert enviado[0]["priority"] == 2  # all goes out: silent
    # Monday: what sounds
    sale, no, fallo_ = ("sale", "x"), ("no", "la 1.2 esta en REJECTED."), ("fallo", "x")
    revision = ("no", "la 2.4.0 esta en WAITING_FOR_REVIEW.")
    assert avisar_lunes({"play": sale, "ios": no}, None, {"play": "sale", "ios": False}) == ({"play": sale, "ios": no}, 2)
    assert avisar_lunes({"play": sale, "ios": no}, None, None)[1] == 4  # no plan: cannot tell it is old news
    assert avisar_lunes({"play": sale, "ios": revision}, None, None)[1] == 2  # in review: nothing to do, silent
    assert avisar_lunes({"play": ("no", DESCONOCIDO + ": x"), "ios": revision}, None, {"play": "sale", "ios": False})[1] == 4
    assert grave("tiene cambios rechazados (x)") and not grave("tiene cambios en revision (x)") and not grave("la 2.4.0 esta en PENDING_DEVELOPER_RELEASE.")
    assert avisar_lunes({"play": fallo_, "ios": ("ya", "x")}, None, {"play": "sale", "ios": False}) == ({"play": fallo_}, 4)
    assert avisar_lunes({"play": ("ya", "x"), "ios": sale}, {"play": "sale", "ios": "espera"}, None) == ({"ios": sale}, 2)
    assert avisar_lunes({"play": ("ya", "x"), "ios": no}, {"play": "sale", "ios": "no"}, None) == ({}, 2)  # reserve after a good 9:07
    assert avisar_lunes({"play": ("ya", "x"), "ios": ("ya", "x")}, None, None) == ({}, 2)
    assert del_tren([{"time": 1, "message": 'tren-plan chroma {"version": "1.3.0", "reintento": true}'},
                     {"time": 2, "message": 'tren-plan chroma {"version": "1.4.0"}'}], "plan", "chroma", "1.3.0")["reintento"]
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
