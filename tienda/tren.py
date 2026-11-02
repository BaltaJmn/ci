#!/usr/bin/env python3
"""The weekly release train, on the Mac (PUBLICAR.md, section 7): the only machine with Xcode, the Keychain and the
upload keys. launchd (com.baltajmn.tren.plist) runs tren-mac.sh every 30 min and on Monday at 7:30, which calls:

    tren.py tick           every app of APPS in order: does what is missing and leaves at once when there is nothing
    tren.py seco <clave>   one app's whole Monday in one go, dry
    tren.py --prueba       checks without network

Monday, per app, when main changed since its last tag (not counting FUERA):
  a) escaparate (escaparate.md, claude -p with sonnet): what's new, listing and screenshots up to date, a separate
     reviewer (9 or more; the Pro screenshot matches the paywall and the prices), comprobar.py, commit and push
  b) ntfy "<App> X.Y sale hoy" with the stop button, which has MARGEN
  c) numbers it (minor + 1), commit and tag; the Play bundle, which Play validates, kept as
     ~/.tren/<clave>/aab-<tag>.aab; testflight.sh; App Review with releaseType MANUAL once TestFlight has the build
  d) Apple approved it (PENDING_DEVELOPER_RELEASE): that bundle to Play production with its listing
  e) Play published it: the iPhone goes on sale (appStoreVersionReleaseRequests)
Apple sets the pace and Android never goes ahead. A version still moving on Monday goes on and no other starts; one
stuck before Play (Apple rejected it, or it never reached App Review) is replaced by Monday's next: its submission is
cancelled and the version renamed, with a new build.

Per store, the states of the old tren-lunes: ya, sale, espera (clears alone), pareja (waits for the other store),
usuario (only the user can: a rejection), fallo (an error), subir (a build is missing) and parada. They live in
~/.tren/<clave>/estado.json and go to the secret topic as "tren-lunes <clave> <json>" when they change (semana and
versiones.mjs read it). Notices are two lines, "Causa:" and "Haz:", once per state (seguir): usuario at once, fallo
and subir after 2 h, espera after 72 h. What goes out arrives silent (priority 2).

play.py and appstore.py read ~/keys and the Keychain; tren-mac.sh puts NTFY_TOPIC (notices) and NTFY_TOPIC_APROBAR
(stop orders and the train's messages) in the environment. Every app works in its own clean clone in ~/.tren, never
in the user's folders. SECO=true: nothing is pushed, the tag is the local tren-seco, Play only validates, the iPhone
archive is exported without uploading, App Store Connect is only read, notices say PRUEBA and the button posts
"parar prueba ...", which the real train never reads.
"""
import datetime, json, os, pathlib, re, shutil, subprocess, sys, time, urllib.error, urllib.request, zoneinfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import appstore, play  # noqa: E402

AQUI = pathlib.Path(__file__).resolve().parent
TREN = pathlib.Path.home() / ".tren"
KTS, AAB = "androidApp/build.gradle.kts", "androidApp/build/outputs/bundle/release/androidApp-release.aab"
CONSOLA = "https://play.google.com/console/u/0/developers/8878547411685670253/app"
# The apps on the train, in the order a tick takes them: GitHub repo, the folder of its clone (the same name as in
# ~/AndroidStudioProjects and in apps.js), its key in gk/contenido/plantilla/tienda/apps.js, its Play Console id, the
# version file and the Play bundle's Gradle tasks and output. Package name and bundle id are the same in each.
APPS = {
    "flowtime": dict(app="FlowTime", github="BaltaJmn/FlowTime-Pomodoro", repo="FlowTime-Pomodoro", plantilla="flowtime",
                     paquete="com.baltajmn.flowtime", consola="4974171869880370951",
                     version="build-logic/plugins/src/main/java/Config.kt", gradle=":app:bundleRelease",
                     aab="app/build/outputs/bundle/release/app-release.aab"),
    "chroma": dict(app="Chroma", github="BaltaJmn/color", repo="color", plantilla="chroma", paquete="com.baltajmn.color",
                   consola="4974549496268375065", version=KTS, gradle=":shared:testAndroidHostTest :androidApp:bundleRelease", aab=AAB),
    "quilt": dict(app="Quilt", github="BaltaJmn/quilt", repo="HabitTracker", plantilla="quilt", paquete="com.baltajmn.habit",
                  consola="4972598744852104658", version=KTS, gradle=":shared:testAndroidHostTest :androidApp:bundleRelease", aab=AAB),
    "mood": dict(app="MoodTraker", github="BaltaJmn/MoodTraker", repo="MoodTraker", plantilla="moodtraker", paquete="com.baltajmn.mood",
                 consola="4976467445397187183", version=KTS, gradle=":shared:testAndroidHostTest :androidApp:bundleRelease", aab=AAB),
}
NOTAS, GENERICAS = "store/whatsnew", "store/whatsnew-generic"
IMAGENES_PLAY = ("store/play", "store/screenshots/play", "store/screenshots/tablet")
# A week with only these changes makes no version: documentation, workflows and IDE files.
FUERA = [":(exclude)*.md", ":(exclude).github", ":(exclude).idea"]
MADRID = zoneinfo.ZoneInfo("Europe/Madrid")
LUNES = (7, 30)  # from this Monday time a new version may start (launchd's fixed start)
MARGEN = 3600  # from the "sale hoy" notice to the numbering: the time the stop button has
# How long a store is in a state before it is told (once). States not here are never told.
UMBRAL = {"usuario": 0, "fallo": 2 * 3600, "subir": 2 * 3600, "espera": 72 * 3600}
TIENDA = {"play": "Play", "ios": "iPhone"}
TITULO = {"usuario": "necesita tu mano", "fallo": "falla", "subir": "sin build", "espera": "lleva 3 dias esperando"}
NADA, CLAUDE = "nada, reintenta solo", "si sigue, pasale este aviso a Claude"
QUIEN = ["-c", "user.name=Tren de versiones"]
# App Store states that leave the way free; the train never piles a version on top of another.
LIBRE_IOS = {"READY_FOR_SALE", "READY_FOR_DISTRIBUTION", "REPLACED_WITH_NEW_VERSION", "REMOVED_FROM_SALE",
             "DEVELOPER_REMOVED_FROM_SALE", "PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED"}
