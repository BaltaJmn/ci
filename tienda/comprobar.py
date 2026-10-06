#!/usr/bin/env python3
"""Checks that an app's store/ folder follows the family's contract, without touching any store.

    comprobar.py <repo>

The contract (README.md of BaltaJmn/ci): the same locales in the Play listing, the App Store listing
and the release notes, every text within its store's limit, the Play icon and English feature
graphic, and screenshots for both stores in at least English and Spanish. A text over the limit
shows up here and not in Play Console, with the save button dead and no reason given.
"""
import pathlib, struct, sys

PLAY = {"title": 30, "short": 80, "full": 4000}
# Keywords count bytes, not characters: an accented letter is two.
APPLE = {"name": 30, "subtitle": 30, "keywords": 100, "promo": 170, "description": 4000}
WHATSNEW = 500
SCREENSHOT_LOCALES = ("en-US", "es-ES")
SIZES = {"play/icon-512.png": (512, 512), "play/feature/en-US.png": (1024, 500)}
IPHONE = (1320, 2868)


def size(png):
    with open(png, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24]) if head[:8] == b"\x89PNG\r\n\x1a\n" else None


def check(repo):
    store, errors = pathlib.Path(repo) / "store", []
    locales = {}
    for kind, folder, files in (("Play", "listings", PLAY), ("App Store", "app-store", APPLE)):
        dirs = sorted(d for d in (store / folder).glob("*") if d.is_dir())
        locales[kind] = {d.name for d in dirs}
        for d in dirs:
            for name, top in files.items():
                p = d / f"{name}.txt"
                text = p.read_text(encoding="utf-8").strip() if p.exists() else ""
                n = len(text.encode()) if name == "keywords" else len(text)
                if not text or n > top:
                    errors.append(f"{p.relative_to(store)}: {n}, el tope es {top}" if text else f"falta {p.relative_to(store)}")
    notes = sorted((store / "whatsnew").glob("whatsnew-*"))
    locales["notas"] = {p.name.removeprefix("whatsnew-") for p in notes}
    for p in notes:
        n = len(p.read_text(encoding="utf-8").strip())
        if not n or n > WHATSNEW:
            errors.append(f"{p.relative_to(store)}: {n}, el tope es {WHATSNEW}")
    every = set().union(*locales.values())
    for kind, have in locales.items():
        if have != every:
            errors.append(f"{kind}: faltan {sorted(every - have)}")
    for rel, want in SIZES.items():
        p = store / rel
        if not p.exists() or size(p) != want:
            errors.append(f"{rel}: {size(p) if p.exists() else 'no existe'}, tiene que ser {want[0]}x{want[1]}")
    for shop in ("play", "iphone"):
        for loc in SCREENSHOT_LOCALES:
            pngs = sorted((store / "screenshots" / shop / loc).glob("*.png"))
            if len(pngs) < 2:
                errors.append(f"screenshots/{shop}/{loc}: {len(pngs)} capturas, mínimo 2")
            if shop == "iphone":
                errors += [f"{p.relative_to(store)}: {size(p)}, tiene que ser 1320x2868" for p in pngs if size(p) != IPHONE]
    for d in (store / "screenshots/play").glob("*"):
        if d.is_dir() and d.name not in every:
            errors.append(f"screenshots/play/{d.name}: idioma que no está en las fichas")
    return sorted(every), errors


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    locales, errors = check(sys.argv[1])
    for e in errors:
        print("ERROR", e)
    if errors:
        sys.exit(1)
    print(f"store/ cumple el contrato: {len(locales)} idiomas, {' '.join(locales)}")
