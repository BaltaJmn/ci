#!/usr/bin/env python3
"""The weekly release train (PUBLICAR.md, section 7). Run by .github/workflows/tren.yml, which each
app calls from its own tren.yml:

    tren.py domingo <repo>   Sunday from 19:00: is there a new version? numbers it (minor + 1), commits and tags
                             it, and says what to build: the Play bundle (Play validates it, it stays as the run's
                             artifact aab-<tag>) and TestFlight. Any other time it only builds what is missing
    tren.py aviso            after the builds: the ntfy with the "Parar esta semana" button
    tren.py eco              night: posts the stop orders again (ntfy keeps 12 h: one pressed on Sunday
                             at 20:00 would be gone by Monday at 9:07)
    tren.py lunes <repo>     from Monday 9:07: Sunday's bundle to Play production and the build to App Review,
                             both stores or neither (a_la_par); what waits is retried every 30 min
    tren.py fallo <paso>     notice when a step broke before its own notice
    tren.py --prueba         checks without network

Who starts it: the GK35's crontab at its Madrid times (gh workflow run), every 30 min while the last
tren-lunes says a store waits (versiones.mjs reintento), and tren.yml's schedule as a late backup. Every run
does only what is missing and tells nothing twice, so a double start changes nothing.

Monday, per store: ya (has it), sale (sent now), espera (clears alone: a review in that store, Apple's
other submission, a 409), pareja (waits for the other store), usuario (only the user can: a rejection,
unsent Play changes), fallo (an error), subir (Sunday's build is missing: the GK35 reruns domingo) and
parada. Notices are two lines, "Causa:" and "Haz:", and come once per state: usuario at once, fallo and
subir after 2 h, espera after 72 h. What goes out arrives silent (ntfy priority 2), a wait says nothing.

Play needs no Play Console reading: the commit (play.py, ERROR_IF_IN_REVIEW) fails with
CHANGES_ALREADY_IN_REVIEW while Google reviews anything, and the train retries. A fresh reading from the
GK35 (tren-play, optional) only adds what the API cannot tell: a rejection, or changes nobody sent.

Everything comes from the environment: APP (FlowTime), CLAVE (flowtime), PAQUETE, BUNDLE, VERSION_FILE,
NTFY_TOPIC (notices), NTFY_TOPIC_APROBAR (secret topic: stop orders, the GK35's Play reading and the
train's own messages), GH_TOKEN and GITHUB_REPOSITORY (the bundle artifact), SECO=true for a dry run, and
the credentials play.py and appstore.py read. Dry: nothing is pushed to main, no tag, Play only validates,
nothing changes in App Store Connect, notices say PRUEBA and stop orders say "parar prueba".
"""
import datetime, io, json, os, pathlib, re, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request, zipfile, zoneinfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import appstore, play  # noqa: E402

NOTAS, GENERICAS = "store/whatsnew", "store/whatsnew-generic"
IMAGENES_PLAY = ("store/play", "store/screenshots/play", "store/screenshots/tablet")
# A week with only these changes makes no version: documentation, workflows and IDE files.
FUERA = [":(exclude)*.md", ":(exclude).github", ":(exclude).idea"]
FRESCO = 3 * 3600  # a Play Console reading older than this tells nothing
MADRID = zoneinfo.ZoneInfo("Europe/Madrid")
# Step: (weekday, Monday 0; time of the GK35's reserve run in Madrid). Only for a step that breaks: before
# that time it stays silent. Goes with the GK35's crontab.
RESERVA = {"domingo": (6, "21:37"), "lunes": (0, "11:07")}
# How long a store is in a state before it is told (once). States not here are never told.
UMBRAL = {"usuario": 0, "fallo": 2 * 3600, "subir": 2 * 3600, "espera": 72 * 3600}
TIENDA = {"play": "Play", "ios": "iPhone"}
TITULO = {"usuario": "necesita tu mano", "fallo": "falla", "subir": "sin build del domingo", "espera": "lleva 3 dias esperando"}
NADA, CLAUDE = "nada, reintenta solo", "si sigue, pasale este aviso a Claude"
# App Store states that leave the way free; the train never piles a version on top of another.
LIBRE_IOS = {"READY_FOR_SALE", "READY_FOR_DISTRIBUTION", "REPLACED_WITH_NEW_VERSION", "REMOVED_FROM_SALE",
             "DEVELOPER_REMOVED_FROM_SALE", "PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED"}
SIN_ENVIAR = {"PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED"}  # prepared but never sent: reused
RECHAZO_IOS = {"REJECTED", "METADATA_REJECTED", "INVALID_BINARY"}
# App Review submissions (a version, a product page test, events) that keep Apple from taking another: on
# 27-10-2026 a product page test WAITING_FOR_REVIEW gave 409 ENTITY_STATE_INVALID, and the same test
# IN_REVIEW let Chroma 1.3.0 in at 13:21. A 409 anyway is retried (error_ios). UNRESOLVED_ISSUES is a rejection.
ENVIO_ABIERTO = {"WAITING_FOR_REVIEW"}


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