SIN_ENVIAR = {"PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED"}  # prepared but never sent: reused
POR_ENVIAR = SIN_ENVIAR | {"READY_FOR_REVIEW"}  # the train's own version not in review yet (READY_FOR_REVIEW: in a draft submission)
RECHAZO_IOS = {"REJECTED", "METADATA_REJECTED", "INVALID_BINARY"}
APROBADA_IOS = {"PENDING_DEVELOPER_RELEASE", "ACCEPTED"}  # approved, waiting for liberar_ios
A_LA_VENTA_IOS = {"READY_FOR_SALE", "READY_FOR_DISTRIBUTION", "PENDING_APPLE_RELEASE", "PROCESSING_FOR_DISTRIBUTION",
                  "REPLACED_WITH_NEW_VERSION"}
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


def usar(clave):
    """The app's settings into the environment, where the helpers below read them."""
    a = APPS[clave]
    os.environ.update(APP=a["app"], CLAVE=clave, PAQUETE=a["paquete"], BUNDLE=a["paquete"], VERSION_FILE=a["version"])
    return a


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
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
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


def clon(carpeta, github, *sparse):
    """A clean clone of origin/main in ~/.tren, never the user's folders. keystore.properties and local.properties
    are links to the user's (ignored, so reset and clean leave them). `sparse`: only those folders (gk is 420 MB)."""
    d = TREN / carpeta
    if not d.exists():
        TREN.mkdir(exist_ok=True)
        subprocess.run(["git", "clone", "-q", *(["--filter=blob:none", "--sparse"] if sparse else []),
                        f"https://github.com/{github}.git", str(d)], check=True)
        if sparse:
            git(d, "sparse-checkout", "set", *sparse)
    git(d, "fetch", "-q", "--tags", "origin")
    git(d, "checkout", "-qf", "-B", "main", "origin/main")
    git(d, "clean", "-fdq")
    for f in ("keystore.properties", "local.properties"):
        suyo = pathlib.Path.home() / "AndroidStudioProjects" / carpeta / f
        if suyo.exists() and not (d / f).exists():
            (d / f).symlink_to(suyo)
    return str(d)