def cambia(repo, desde, hasta, *rutas):
    return bool(git(repo, "diff", "--name-only", desde, hasta, "--", *rutas))


def notas_para(repo, desde_code, hasta):
    """store/whatsnew, or the generic notes if nobody touched it since the version that store has.
    Per store: one may be a week behind, and its users have not read the last notes."""
    base = etiqueta_de(repo, desde_code)
    return GENERICAS if base and not cambia(repo, base, hasta, NOTAS) else NOTAS


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


def avisar(titulo, causa, haz, prioridad=3):
    """The format of every notice that asks for something: what happens in the title, two short lines."""
    ntfy({"title": ("PRUEBA " if SECO else "") + titulo, "priority": prioridad, "click": ejecucion(),
          "message": f"Causa: {causa.rstrip('.')}.\nHaz: {haz.rstrip('.')}."})


def publicar(texto):
    """A line on the secret topic (stop orders, the train's own messages)."""
    _ntfy(urllib.request.Request(f"https://ntfy.sh/{env('NTFY_TOPIC_APROBAR')}", texto.encode(), method="POST"))


def vivos(eventos):
    """The topic's messages minus the ones deleted in ntfy (DELETE /<topic>/<id>, which ntfy reports as a separate
    message_delete event): deleting a 'parar tren' order undoes it."""
    borrados = {e.get("sequence_id") for e in eventos if e.get("event") == "message_delete"}
    return [m for m in eventos if m.get("event") == "message" and (m.get("sequence_id") or m.get("id")) not in borrados]


def leer_topic():
    out = _ntfy(urllib.request.Request(f"https://ntfy.sh/{env('NTFY_TOPIC_APROBAR')}/json?poll=1&since=12h"))
    return vivos([json.loads(l) for l in out.decode().splitlines() if l.strip()])


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


# ---------- clock ----------
def reserva(paso, ahora=None):
    """The reserve run's time while it is still to come today, else None (this is the last chance). By the
    clock and not by who started the run: the GK35, GitHub's late backup or a person all get it right."""
    if SECO or paso not in RESERVA:
        return None
    dia, hora = RESERVA[paso]
    t = ahora or datetime.datetime.now(MADRID)
    return hora if t.weekday() == dia and f"{t.hour:02}:{t.minute:02}" < hora.zfill(5) else None


def semana(t):
    """The start of this train week: the latest Sunday 19:00 in Madrid not after t."""
    d = (t - datetime.timedelta(days=(t.weekday() + 1) % 7)).replace(hour=19, minute=0, second=0, microsecond=0)
    return d if d <= t else d - datetime.timedelta(days=7)


def plazo_parar(t):
    """From Sunday 19:00 to Monday 9:07 nothing goes out: it is the time the stop button has."""
    return (t.weekday() == 6 and t.hour >= 19) or (t.weekday() == 0 and (t.hour, t.minute) < (9, 7))