def empujar(repo, *refs):
    """HEAD to main (and its tags, atomically), once more on top of what someone pushed meanwhile."""
    for intento in (1, 2):
        if not subprocess.run(["git", "-C", str(repo), "push", "-q", "--atomic", "origin", "HEAD:refs/heads/main",
                               *[f"refs/tags/{r}" for r in refs]]).returncode:
            return
        if intento == 2:
            sys.exit(f"no pude empujar {repo} a main")
        git(repo, *QUIEN, "pull", "-q", "--rebase", "origin", "main")
        for r in refs:
            git(repo, "tag", "-f", r)


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
    ntfy({"title": ("PRUEBA " if SECO else "") + titulo, "priority": prioridad,
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


def orden(clave, nombre):
    return f"parar {'prueba' if SECO else 'tren'} {clave} {nombre}"


def parada(msgs, clave, nombre, hasta=None):
    """A stop order, only if pressed before `hasta` (the end of MARGEN): a later press does nothing, so a version
    already numbered and on its way is never left half done."""
    return any((m.get("message") or "").strip() == orden(clave, nombre) and (hasta is None or m.get("time", 0) < hasta) for m in msgs)


# ---------- the stores ----------
def asc_url(app_id):
    return f"https://appstoreconnect.apple.com/apps/{app_id}/distribution"


def play_url(a):
    return f"{CONSOLA}/{a['consola']}/publishing"


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
    alone, and so does an approved version waiting for its release."""
    for nombre, estado, _ in vers:
        if estado in RECHAZO_IOS:
            return "usuario", f"Apple rechazo la {nombre} ({estado})", f"contesta o corrige en {enlace}"
    if "UNRESOLVED_ISSUES" in envios:
        return "usuario", "Apple rechazo un envio (prueba de pagina, evento)", f"contesta en {enlace}"
    for nombre, estado, _ in vers:
        if estado in APROBADA_IOS:
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


def vendida_ios(vers):
    """The newest build on sale in the App Store: what its users have read and seen."""
    return max([b for _, e, b in vers if e in LIBRE_IOS - SIN_ENVIAR] or [0])


def ios_max_build(bundle):
    """The highest build number App Store Connect has, TestFlight test builds included."""
    a = appstore.app_of(bundle)["id"]
    return max([int(v) for b in appstore.get_all(f"/v1/builds?filter[app]={a}&fields[builds]=version&limit=200")
                if (v := b["attributes"]["version"]).isdigit()] or [0])


def ios_builds(bundle, nombre):
    a = appstore.app_of(bundle)["id"]
    return {int(b["attributes"]["version"]): b["attributes"]["processingState"] for b in appstore.get_all(
        f"/v1/builds?filter[app]={a}&filter[preReleaseVersion.version]={nombre}&fields[builds]=version,processingState&limit=50")}


def veredicto_play(paquete, code):
    """Google's word on the production release with that versionCode (releases.list: IN_REVIEW, NOT_APPROVED,
    PUBLISHED...), or None if production does not have it; and the highest code in production."""
    rs = play.call("GET", f"{play.API}{paquete}/tracks/production/releases").get("releases", [])
    codigo = lambda r: [int(x["versionCode"]) for x in r.get("activeArtifacts", [])]
    suyo = next((r["releaseLifecycleState"].removeprefix("RELEASE_LIFECYCLE_STATE_") for r in rs if code in codigo(r)), None)
    return suyo, max([c for r in rs for c in codigo(r)] or [0])


def produccion_play(pistas):
    return max([int(c) for r in pistas.get("production", []) for c in r.get("versionCodes", [])] or [0])


def precios(bundle):
    """What each purchase of the app costs in Spain in App Store Connect, the prices compra.py set, scheduled ones
    included: the escaparate's reviewer checks the Pro screenshot against them."""
    a, out = appstore.app_of(bundle)["id"], []
    for p in appstore.get_all(f"/v1/apps/{a}/inAppPurchasesV2"):
        d = appstore.api("GET", f"/v1/inAppPurchasePriceSchedules/{p['id']}/manualPrices?filter[territory]=ESP&include=inAppPurchasePricePoint")
        out.append(f"{p['attributes']['productId']} {', '.join(i['attributes']['customerPrice'] for i in d.get('included', []))} EUR")
    for g in appstore.get_all(f"/v1/apps/{a}/subscriptionGroups"):
        for s in appstore.get_all(f"/v1/subscriptionGroups/{g['id']}/subscriptions"):
            d = appstore.api("GET", f"/v1/subscriptions/{s['id']}/prices?filter[territory]=ESP&include=subscriptionPricePoint")
            pp = {i["id"]: i["attributes"]["customerPrice"] for i in d.get("included", [])}
            out.append(f"{s['attributes']['productId']} " + ", ".join(
                pp[x["relationships"]["subscriptionPricePoint"]["data"]["id"]] + " EUR"
                + (f" desde el {x['attributes']['startDate']}" if x["attributes"].get("startDate") else "") for x in d["data"]))
    return "; ".join(out)


# ---------- builds ----------
def ruta(nombre):
    """~/.tren/<clave>/<nombre>: the app's state, bundles and logs."""
    d = TREN / env("CLAVE")
    d.mkdir(parents=True, exist_ok=True)
    return d / nombre


def resumen_log(log):
    """The lines that say why a build broke, short enough for a notice."""
    l = [x.strip() for x in pathlib.Path(log).read_text(errors="replace").splitlines() if x.strip()]
    malas = [x for x in l if re.search(r"^e: |error:|What went wrong|FAILED|Exception", x)]
    return " | ".join((malas or l)[-3:])[-300:]


def subir_play(repo, paquete, aab, carpeta, base, dry):
    """The bundle to production at 100 %, with its notes and the listing in the same edit (play.py); images only
    if they changed since `base`, the tag production has. The commit never cancels a review. Dry, Play validates."""
    imagenes = not base or cambia(repo, base, "HEAD", *IMAGENES_PLAY)
    play.aab(paquete, str(aab), "production", os.path.join(repo, carpeta), dry=dry, ficha_de=repo, imagenes=imagenes)


def construir_aab(repo, a, destino):
    """The Play bundle of the checked-out tag (gradle, signed with the user's keystore.properties). Play validates it now
    as production with its listing, so a broken bundle or listing shows on Monday and not the day Apple approves; only
    then is it kept as `destino`."""
    log = ruta("gradle.log")
    with open(log, "w") as f:
        r = subprocess.run(["./gradlew", "--console=plain", *a["gradle"].split()], cwd=repo, stdout=f, stderr=subprocess.STDOUT)
    if r.returncode:
        sys.exit(f"gradle: {resumen_log(log)}")
    prod = produccion_play(play.tracks(a["paquete"]))
    hecho = pathlib.Path(repo) / a["aab"]
    subir_play(repo, a["paquete"], hecho, notas_para(repo, prod, "HEAD"), etiqueta_de(repo, prod), True)
    shutil.copy(hecho, destino)


def testflight(repo, a):
    """The iPhone build of the checked-out tag to App Store Connect (testflight.sh, next to this file); dry, the
    archive is exported and not uploaded."""
    log = ruta("testflight.log")
    with open(log, "w") as f:
        r = subprocess.run(["/bin/zsh", str(AQUI / "testflight.sh"), repo, a["version"]], stdout=f, stderr=subprocess.STDOUT,
                           env={**os.environ, "SECO": "true" if SECO else "false"})
    if r.returncode:
        sys.exit(f"testflight.sh: {resumen_log(log)}")


# ---------- a) escaparate ----------
def claude(prompt, repo, gk, tope, horas):
    """claude -p with sonnet, no window and no permission prompts, in the app's clean clone (gk added)."""
    r = subprocess.run(["claude", "-p", "--model", "sonnet", "--permission-mode", "bypassPermissions", "--add-dir", gk,
                        "--max-budget-usd", str(tope), "--output-format", "json"],
                       input=prompt, cwd=repo, capture_output=True, text=True, timeout=horas * 3600)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        d = {}
    texto = d.get("result") or ""
    with open(ruta("escaparate.log"), "a") as f:
        f.write(f"\n===== {datetime.datetime.now(MADRID):%Y-%m-%d %H:%M}, salida {r.returncode}\n{texto or r.stderr[-2000:]}\n")
    if r.returncode or d.get("is_error"):
        if re.search(r"authenticat|OAuth|/login", texto + r.stderr):
            sys.exit("Claude sin sesion en el Mac: abre claude y /login")
        sys.exit(f"claude: {(texto or r.stderr).strip()[-200:]}")
    return texto


def veredicto(texto):
    """The reviewer's last word: (mark, whether the Pro check passed)."""
    n = re.findall(r"NOTA:\s*(\d+(?:[.,]\d+)?)", texto)
    return float(n[-1].replace(",", ".")) if n else 0.0, bool(re.search(r"PRO:\s*bien", texto))


def escaparate(repo, a, ultima):
    """a) The listing up to date before the version: escaparate.md with sonnet, then its reviewer apart (9 or more and
    the Pro check), comprobar.py and the commit (store/ in the app, apps.js and textos.js in gk). If it does not pass,
    nothing of it is kept and the version goes with the listing it had. A few words for the day's notice."""
    gk = clon("gk", "BaltaJmn/gk", "contenido/plantilla/tienda", "contenido/fonts")
    hacer, revisar = (AQUI / "escaparate.md").read_text(encoding="utf-8").split("\n# Revisor\n")
    try:
        pro = precios(a["paquete"])
    except (Exception, SystemExit) as e:
        pro = f"no se pudieron leer ({str(getattr(e, 'code', e))[:80]})"
    datos = (f"\n\n## Datos de hoy\n\n- App: {a['app']} (clave `{a['plantilla']}` en apps.js)\n- Repo, clon limpio en main: {repo}\n"
             f"- gk, clon limpio: {gk}\n- ci: {AQUI.parent}\n- Ultima version publicada: {ultima}\n"
             f"- Precios de Pro en Espana (App Store Connect): {pro}\n")

    def deshacer(por):
        for d in (repo, gk):
            git(d, "reset", "-q", "--hard")
            git(d, "clean", "-fdq")
        return f"ficha sin tocar ({por})"

    try:
        if claude(hacer + datos, repo, gk, 8, 2).strip().splitlines()[-1:] == ["ESCAPARATE: nada"]:
            deshacer("nada visible")
            return "ficha sin cambios"
        for vuelta in (1, 2):
            dicho = claude(revisar + datos, repo, gk, 2, 1)
            nota, pro = veredicto(dicho)
            if nota >= 9 and pro:
                break
            if vuelta == 2:
                return deshacer(f"revisor {nota:g}{'' if pro else ', Pro no cuadra'}")
            claude(hacer + datos + f"\n\n## Lo que dijo el revisor\n\n{dicho}\n\nArreglalo.\n", repo, gk, 5, 1)
    except (Exception, SystemExit) as e:
        return deshacer(f"el escaparate fallo: {str(getattr(e, 'code', e))[:80]}")
    if subprocess.run([sys.executable, str(AQUI / "comprobar.py"), repo]).returncode:
        return deshacer("comprobar.py no pasa")
    if not git(repo, "status", "--porcelain", "--", "store"):
        return "ficha sin cambios"
    git(repo, "add", "--", "store")
    git(repo, *QUIEN, "commit", "-qm", f"escaparate: {a['app']} al dia antes de la version", "--", "store")
    tienda = ["contenido/plantilla/tienda/apps.js", "contenido/plantilla/tienda/textos.js"]
    if git(gk, "status", "--porcelain", "--", *tienda):  # never git add -A in gk
        git(gk, *QUIEN, "commit", "-qm", f"tienda: capturas de {a['app']} del escaparate del lunes", "--", *tienda)
    hecho = f"ficha al dia (revisor {nota:g})"
    if not SECO:
        empujar(repo)
        try:
            if git(gk, "rev-list", "origin/main..HEAD"):
                empujar(gk)
        except SystemExit:  # the app's listing is out already; the screenshots' source is not, and says so
            hecho += ", sin subir apps.js a gk"
    return hecho


# ---------- b) and c) ----------
def anunciar(a, S):
    """b) The day's notice: the version, when it goes to Apple, and the stop button (MARGEN to press it)."""
    hora = datetime.datetime.fromtimestamp(time.time() + MARGEN, MADRID).strftime("%H:%M")
    ntfy({"title": f"{'PRUEBA ' if SECO else ''}{a['app']} {S['version']} sale hoy", "priority": 2,
          "message": f"A Apple a las {hora}, a Play cuando Apple la apruebe; {S['escaparate']}.\nHaz: nada, o Parar esta semana.",
          "actions": [{"action": "http", "label": "Parar esta semana", "url": f"https://ntfy.sh/{env('NTFY_TOPIC_APROBAR')}",
                       "method": "POST", "body": orden(env("CLAVE"), S["version"]), "clear": True}]})


def numerar(repo, a, S):
    """c) minor + 1 and the highest versionCode used anywhere + 1, commit and tag (dry: the local tag tren-seco,
    nothing pushed). A version this run already numbered and pushed (the state was not saved) is taken as it is."""
    hecho = git(repo, "tag", "--points-at", "HEAD", "--list", "v*")
    if hecho and not SECO and int(git(repo, "log", "-1", "--format=%ct")) >= S["aviso"]:
        tag = hecho.split()[0]
        S.update(tag=tag, **dict(zip(("version", "code"), version_en(repo, tag))))
        return
    paquete, bundle, vf = a["paquete"], a["paquete"], pathlib.Path(repo) / a["version"]
    pistas = play.tracks(paquete)
    usado = max([play.usados(paquete), ios_max_build(bundle)] + [int(c) for rs in pistas.values() for r in rs for c in r.get("versionCodes", [])])
    nuevo, nombre, code = subir_minor(vf.read_text(encoding="utf-8"), usado)
    tag = "tren-seco" if SECO else f"v{nombre}"
    vf.write_text(nuevo, encoding="utf-8")
    git(repo, *QUIEN, "commit", "-qm", f"tren: {a['app']} {nombre} ({code})", "--", a["version"])
    git(repo, "tag", "-f", tag)
    if not SECO:
        empujar(repo, tag)
    S.update(version=nombre, code=code, tag=tag)


def preparar(repo, a, S):
    """a) to c), each once: escaparate, the notice, MARGEN for the stop button and the numbering. None while waiting
    or with nothing to ship, else the stores' states."""
    clave = env("CLAVE")
    if "escaparate" not in S:
        ultima = ultima_etiqueta(repo)
        if not git(repo, "diff", "--name-only", ultima, "HEAD", "--", ".", *FUERA):
            S["nada"] = True
            volver(S)
            return print(f"{a['app']}: nada nuevo desde la {ultima}, sin version esta semana")
        S["escaparate"] = escaparate(repo, a, ultima)
        clon(a["repo"], a["github"])  # the escaparate's commit is main now
        guardar(S)
    if time.time() > S.get("aviso", time.time()) + 11 * 3600:  # the Mac slept: ntfy keeps 12 h, a stop pressed then is gone
        del S["aviso"]
    if "aviso" not in S:
        S["version"] = subir_minor((pathlib.Path(repo) / a["version"]).read_text(encoding="utf-8"))[1]
        anunciar(a, S)
        S["aviso"] = int(time.time())
        guardar(S)
    if time.time() < S["aviso"] + MARGEN:
        return print(f"{a['app']} {S['version']}: el boton de parar tiene hasta las "
                     f"{datetime.datetime.fromtimestamp(S['aviso'] + MARGEN, MADRID):%H:%M}")
    if parada(leer_topic(), clave, S["version"], S["aviso"] + MARGEN):
        avisar(f"{a['app']} {S['version']} parada esta semana", "pulsaste Parar", "nada; el lunes que viene sale lo que haya en main", 2)
        if S.get("antes"):
            return volver(S)
        return {k: ("parada", "pulsaste Parar", NADA) for k in TIENDA}
    numerar(repo, a, S)
    S.pop("antes", None)  # the stuck version is replaced by this one now
    guardar(S)
    return avanzar(repo, a, S)


def volver(S):
    """No version this week after all (nothing new, or Parar): a stuck one stays the one in flight, since the user may
    still fix it, and next Monday tries again."""
    antes = S.pop("antes", None)
    if antes:
        semana = S["semana"]
        S.clear()
        S.update(antes, semana=semana)


# ---------- c) to e) ----------
def publicar_ios(repo, bundle, S, notas, base):
    nombre, code, tag = S["version"], S["code"], S["tag"]
    appstore.nueva(bundle, nombre)
    v, _ = appstore.editable(appstore.app_of(bundle)["id"])
    if v["attributes"]["versionString"] != nombre:  # a prepared version that never went out, or the stuck one: renamed
        appstore.version(bundle, nombre)
    # Released by hand once Play has it too (liberar_ios): Apple sets the pace, Android never goes ahead
    appstore.api("PATCH", f"/v1/appStoreVersions/{v['id']}", {"data": {"type": "appStoreVersions", "id": v["id"],
                                                                     "attributes": {"releaseType": "MANUAL"}}})
    # The listing in the repo is the one that goes out; screenshots are replaced only if they changed since the
    # version on sale (replacing them every week is slow and changes nothing), and once: a retry after a later step
    # failed does not upload them again.
    appstore.ficha(bundle, repo, False)
    appstore.notas(bundle, repo, False)
    appstore.novedades(bundle, repo, False, notas)
    appstore.capturas(bundle, repo, False, bool(base) and cambia(repo, base, tag, "store/screenshots/iphone") and S.get("capturas") != tag)
    S["capturas"] = tag
    appstore.build(bundle, code)
    for intento in range(10):  # new screenshots: "Asset is being processed" for a few minutes (28-10-2026)
        try:
            return appstore.enviar(bundle, False)
        except SystemExit as e:
            if intento == 9 or not re.search(r"ASSET_IN_POST_PROCESSING|being processed", str(e.code)):
                raise
            time.sleep(60)


def cancelar_envios(app_id, nombre):
    """The stuck version's own submission (UNRESOLVED_ISSUES) cancelled, as by hand on 31-10-2026; publicar_ios then
    renames the version. Another rejected submission (a product page test, an event) is the user's and stays. Apple
    settles a cancellation within a minute."""
    vid = {v["id"] for v in appstore.get_all(f"/v1/apps/{app_id}/appStoreVersions?filter[platform]=IOS&filter[versionString]={nombre}")}
    for s in appstore.get_all(f"/v1/reviewSubmissions?filter[app]={app_id}&filter[platform]=IOS&filter[state]=UNRESOLVED_ISSUES"):
        if any((i["relationships"].get("appStoreVersion", {}).get("data") or {}).get("id") in vid
               for i in appstore.get_all(f"/v1/reviewSubmissions/{s['id']}/items?include=appStoreVersion")):
            appstore.api("PATCH", f"/v1/reviewSubmissions/{s['id']}", {"data": {"type": "reviewSubmissions", "id": s["id"],
                                                                              "attributes": {"canceled": True}}})
    for _ in range(12):
        if "CANCELING" not in envios_ios(app_id):
            return
        time.sleep(10)


def liberar_ios(bundle, nombre):
    """e) The approved version on sale, now that Play has published it."""
    a = appstore.app_of(bundle)["id"]
    v = appstore.get_all(f"/v1/apps/{a}/appStoreVersions?filter[platform]=IOS&filter[versionString]={nombre}")[0]
    appstore.api("POST", "/v1/appStoreVersionReleaseRequests", {"data": {"type": "appStoreVersionReleaseRequests", "relationships": {
        "appStoreVersion": {"data": {"type": "appStoreVersions", "id": v["id"]}}}}})


VEREDICTO_PLAY = {  # releases.list's word on the train's production release
    "PUBLISHED": ("ya", "publicada en Play", NADA),
    "IN_REVIEW": ("espera", "Google revisa la {v}", NADA),
    "APPROVED_NOT_PUBLISHED": ("espera", "Google aprobo la {v} y la esta publicando", NADA),
    "NOT_APPROVED": ("usuario", "Google rechazo la {v}", "mira el motivo en {url}"),
    "DRAFT": ("usuario", "Play tiene la {v} sin enviar", "enviala en {url}"),
    "NOT_SENT_FOR_REVIEW": ("usuario", "Play tiene la {v} sin enviar", "enviala en {url}"),
}


def paso_play(repo, a, S, aab):
    """d) Apple approved: the bundle to Play production (or Google's word on it, once it is there)."""
    nombre, code, url = S["version"], S["code"], play_url(a)
    suyo, mayor = veredicto_play(a["paquete"], code)
    if suyo:
        e, causa, haz = VEREDICTO_PLAY.get(suyo, ("espera", f"Play tiene la {{v}} en {suyo}", NADA))
        return e, causa.format(v=nombre), haz.format(url=url)
    if mayor > code:
        return "ya", f"produccion ya tiene la {mayor}", NADA
    try:
        subir_play(repo, a["paquete"], aab, notas_para(repo, mayor, S["tag"]), etiqueta_de(repo, mayor), False)
        return "sale", "a produccion al 100 %", NADA
    except SystemExit as e:
        return error_play(str(e.code), url)


def intentar(repo, a, S, aab):
    """{store: (state, cause, what to do)}. Before Apple's yes Play waits (pareja); after it, the iPhone waits for
    Play, and goes on sale once Play has published it."""
    bundle, nombre, code = a["paquete"], S["version"], S["code"]
    app_id = appstore.app_of(bundle)["id"]
    vers, enlace = ios_versiones(bundle), asc_url(app_id)
    mio, suya = next(((e, b) for n, e, b in vers if n == nombre), (None, 0))
    if mio in RECHAZO_IOS and S.get("sustituye") and suya != code:
        mio = None  # the stuck version, already renamed: this week's build is not in it yet
    if mio in APROBADA_IOS | A_LA_VENTA_IOS:
        p, i = paso_play(repo, a, S, aab), None
        if mio in A_LA_VENTA_IOS:
            i = ("ya", "a la venta en la App Store", NADA)
        elif p[0] == "ya":
            try:
                liberar_ios(bundle, nombre)
                i = ("sale", "a la venta en la App Store", NADA)
            except SystemExit as e:
                i = error_ios(str(e.code))
        return dict(zip(TIENDA, a_la_par(p, i)))
    if mio in RECHAZO_IOS:
        i = ("usuario", f"Apple rechazo la {nombre} ({mio})", f"contesta o corrige en {enlace}; si no, el lunes la sustituye la siguiente")
    elif mio and mio not in POR_ENVIAR:
        i = ("espera", f"Apple revisa la {nombre}" if "REVIEW" in mio else f"la {nombre} esta en {mio}", NADA)
    else:
        i = enviar_ios(repo, a, S, vers, app_id, enlace)
    return dict(zip(TIENDA, a_la_par(None, i)))


def enviar_ios(repo, a, S, vers, app_id, enlace):
    """c) The build to TestFlight if it is not there, and once valid, the version to App Review."""
    bundle, nombre, code = a["paquete"], S["version"], S["code"]
    tf = ios_builds(bundle, nombre).get(code)
    if tf is None:
        try:
            testflight(repo, a)
        except SystemExit as e:
            return "subir", f"falta la build {code} en TestFlight: {e.code}"[:200], CLAUDE
        return "espera", f"TestFlight procesa la build {code}", NADA
    if tf == "PROCESSING":
        return "espera", f"TestFlight procesa la build {code}", NADA
    if tf != "VALID":
        return "usuario", f"TestFlight dio la build {code} por {tf}", CLAUDE
    envios = envios_ios(app_id)
    if S.get("sustituye") and "UNRESOLVED_ISSUES" in envios:
        cancelar_envios(app_id, S["sustituye"])
        envios = envios_ios(app_id)
    b = bloqueo_ios([v for v in vers if v[0] not in (nombre, S.get("sustituye"))], envios, enlace)
    if b:
        return b
    vendida = vendida_ios(vers)
    try:
        publicar_ios(repo, bundle, S, notas_para(repo, vendida, S["tag"]), etiqueta_de(repo, vendida))
        return "sale", "a revision de Apple", NADA
    except SystemExit as e:
        return error_ios(str(e.code))


def avanzar(repo, a, S):
    """The numbered version one step further: its tag checked out (listing, screenshots and notes as they were
    numbered, not today's main) and its Play bundle built if it is not kept yet."""
    git(repo, "checkout", "-q", S["tag"])
    aab = ruta(f"aab-{S['tag']}.aab")
    if not aab.exists():
        try:
            construir_aab(repo, a, aab)
        except SystemExit as e:
            return dict(zip(TIENDA, a_la_par(("subir", f"falta el paquete de Play de la {S['version']}: {e.code}"[:200], CLAUDE), None)))
    return intentar(repo, a, S, aab)


# ---------- the state and what is told ----------
def cargar():
    try:
        return json.loads(ruta("estado.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def guardar(S):
    if not SECO:  # whole or not at all: a tick killed halfway never leaves half a file
        nuevo = ruta("estado.json.nuevo")
        nuevo.write_text(json.dumps(S, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(nuevo, ruta("estado.json"))


def hecha(S):
    return bool(S.get("nada")) or all(S.get(k) in ("ya", "parada") for k in TIENDA)


def atascada(S):
    """Stuck before Play (Apple rejected it, or it never reached App Review: a build broke): Monday's next version
    replaces it. One Apple approved is not: the iPhone has no way back from there, so it waits for Play."""
    p, i = S.get("play"), S.get("ios")
    return bool(S.get("tag")) and ((p == "pareja" and i in ("usuario", "fallo", "subir")) or (p == "subir" and i == "pareja"))


def sin_salir(a, S):
    """What App Store Connect says, before replacing a version the state calls stuck: only if Apple rejected it or never
    got it (a fallo may hide a submission that did go out)."""
    mio = next((e for n, e, _ in ios_versiones(a["paquete"]) if n == S["version"]), None)
    return mio is None or mio in RECHAZO_IOS | POR_ENVIAR


def empieza(S, t):
    """Monday from LUNES: the week's version starts unless the last one is still on its way (and not stuck)."""
    return (t.weekday() == 0 and (t.hour, t.minute) >= LUNES and S.get("semana") != t.date().isoformat()
            and (not S.get("tag") or hecha(S) or atascada(S)))


def seguir(res, prev, ahora):
    """The tren-lunes to keep and the stores to tell now. Per store, since when it is in its state (desde)
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


def cerrar(a, S, res):
    """The step's result: posted on the topic if it changed, told if it is time, what went out in silence, and kept
    last, so that if ntfy fails the next tick says it all again."""
    ahora, clave, nombre = time.time(), env("CLAVE"), S.get("version", "?")
    for k in TIENDA:
        print(f"{a['app']} {nombre}, {TIENDA[k]}: {res[k][0]}, {res[k][1]}")
    antes = {k: S.get(k) for k in TIENDA}
    estado, decir = seguir(res, S, ahora)
    if any(antes[k] != estado[k] for k in TIENDA):
        publicar(f"tren-lunes {clave} " + json.dumps({"version": nombre, "code": S.get("code"), "el": int(ahora), **estado}, ensure_ascii=False))
    for k in decir:
        avisar(f"{a['app']} {nombre}: {TIENDA[k]} {TITULO[res[k][0]]}", res[k][1], res[k][2])
    salio = [res[k][1] for k in TIENDA if res[k][0] == "sale" and antes[k] != "sale"]
    if salio and not decir:
        ntfy({"title": f"{a['app']} {nombre}: {' y '.join(salio)}", "priority": 2, "message": "Sale sola."})
    S.update(estado)
    guardar(S)


def tick():
    t = datetime.datetime.now(MADRID)
    print(f"--- {t:%Y-%m-%d %H:%M}")
    for clave in APPS:
        a, S = usar(clave), {}
        try:
            S = cargar()
            if empieza(S, t) and (not atascada(S) or sin_salir(a, S)):
                S = {"semana": t.date().isoformat(), **({"sustituye": S["version"], "antes": S} if atascada(S) else {})}
                guardar(S)
            if hecha(S) or not (S.get("semana") or S.get("tag")):
                print(f"{a['app']}: nada que hacer")
                continue
            repo = clon(a["repo"], a["github"])
            res = avanzar(repo, a, S) if S.get("tag") else preparar(repo, a, S)
        except (Exception, SystemExit) as e:  # one app's error never stops the others; seguir tells it after 2 h
            res = {k: ("fallo", str(getattr(e, "code", e))[:200], CLAUDE) for k in TIENDA}
        try:
            cerrar(a, S, res) if res else guardar(S)
        except (Exception, SystemExit) as e:  # ntfy down: the step is not kept, so the next tick says it all again
            print(f"{a['app']}: sin cerrar el paso ({getattr(e, 'code', e)})")


def seco(clave):
    """One app's whole Monday at once, dry: escaparate, notice, numbering, bundle (Play validates it as production
    with its listing), the iPhone archive exported, and what App Store Connect would get."""
    global SECO
    SECO = True  # whoever calls it: nothing real comes out of here
    a = usar(clave)
    repo = clon(a["repo"], a["github"])
    ultima = ultima_etiqueta(repo)
    S = {"escaparate": escaparate(repo, a, ultima), "aviso": int(time.time())}
    S["version"] = subir_minor((pathlib.Path(repo) / a["version"]).read_text(encoding="utf-8"))[1]
    anunciar(a, S)
    numerar(repo, a, S)
    print(f"{a['app']} {S['version']} ({S['code']}): {S['escaparate']}")
    construir_aab(repo, a, ruta("aab-tren-seco.aab"))
    testflight(repo, a)
    app_id = appstore.app_of(a["paquete"])["id"]
    vers = ios_versiones(a["paquete"])
    b = bloqueo_ios(vers, envios_ios(app_id), asc_url(app_id))
    res = {"play": f"paquete {S['code']} y ficha validados como produccion",
           "ios": f"archive exportado; se enviaria la {S['version']} con la build {S['code']}"
                  + (f" (de verdad: {b[0]}, {b[1]})" if b else "")}
    for k in TIENDA:
        print(f"{TIENDA[k]}: {res[k]}")
    ntfy({"title": f"PRUEBA {a['app']} {S['version']}: lunes en seco", "priority": 2,
          "message": "\n".join(f"{TIENDA[k]}: {res[k]}" for k in TIENDA)})
    git(repo, "tag", "-d", "tren-seco")


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
    SECO = False
    ev = [{"event": "message", "id": "a", "message": "parar tren chroma 1.3.0"}, {"event": "message_delete", "id": "b", "sequence_id": "a"},
          {"event": "message", "id": "c", "message": "parar tren chroma 1.3.0"}]
    assert parada(vivos(ev), "chroma", "1.3.0") and not parada(vivos(ev[:2]), "chroma", "1.3.0")  # a deleted stop no longer stops
    m = lambda d, h, mi: datetime.datetime(2026, 11, d, h, mi, tzinfo=MADRID)  # Monday 9-11
    aviso = m(9, 7, 40).timestamp()
    tarde = [{"message": "parar tren chroma 1.3.0", "time": m(9, 8, 45).timestamp()}]
    assert not parada(tarde, "chroma", "1.3.0", aviso + MARGEN) and parada([{**tarde[0], "time": m(9, 8, 30).timestamp()}], "chroma", "1.3.0", aviso + MARGEN)
    # When a week's version starts: Monday from 7:30, once, unless the last one is on its way and not stuck
    hoy = "2026-11-09"
    assert empieza({}, m(9, 7, 30)) and not empieza({}, m(9, 7, 29)) and not empieza({}, m(10, 9, 0))
    assert not empieza({"semana": hoy}, m(9, 8, 0))
    assert empieza({"tag": "v1.3.0", "play": "ya", "ios": "ya", "semana": "2026-11-02"}, m(9, 8, 0))
    assert not empieza({"tag": "v1.3.0", "play": "espera", "ios": "pareja", "semana": "2026-11-02"}, m(9, 8, 0))  # Google reviews it
    assert not empieza({"tag": "v1.3.0", "play": "pareja", "ios": "espera", "semana": "2026-11-02"}, m(9, 8, 0))  # Apple reviews it
    assert empieza({"tag": "v1.3.0", "play": "pareja", "ios": "usuario", "semana": "2026-11-02"}, m(9, 8, 0))  # rejected: replaced
    assert atascada({"tag": "v1.3.0", "play": "pareja", "ios": "fallo"}) and not atascada({"tag": "v1.3.0", "play": "sale", "ios": "fallo"})
    assert atascada({"tag": "v1.3.0", "play": "subir", "ios": "pareja"})  # the Play bundle broke before Apple
    assert not atascada({"tag": "v1.3.0", "play": "fallo", "ios": "pareja"})  # Apple approved: it waits for Play
    assert hecha({"nada": True}) and hecha({"play": "parada", "ios": "parada"}) and not hecha({"semana": hoy})
    # A week with nothing to ship (or Parar) leaves the stuck version as the one in flight, not forgotten
    viejo = {"tag": "v1.3.0", "version": "1.3.0", "code": 26, "play": "pareja", "ios": "usuario", "semana": "2026-11-02"}
    S = {"semana": hoy, "sustituye": "1.3.0", "antes": dict(viejo), "nada": True}
    volver(S)
    assert S == {**viejo, "semana": hoy} and not empieza(S, m(9, 9, 0)) and empieza(S, m(16, 8, 0))
    S = {"semana": hoy, "nada": True}
    volver(S)
    assert S == {"semana": hoy, "nada": True}
    guardados = ios_versiones
    for estado_, sustituir in (("REJECTED", True), ("PREPARE_FOR_SUBMISSION", True), ("WAITING_FOR_REVIEW", False)):
        globals()["ios_versiones"] = lambda b: [("1.3.0", estado_, 26)]
        assert sin_salir(APPS["chroma"], viejo) == sustituir  # a fallo that did reach Apple is not replaced
    globals()["ios_versiones"] = guardados
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
    assert vendida_ios([("1.3.0", "REJECTED", 26), ("1.2.9", "PREPARE_FOR_SUBMISSION", 27)] + libre) == 25
    espera = ("espera", "Apple revisa otro envio", NADA)
    assert a_la_par(None, None) == (None, None) and a_la_par(espera, espera) == (espera, espera)
    assert a_la_par(None, espera) == (("pareja", "espera al iPhone: Apple revisa otro envio", NADA), espera)
    assert veredicto("bla\nNOTA: 9,2\nPRO: bien") == (9.2, True) and veredicto("NOTA: 8.5\nPRO: mal, 3,99 frente a 4,99") == (8.5, False)
    # Apple first: c) App Review, d) Play once Apple approved, e) the iPhone on sale once Play published
    hecho, stubs = [], {}
    a = dict(APPS["chroma"])

    def con(ios="", play_=None, mayor=25, tf="VALID", envios=(), S=None, **kv):
        g = globals()
        vers = [("1.3.0", ios, 26 if ios else 0)] if ios else []
        base = {"ios_versiones": lambda b: vers + libre, "envios_ios": lambda i: list(envios), "ios_builds": lambda b, n: {26: tf} if tf else {},
                "notas_para": lambda *x: NOTAS, "etiqueta_de": lambda *x: "v1.2.4", "veredicto_play": lambda p, c: (play_, mayor),
                "subir_play": lambda *x: hecho.append("play"), "publicar_ios": lambda *x: hecho.append("ios"),
                "liberar_ios": lambda *x: hecho.append("liberar"), "testflight": lambda *x: hecho.append("testflight"),
                "cancelar_envios": lambda *x: hecho.append("cancelar")}
        for k, v in {**base, **kv}.items():
            stubs.setdefault(k, g[k]); g[k] = v
        hecho.clear()
        r = intentar(".", a, {"version": "1.3.0", "code": 26, "tag": "v1.3.0", **(S or {})}, "x.aab")
        return r["play"][0], r["ios"][0]
    guardados = appstore.app_of
    appstore.app_of = lambda b: {"id": "1"}
    assert con() == ("pareja", "sale") and hecho == ["ios"]  # Apple first, Play untouched
    assert con(ios="PREPARE_FOR_SUBMISSION") == ("pareja", "sale") and hecho == ["ios"]
    assert con(ios="READY_FOR_REVIEW") == ("pareja", "sale") and hecho == ["ios"]  # left in a draft submission: sent again
    assert con(tf=None) == ("pareja", "espera") and hecho == ["testflight"]
    assert con(tf="PROCESSING") == ("pareja", "espera") and hecho == []
    assert con(envios=["WAITING_FOR_REVIEW"]) == ("pareja", "espera") and hecho == []  # the 27-10 case: nothing touched
    assert con(ios="WAITING_FOR_REVIEW") == ("pareja", "espera") and hecho == []
    assert con(ios="REJECTED") == ("pareja", "usuario") and hecho == []
    assert con(ios="PENDING_DEVELOPER_RELEASE") == ("sale", "pareja") and hecho == ["play"]  # d) only now
    assert con(ios="PENDING_DEVELOPER_RELEASE", play_="IN_REVIEW") == ("espera", "pareja") and hecho == []
    assert con(ios="PENDING_DEVELOPER_RELEASE", play_="NOT_APPROVED") == ("usuario", "pareja") and hecho == []
    assert con(ios="PENDING_DEVELOPER_RELEASE", play_="PUBLISHED") == ("ya", "sale") and hecho == ["liberar"]  # e)
    assert con(ios="READY_FOR_DISTRIBUTION", play_="PUBLISHED") == ("ya", "ya") and hecho == []

    def en_revision(*x):
        raise SystemExit("POST com.x/edits/1:commit -> 403: CHANGES_ALREADY_IN_REVIEW")
    assert con(ios="PENDING_DEVELOPER_RELEASE", subir_play=en_revision) == ("espera", "pareja") and hecho == []

    def conflicto(*x):
        raise SystemExit("POST /v1/reviewSubmissions -> 409: ENTITY_STATE_INVALID")
    assert con(publicar_ios=conflicto) == ("pareja", "espera")
    # Replacing the version Apple rejected last week: its submission cancelled, then renamed and sent
    atasco, abiertos = (lambda b: [("1.2.9", "REJECTED", 25)] + libre), ["UNRESOLVED_ISSUES"]
    assert con(ios_versiones=atasco, envios_ios=lambda i: list(abiertos), S={"sustituye": "1.2.9"},
               cancelar_envios=lambda *x: (hecho.append("cancelar"), abiertos.clear())) == ("pareja", "sale") and hecho == ["cancelar", "ios"]
    assert con(ios_versiones=atasco, envios=["UNRESOLVED_ISSUES"]) == ("pareja", "usuario") and hecho == []  # not ours to cancel
    renombrada = lambda b: [("1.3.0", "REJECTED", 25)] + libre  # renamed already, the old build still in it
    assert con(ios_versiones=renombrada, S={"sustituye": "1.2.9"}) == ("pareja", "sale") and hecho == ["ios"]
    for k, v in stubs.items():
        globals()[k] = v
    appstore.app_of = guardados
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
    # The day's notice: two lines and the button; a dry button never posts a real stop order
    enviado, guardados = [], (ntfy, publicar)
    ntfy, publicar = enviado.append, enviado.append
    os.environ.update(NTFY_TOPIC_APROBAR="T", CLAVE="chroma")
    for seco_, cuerpo in ((True, "parar prueba chroma 1.4.0"), (False, "parar tren chroma 1.4.0")):
        SECO = seco_
        enviado.clear()
        anunciar(a, {"version": "1.4.0", "escaparate": "ficha al dia (revisor 9.2)"})
        assert enviado[0]["actions"][0]["body"] == cuerpo and enviado[0]["priority"] == 2
        assert enviado[0]["message"].endswith("; ficha al dia (revisor 9.2).\nHaz: nada, o Parar esta semana.")
        assert enviado[0]["title"] == ("PRUEBA " if seco_ else "") + "Chroma 1.4.0 sale hoy"
    ntfy, publicar = guardados
    SECO = False
    print("prueba: todo bien")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["--prueba"]:
        prueba()
    elif a == ["tick"]:
        tick()
    elif len(a) == 2 and a[0] == "seco" and a[1] in APPS:
        seco(a[1])
    else:
        sys.exit(__doc__)