def ejecucion():
    s, r, i = (os.environ.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{s}/{r}/actions/runs/{i}" if i else ""


# ---------- states ----------
def orden(clave, nombre):
    return f"parar {'prueba' if SECO else 'tren'} {clave} {nombre}"


def parada(msgs, clave, nombre, hasta=None):
    """A stop order, only if pressed before `hasta` (Monday 9:07): a later press does nothing, so a store
    still waiting after 9:07 is not left behind the one that already went out."""
    return any((m.get("message") or "").strip() == orden(clave, nombre) and (hasta is None or m.get("time", 0) < hasta) for m in msgs)


def estado_play(msgs, paquete):
    """The newest "tren-play <package> <json>" the GK35 posted (navegador.mjs on Publishing overview)."""
    return del_tren(msgs, "play", paquete)


def play_url(est):
    return (est or {}).get("url") or "https://play.google.com/console"


def bloqueo_play(est, ahora):
    """(state, cause, what to do) for what only the user can clear in Play, from a fresh Play Console
    reading: a rejection, or changes nobody sent. Reviews need no reading: the commit says so."""
    if not est or ahora - datetime.datetime.fromisoformat(est["leido"].replace("Z", "+00:00")).timestamp() > FRESCO:
        return None
    return {"rechazada": ("usuario", "Google rechazo un cambio", f"mira el motivo en {play_url(est)}"),
            "pendiente": ("usuario", "Play tiene cambios sin enviar", f"envialos o descartalos en {play_url(est)}"),
            }.get(est.get("estado"))


def asc_url(app_id):
    return f"https://appstoreconnect.apple.com/apps/{app_id}/distribution"


def ios_versiones(bundle):
    a = appstore.app_of(bundle)["id"]
    d = appstore.api("GET", f"/v1/apps/{a}/appStoreVersions?filter[platform]=IOS&include=build&fields[builds]=version&limit=10")
    builds = {b["id"]: int(b["attributes"]["version"]) for b in d.get("included", []) if b["type"] == "builds"}
    return [(v["attributes"]["versionString"], v["attributes"].get("appVersionState") or v["attributes"]["appStoreState"],
             builds.get((v["relationships"]["build"].get("data") or {}).get("id"), 0)) for v in d["data"]]


def envios_ios(app_id):
    """States of the app's open App Review submissions: a version, a product page test, events."""
    return [s["attributes"]["state"] for s in appstore.get_all(
        f"/v1/reviewSubmissions?filter[app]={app_id}&filter[platform]=IOS"
        "&filter[state]=WAITING_FOR_REVIEW,IN_REVIEW,UNRESOLVED_ISSUES,CANCELING,COMPLETING&limit=50")]


def bloqueo_ios(vers, envios, enlace):
    """(state, cause, what to do) for what stops a new version in App Store Connect, or None. A rejection is
    the user's; a review, a release in progress or another submission (a product page test, an event) clears
    alone, and so does, usually, an approved version waiting for its release (FlowTime 2.4.0 has a watcher)."""
    for nombre, estado, _ in vers:
        if estado in RECHAZO_IOS:
            return "usuario", f"Apple rechazo la {nombre} ({estado})", f"contesta o corrige en {enlace}"
    if "UNRESOLVED_ISSUES" in envios:
        return "usuario", "Apple rechazo un envio (prueba de pagina, evento)", f"contesta en {enlace}"
    for nombre, estado, _ in vers:
        if estado == "PENDING_DEVELOPER_RELEASE":
            return "espera", f"la {nombre} esta aprobada y sin publicar", f"publicala en {enlace}"
        if estado not in LIBRE_IOS:
            return "espera", f"Apple revisa la {nombre}" if "REVIEW" in estado else f"la {nombre} esta en {estado}", NADA
    if ENVIO_ABIERTO & set(envios):
        return "espera", "Apple revisa otro envio (prueba de pagina, evento)", NADA
    return None


def a_la_par(p, i):
    """Both stores or neither (the user, 23-10-2026): a store that could go waits for the blocked one."""
    if bool(p) == bool(i):
        return p, i
    b = p or i
    pareja = ("pareja", f"espera {'a Play' if p else 'al iPhone'}: {b[1]}", NADA)
    return p or pareja, i or pareja


def error_play(m, url):
    if "CHANGES_ALREADY_IN_REVIEW" in m:
        return "espera", "Google revisa otros cambios", NADA
    if "changesNotSentForReview" in m:
        return "usuario", "Play tiene cambios sin enviar", f"envialos o descartalos en {url}"
    return "fallo", m[:200], CLAUDE


def error_ios(m):
    # Apple takes one submission at a time; STATE_ERROR is one it has not settled yet
    if "ENTITY_STATE_INVALID" in m or "STATE_ERROR" in m:
        return "espera", "Apple tiene otro envio abierto", NADA
    return "fallo", m[:200], CLAUDE


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


# ---------- Sunday's bundle ----------
def gh(ruta, crudo=False):
    """GitHub's API for this repository with the job's token (gh follows the artifact's redirect without it)."""
    r = subprocess.run(["gh", "api", f"repos/{env('GITHUB_REPOSITORY')}/{ruta}"], capture_output=True)
    if r.returncode:
        sys.exit(f"GitHub {ruta}: {r.stderr.decode(errors='replace').strip()[:300]}")
    return r.stdout if crudo else json.loads(r.stdout)


def aab_guardado(ref):
    """The id of the bundle Sunday built for that tag (artifact aab-<ref>, android-play-release.yml), or None."""
    return next((a["id"] for a in gh(f"actions/artifacts?name=aab-{ref}&per_page=20")["artifacts"] if not a["expired"]), None)


def subir_play(repo, paquete, art, carpeta, base, dry):
    """Sunday's bundle to production at 100 %, with its notes and the listing in the same edit (play.py);
    images only if they changed since `base`, the tag production has. The commit never cancels a review."""
    imagenes = not base or cambia(repo, base, "HEAD", *IMAGENES_PLAY)
    with tempfile.TemporaryDirectory() as d:
        zipfile.ZipFile(io.BytesIO(gh(f"actions/artifacts/{art}/zip", crudo=True))).extractall(d)
        play.aab(paquete, str(next(pathlib.Path(d).rglob("*.aab"))), "production", os.path.join(repo, carpeta), dry=dry,
                 ficha_de=repo, imagenes=imagenes)


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
    msgs, ahora, t = leer_topic(), time.time(), datetime.datetime.now(MADRID)
    # A new version only on Sunday from 19:00, once a week: a later run (the reserve, GitHub's late backup, the
    # GK35 rebuilding what Monday found missing) finishes that one and never makes a second
    hecha = int(git(repo, "log", "-1", "--format=%ct", ultima)) >= semana(t).timestamp()
    numera = SECO or (t.weekday() == 6 and t.hour >= 19 and not hecha)
    a = appstore.app_of(bundle)["id"]
    pistas, vers, envios = play.tracks(paquete), ios_versiones(bundle), envios_ios(a)
    prod_play, env_ios = produccion_play(pistas), enviada_ios(vers)
    base, hasta = (ultima, "HEAD") if numera else (
        git(repo, "describe", "--tags", "--abbrev=0", "--match", "v*", f"{ultima}^", check=False) or ultima, ultima)
    cambios = git(repo, "diff", "--name-only", base, hasta, "--", ".", *FUERA).split()
    # Sunday stops only for what the user has to clear: a review elsewhere is Monday's to wait for
    b_play = bloqueo_play(estado_play(msgs, paquete), ahora)
    b_ios = bloqueo_ios(vers, envios, asc_url(a))
    bloqueo = {k: list(b) for k, b in (("play", b_play), ("ios", b_ios)) if b and b[0] == "usuario"}
    bloqueado = bool(bloqueo) and not SECO
    nuevo = None
    if numera and (cambios or SECO) and not bloqueado:  # blocked: no number spent, next Sunday it is tried again
        usado = max([play.usados(paquete), ios_max_build(bundle)] + [int(c) for rs in pistas.values() for r in rs for c in r.get("versionCodes", [])])
        nuevo, nombre, code = subir_minor(texto, usado)
    else:
        nombre, code = version_en(repo, ultima)
    falta_play, falta_ios = prod_play < code, env_ios < code
    tf = {} if nuevo else ios_builds(bundle, nombre)
    ref = "tren-seco" if SECO else (f"v{nombre}" if nuevo else ultima)
    plan = {
        "app": app, "clave": clave, "version": nombre, "code": code, "nueva": bool(nuevo), "cambios": len(cambios),
        "seco": SECO, "bloqueo": bloqueo,
        "play": {"falta": falta_play, "subir": SECO or (falta_play and not bloqueado and (bool(nuevo) or not aab_guardado(ref)))},
        "ios": {"falta": falta_ios, "subir": SECO or (falta_ios and not bloqueado and code not in tf)},
    }
    if nuevo:
        (pathlib.Path(repo) / vf).write_text(nuevo, encoding="utf-8")
        quien = ["-c", "user.name=Tren de versiones", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com"]
        git(repo, *quien, "commit", "-qm", f"tren: {app} {nombre} ({code})", "--", vf)
        if SECO:
            git(repo, "push", "-qf", "origin", f"HEAD:refs/heads/{ref}")
        else:
            for intento in (1, 2):  # someone may have pushed to main since the checkout
                git(repo, "tag", "-f", ref)
                if not subprocess.run(["git", "-C", repo, "push", "-q", "--atomic", "origin", "HEAD:refs/heads/main",
                                       f"refs/tags/{ref}"]).returncode:
                    break
                if intento == 2:
                    sys.exit("no pude empujar el commit del tren a main")
                git(repo, *quien, "pull", "-q", "--rebase", "origin", "main")
    print(json.dumps(plan, ensure_ascii=False, indent=1))
    ya = None if SECO else del_tren(msgs, "plan", clave, nombre)
    # The button goes once per version, on Sunday; a block once per cause
    plan["anunciar"] = SECO or (t.weekday() == 6 and (falta_play or falta_ios) and not bloqueado and not (ya or {}).get("avisado"))
    plan["decir"] = bloqueado and (bool(cambios) or falta_play or falta_ios) and (ya or {}).get("bloqueo") != bloqueo
    plan["avisado"] = bool((ya or {}).get("avisado"))
    pathlib.Path(env("PLAN_FILE", os.devnull)).write_text(json.dumps(plan), encoding="utf-8")
    salida(ref=ref, play=plan["play"]["subir"], ios=plan["ios"]["subir"], **{"notas-play": notas_para(repo, prod_play, hasta)},
           aviso=plan["anunciar"] or plan["decir"])


def aviso():
    """Sunday's notice: the version and the stop button, or why there is none this week."""
    global SECO
    p = json.loads(pathlib.Path(env("PLAN_FILE")).read_text(encoding="utf-8"))
    SECO = p["seco"]  # the stop button of a dry notice says "parar prueba"
    if p["anunciar"]:
        donde = " y ".join(TIENDA[k] for k in ("play", "ios") if p[k]["falta"]) or "Play y iPhone"
        ntfy({"title": f"{'PRUEBA ' if SECO else ''}{p['app']} {p['version']} sale el lunes", "priority": 2, "click": ejecucion(),
              "message": f"{donde}, lunes 9:07.\nHaz: nada, o Parar esta semana.",
              "actions": [{"action": "http", "label": "Parar esta semana", "url": f"https://ntfy.sh/{env('NTFY_TOPIC_APROBAR')}",
                           "method": "POST", "body": orden(p["clave"], p["version"]), "clear": True}]})
    elif p["decir"]:
        _, causa, haz = next(iter(p["bloqueo"].values()))
        avisar(f"{p['app']}: sin version esta semana", causa, haz)
    if not SECO:
        publicar(f"tren-plan {p['clave']} " + json.dumps({
            "version": p["version"], "code": p["code"], "el": int(time.time()),
            "play": p["play"]["falta"] and not p["bloqueo"], "ios": p["ios"]["falta"] and not p["bloqueo"],
            "avisado": p["anunciar"] or p["avisado"], "bloqueo": p["bloqueo"], "dicho": {k: "usuario" for k in p["bloqueo"]}}, ensure_ascii=False))
    print("aviso:", "boton" if p["anunciar"] else "bloqueo" if p["decir"] else "nada")


def eco():
    msgs, clave = leer_topic(), env("CLAVE")
    vistas = {(m.get("message") or "").strip() for m in msgs}
    for o in sorted(o for o in vistas if re.fullmatch(rf"parar {'prueba' if SECO else 'tren'} {re.escape(clave)} [\d.]+", o)):
        publicar(o)
        print("eco:", o)
    plan = None if SECO else del_tren(msgs, "plan", clave)
    if plan:  # Monday reads it to know what Sunday already said (no second notice for the same thing)
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


def intentar(repo, paquete, bundle, nombre, code, tag, msgs, ahora):
    """{store: (state, cause, what to do)}. The iPhone is checked first, so that Play waits for it too; Play's
    commit (ERROR_IF_IN_REVIEW) has the last word on Google's reviews; App Review gets it once Play has it."""
    a = appstore.app_of(bundle)["id"]
    pistas, vers, envios = play.tracks(paquete), ios_versiones(bundle), envios_ios(a)
    prod, enviada, enlace, est = produccion_play(pistas), enviada_ios(vers), asc_url(a), estado_play(msgs, paquete)
    res = {k: ("ya", f"ya tiene la {code}", NADA) for k, tiene in (("play", prod >= code), ("ios", enviada >= code)) if tiene and not SECO}
    art = p = i = None
    if "play" not in res:
        art = aab_guardado("tren-seco" if SECO else tag)
        p = bloqueo_play(est, ahora) or (None if art else ("subir", f"falta el paquete de Play de la {nombre}", CLAUDE))
    if "ios" not in res:
        tf = ios_builds(bundle, nombre).get(code)
        i = bloqueo_ios(vers, envios, enlace) or {
            "VALID": None, "PROCESSING": ("espera", f"TestFlight procesa la build {code}", NADA),
            None: ("subir", f"falta la build {code} en TestFlight", CLAUDE)}.get(tf, ("usuario", f"TestFlight dio la build {code} por {tf}", CLAUDE))
    if SECO:  # nothing changes: Play validates the dry Sunday's bundle, App Store Connect is only described
        if art:
            subir_play(repo, paquete, art, NOTAS, etiqueta_de(repo, prod), True)
        de_verdad = lambda b: f"; de verdad: {b[0]}, {b[1]}" if b else ""
        return {"play": ("sale", ("validado el paquete del domingo en seco" if art else "sin paquete del domingo en seco") + de_verdad(p), NADA),
                "ios": ("sale", f"en seco: se enviaria la {nombre} con la build {code}" + de_verdad(i), NADA)}
    if not res:
        p, i = a_la_par(p, i)
    res.update({k: b for k, b in (("play", p), ("ios", i)) if b and k not in res})
    if "play" not in res:
        try:
            subir_play(repo, paquete, art, notas_para(repo, prod, tag), etiqueta_de(repo, prod), False)
            res["play"] = ("sale", "a produccion al 100 %", NADA)
        except SystemExit as e:
            res["play"] = error_play(str(e.code), play_url(est))
    if "ios" not in res:
        if res["play"][0] not in ("sale", "ya"):
            res["ios"] = ("pareja", f"espera a Play: {res['play'][1]}", NADA)
        else:
            try:
                publicar_ios(repo, bundle, nombre, code, notas_para(repo, enviada, tag), etiqueta_de(repo, enviada))
                res["ios"] = ("sale", "a revision de Apple", NADA)
            except SystemExit as e:
                res["ios"] = error_ios(str(e.code))
    return res


def seguir(res, prev, ahora):
    """The tren-lunes to post and the stores to tell now. Per store, since when it is in its state (desde)
    and what was told (dicho): each state is told once, after its UMBRAL."""
    prev = prev or {}
    desde, dicho, decir = dict(prev.get("desde") or {}), dict(prev.get("dicho") or {}), []
    for k, (e, *_) in res.items():
        if prev.get(k, e) != e:
            desde[k] = int(ahora)
            dicho.pop(k, None)
        desde.setdefault(k, int(ahora))
        if e in UMBRAL and dicho.get(k) != e and ahora - desde[k] >= UMBRAL[e]:
            decir.append(k)
            dicho[k] = e
    return {**{k: v[0] for k, v in res.items()}, "desde": desde, "dicho": dicho}, decir


def lunes(repo):
    app, clave, paquete, bundle = env("APP"), env("CLAVE"), env("PAQUETE"), env("BUNDLE")
    if not SECO and plazo_parar(datetime.datetime.now(MADRID)):
        return print("del domingo 19:00 al lunes 9:07 no sale nada: es el plazo del boton de parar")
    tag = ultima_etiqueta(repo)
    nombre, code = version_en(repo, tag)
    git(repo, "checkout", "-q", tag)  # listing, screenshots and notes as they were built, not today's main
    msgs, ahora = leer_topic(), time.time()
    prev = None if SECO else del_tren(msgs, "lunes", clave, nombre)
    plan = None if SECO else del_tren(msgs, "plan", clave, nombre)
    # Dry, the stop order to look for is the one of the dry Sunday notice: the version it would have made
    anunciada = subir_minor((pathlib.Path(repo) / env("VERSION_FILE")).read_text(encoding="utf-8"))[1] if SECO else nombre
    hasta = None if SECO else (semana(datetime.datetime.now(MADRID)) + datetime.timedelta(hours=14, minutes=7)).timestamp()
    if parada(msgs, clave, anunciada, hasta):
        if (prev or {}).get("play") != "parada":
            avisar(f"{app} {anunciada} parada esta semana", "pulsaste Parar", "nada; el domingo sale lo que haya en main", 2)
        if not SECO:
            publicar(f"tren-lunes {clave} " + json.dumps({"version": nombre, "code": code, "el": int(ahora), "play": "parada", "ios": "parada"}))
        return
    try:
        res = intentar(repo, paquete, bundle, nombre, code, tag, msgs, ahora)
    except SystemExit as e:  # a store's API failed before anything was decided
        res = {k: ("fallo", str(e.code)[:200], CLAUDE) for k in TIENDA}
    for k in TIENDA:
        print(f"{TIENDA[k]}: {res[k][0]}, {res[k][1]}")
    # What Sunday already told (a block) is not told again
    estado, decir = seguir(res, prev or {"dicho": (plan or {}).get("dicho") or {}}, ahora)
    if not SECO:
        publicar(f"tren-lunes {clave} " + json.dumps({"version": nombre, "code": code, "el": int(ahora), **estado}, ensure_ascii=False))
    for k in decir:
        avisar(f"{app} {nombre}: {TIENDA[k]} {TITULO[res[k][0]]}", res[k][1], res[k][2])
    salio = [TIENDA[k] for k in TIENDA if res[k][0] == "sale"]
    if SECO:
        ntfy({"title": f"PRUEBA {app} {nombre}: lunes en seco", "priority": 2, "click": ejecucion(),
              "message": "\n".join(f"{TIENDA[k]}: {res[k][1]}" for k in TIENDA)})
    elif salio and not decir:
        ntfy({"title": f"{app} {nombre} enviada a {' y '.join(salio)}", "priority": 2, "click": ejecucion(),
              "message": "Google y Apple la revisan; sale sola." if len(salio) == 2 else f"{salio[0]} la revisa; sale sola."})


def fallo(paso):
    """A step broke before its own notice. Silent while a later run retries it: Sunday's reserve, or on any
    other day Monday's state, which the GK35 keeps retrying and tells after 2 h. Else once in 12 h."""
    t = datetime.datetime.now(MADRID)
    if reserva(paso, t) or (paso == "domingo" and t.weekday() != 6):
        return print(f"el {paso} se corto: lo reintenta la siguiente ejecucion")
    marca = f"tren-fallo {env('CLAVE')} {paso}"
    try:
        if not SECO and any((m.get("message") or "").startswith(marca) for m in leer_topic()):
            return print(f"el {paso} se corto otra vez: ya avisado")
        if not SECO:
            publicar(marca)
    except Exception:  # without ntfy's topic it is told anyway
        pass
    avisar(f"Tren de {env('APP')}: se corto el {paso}", "un paso fallo (toca el aviso)", "pasale este aviso a Claude", 4 if paso == "domingo" else 3)


# ---------- self-check ----------
def prueba():
    t = 'android {\n    versionCode = 24\n    versionName = "1.2.4"\n}'
    assert version(t) == ("1.2.4", 24) and subir_minor(t)[1:] == ("1.3.0", 25)
    assert subir_minor('const val versionCode = 66\nconst val versionName = "2.5.0"')[1:] == ("2.6.0", 67)
    assert subir_minor('versionCode = 9\nversionName = "1.8"')[1:] == ("1.9.0", 10)
    assert subir_minor(t, 30)[2] == 31 and subir_minor(t, 3)[2] == 25  # the highest used anywhere + 1
    global SECO, ntfy, publicar
    msgs = [{"message": "parar tren flowtime 2.6.0"}, {"message": "aprobar 2026-10-26 x todo"}]
    assert parada(msgs, "flowtime", "2.6.0") and not parada(msgs, "flowtime", "2.7.0") and not parada(msgs, "chroma", "2.6.0")
    SECO = True
    assert not parada(msgs, "flowtime", "2.6.0") and orden("chroma", "1.3.0") == "parar prueba chroma 1.3.0"
    m = lambda d, h, mi: datetime.datetime(2026, 10, d, h, mi, tzinfo=MADRID)  # Sunday 25-10, Monday 26-10
    assert reserva("domingo", m(25, 19, 38)) is None  # dry: no reserve run comes
    SECO = False
    assert reserva("domingo", m(25, 19, 38)) == "21:37" and reserva("domingo", m(25, 21, 37)) is None  # the reserve itself
    assert reserva("lunes", m(26, 9, 7)) == "11:07" and reserva("lunes", m(26, 16, 7)) is None and reserva("eco", m(26, 1, 17)) is None
    assert semana(m(25, 19, 38)) == m(25, 19, 0) and semana(m(26, 9, 7)) == m(25, 19, 0) and semana(m(25, 18, 0)) == m(18, 19, 0)
    assert plazo_parar(m(25, 19, 0)) and plazo_parar(m(26, 9, 6)) and not plazo_parar(m(26, 9, 7)) and not plazo_parar(m(25, 18, 59))
    ev = [{"event": "message", "id": "a", "message": "parar tren chroma 1.3.0"}, {"event": "message_delete", "id": "b", "sequence_id": "a"},
          {"event": "message", "id": "c", "message": "parar tren chroma 1.3.0"}]
    assert parada(vivos(ev), "chroma", "1.3.0") and not parada(vivos(ev[:2]), "chroma", "1.3.0")  # a deleted stop no longer stops
    corte = (semana(m(26, 10, 0)) + datetime.timedelta(hours=14, minutes=7)).timestamp()
    assert corte == m(26, 9, 7).timestamp()
    tarde = [{"message": "parar tren chroma 1.3.0", "time": m(26, 9, 30).timestamp()}]
    assert parada(tarde, "chroma", "1.3.0") and not parada(tarde, "chroma", "1.3.0", corte)  # pressed after 9:07: nothing
    assert parada([{**tarde[0], "time": m(25, 20, 0).timestamp()}], "chroma", "1.3.0", corte)
    # Play: only a fresh reading says anything, and only what the user has to clear
    ahora = m(26, 9, 7).timestamp()
    est = lambda e, h=0.3: {"leido": datetime.datetime.fromtimestamp(ahora - h * 3600, datetime.timezone.utc).isoformat(), "estado": e, "url": "U"}
    assert bloqueo_play(None, ahora) is None and bloqueo_play(est("revision"), ahora) is None and bloqueo_play(est("desconocido"), ahora) is None
    assert bloqueo_play(est("rechazada"), ahora)[0] == "usuario" and "U" in bloqueo_play(est("pendiente"), ahora)[2]
    assert bloqueo_play(est("rechazada", h=4), ahora) is None  # stale: the API decides
    assert error_play("POST x:commit -> 400: CHANGES_ALREADY_IN_REVIEW", "U")[0] == "espera"
    assert error_play("x changesNotSentForReview x", "U")[0] == "usuario" and error_play("-> 500: boom", "U")[0] == "fallo"
    assert error_ios("POST /v1/reviewSubmissions -> 409: ENTITY_STATE_INVALID")[0] == "espera" and error_ios("-> 409: ENTITY_ERROR")[0] == "fallo"
    # iPhone: another submission in review (a product page test, 27-10) waits; a rejection is the user's
    libre = [("1.2.4", "READY_FOR_SALE", 25)]
    assert bloqueo_ios(libre, [], "E") is None
    assert bloqueo_ios(libre, ["WAITING_FOR_REVIEW"], "E")[0] == "espera" and bloqueo_ios(libre, ["IN_REVIEW"], "E") is None
    assert bloqueo_ios(libre, ["UNRESOLVED_ISSUES", "WAITING_FOR_REVIEW"], "E")[0] == "usuario"
    assert bloqueo_ios([("1.3.0", "WAITING_FOR_REVIEW", 26)] + libre, [], "E")[1] == "Apple revisa la 1.3.0"
    assert bloqueo_ios([("1.3.0", "REJECTED", 26)] + libre, ["WAITING_FOR_REVIEW"], "E")[0] == "usuario"
    assert bloqueo_ios([("1.3.0", "PREPARE_FOR_SUBMISSION", 26)] + libre, [], "E") is None
    assert bloqueo_ios([("2.4.0", "PENDING_DEVELOPER_RELEASE", 63)], [], "E") == ("espera", "la 2.4.0 esta aprobada y sin publicar", "publicala en E")
    assert enviada_ios([("1.3.0", "PREPARE_FOR_SUBMISSION", 26)] + libre) == 25
    espera = ("espera", "Apple revisa otro envio", NADA)
    assert a_la_par(None, None) == (None, None) and a_la_par(espera, espera) == (espera, espera)
    assert a_la_par(None, espera) == (("pareja", "espera al iPhone: Apple revisa otro envio", NADA), espera)
    # Monday in order: iPhone first, Play's commit, App Review only with Play in
    hecho, stubs = [], {}

    def con(**kv):
        g = globals()
        base = {"ios_versiones": lambda b: libre, "envios_ios": lambda a: [], "ios_builds": lambda b, n: {26: "VALID"},
                "aab_guardado": lambda r: 7, "notas_para": lambda *a: NOTAS, "etiqueta_de": lambda *a: "v1.2.4",
                "estado_play": lambda m, p: None, "subir_play": lambda *a: hecho.append("play"),
                "publicar_ios": lambda *a: hecho.append("ios"), "produccion_play": lambda p: 24}
        for k, v in {**base, **kv}.items():
            stubs.setdefault(k, g[k]); g[k] = v
        hecho.clear()
        return intentar(".", "com.x", "com.x", "1.3.0", 26, "v1.3.0", [], ahora)
    guardados = (appstore.app_of, play.tracks)
    appstore.app_of, play.tracks = (lambda b: {"id": "1"}), (lambda p: {})
    r = con()
    assert hecho == ["play", "ios"] and r["play"][0] == r["ios"][0] == "sale"
    r = con(envios_ios=lambda a: ["WAITING_FOR_REVIEW"])  # the 27-10 case: Play waits too, nothing is touched
    assert hecho == [] and r["ios"][0] == "espera" and r["play"][0] == "pareja"
    def en_revision(*a):
        raise SystemExit("POST com.x/edits/1:commit -> 403: CHANGES_ALREADY_IN_REVIEW")
    r = con(subir_play=en_revision)
    assert hecho == [] and r["play"][0] == "espera" and r["ios"][0] == "pareja"
    def conflicto(*a):
        raise SystemExit("POST /v1/reviewSubmissions -> 409: ENTITY_STATE_INVALID")
    r = con(publicar_ios=conflicto)  # the 409 arrives anyway: Play went, the iPhone retries alone
    assert hecho == ["play"] and r["play"][0] == "sale" and r["ios"][0] == "espera"
    r = con(produccion_play=lambda p: 26, envios_ios=lambda a: ["WAITING_FOR_REVIEW"])  # Play has it: the iPhone catches up
    assert hecho == [] and r["play"][0] == "ya" and r["ios"][0] == "espera"
    r = con(aab_guardado=lambda r: None)
    assert hecho == [] and r["play"][0] == "subir" and r["ios"][0] == "pareja"
    for k, v in stubs.items():
        globals()[k] = v
    appstore.app_of, play.tracks = guardados
    # What is told, and when: once per state, after its threshold
    h = 3600
    st, d = seguir({"play": ("pareja", "x", NADA), "ios": espera}, None, 0)
    assert d == [] and st["desde"] == {"play": 0, "ios": 0}
    st, d = seguir({"play": ("pareja", "x", NADA), "ios": espera}, st, 71 * h)
    assert d == []
    st, d = seguir({"play": ("pareja", "x", NADA), "ios": espera}, st, 72 * h)
    assert d == ["ios"] and st["dicho"] == {"ios": "espera"}
    assert seguir({"play": ("pareja", "x", NADA), "ios": espera}, st, 80 * h)[1] == []  # told once
    st, d = seguir({"play": ("ya", "x", NADA), "ios": ("fallo", "x", CLAUDE)}, st, 81 * h)
    assert d == [] and st["desde"]["ios"] == 81 * h
    assert seguir({"play": ("ya", "x", NADA), "ios": ("fallo", "x", CLAUDE)}, st, 83 * h)[1] == ["ios"]
    assert seguir({"play": ("pareja", "x", NADA), "ios": ("usuario", "x", "y")}, None, 0)[1] == ["ios"]  # at once
    assert seguir({"play": ("pareja", "x", NADA), "ios": ("usuario", "x", "y")}, {"dicho": {"ios": "usuario"}}, 0)[1] == []  # Sunday told it
    # Sunday's notice: two lines and the button; a dry button never posts a real stop order
    import tempfile as tf
    enviado, guardados = [], (ntfy, publicar)
    ntfy, publicar = enviado.append, enviado.append
    t = {"falta": True, "subir": True}
    for seco, cuerpo, n in ((True, "parar prueba chroma 1.4.0", 1), (False, "parar tren chroma 1.4.0", 2)):
        with tf.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"app": "Chroma", "clave": "chroma", "version": "1.4.0", "code": 27, "seco": seco, "play": t, "ios": t,
                       "bloqueo": {}, "anunciar": True, "decir": False, "avisado": False}, f)
        os.environ.update(PLAN_FILE=f.name, NTFY_TOPIC_APROBAR="T")
        enviado.clear(); aviso()
        assert enviado[0]["actions"][0]["body"] == cuerpo and len(enviado) == n and enviado[0]["priority"] == 2
        assert enviado[0]["message"] == "Play y iPhone, lunes 9:07.\nHaz: nada, o Parar esta semana."
    assert '"avisado": true' in enviado[1]
    with open(f.name, "w") as g:
        json.dump({"app": "Chroma", "clave": "chroma", "version": "1.3.0", "code": 26, "seco": False, "play": t, "ios": t, "avisado": False,
                   "bloqueo": {"ios": ["usuario", "Apple rechazo la 1.3.0 (REJECTED)", "contesta o corrige en E"]}, "anunciar": False, "decir": True}, g)
    enviado.clear(); aviso()
    assert enviado[0]["message"] == "Causa: Apple rechazo la 1.3.0 (REJECTED).\nHaz: contesta o corrige en E." and enviado[0]["priority"] == 3
    assert '"dicho": {"ios": "usuario"}' in enviado[1]
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
