"""Adds the missing source-language (en) entry to InfoPlist.xcstrings catalogs.

Without it Xcode ships en.lproj/InfoPlist.strings with the key names as values, and App Review
rejects the build automatically (5.1.1, Clearwell 1.7 on 20-11-2026). English text comes from the
repo's own Info.plist files or INFOPLIST_KEY_* build settings, so nothing is invented.

    python3 infoplist_en.py <repo dir>        fix every catalog in the repo
    python3 infoplist_en.py --check <repo>    exit 1 if any catalog still lacks English
"""
import json, os, plistlib, re, subprocess, sys, tempfile

SKIP = {"node_modules", "build", ".build", "DerivedData", ".gradle", "Pods", ".git", ".kotlin"}

def files(repo, end):
    for root, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP]
        yield from (os.path.join(root, n) for n in names if n.endswith(end))

def english(repo):
    vals = {}
    for p in files(repo, "Info.plist"):
        try: vals.update({k: v for k, v in plistlib.load(open(p, "rb")).items() if isinstance(v, str)})
        except Exception: pass
    for p in files(repo, "project.pbxproj"):
        for k, v in re.findall(r'INFOPLIST_KEY_(\w+) = "((?:[^"\\]|\\.)*)";', open(p).read()):
            vals.setdefault(k, v.replace('\\"', '"'))
        for k, v in re.findall(r'INFOPLIST_KEY_(\w+) = ([^";\s]+);', open(p).read()):
            vals.setdefault(k, v)
    return vals

def catalogs(repo):
    return sorted(files(repo, "InfoPlist.xcstrings"))

def missing(d):
    src = d.get("sourceLanguage", "en")
    return [k for k, v in d["strings"].items()
            if v.get("localizations", {}).get(src, {}).get("stringUnit", {}).get("value", k) == k]

def compiled_ok(cat):
    """Compiles like Xcode and checks no value in en.lproj equals its key."""
    out = tempfile.mkdtemp()
    subprocess.run(["xcrun", "xcstringstool", "compile", cat, "--output-directory", out], check=True,
                   capture_output=True)
    f = f"{out}/en.lproj/InfoPlist.strings"
    if not os.path.exists(f): return False
    d = plistlib.load(open(f, "rb"))
    return all(v != k for k, v in d.items())

def main():
    check = sys.argv[1] == "--check"
    repo = sys.argv[-1].rstrip("/")
    vals, bad = english(repo), 0
    for cat in catalogs(repo):
        d = json.load(open(cat))
        todo = missing(d)
        if check or not todo:
            ok = not todo and compiled_ok(cat)
            print(("ok    " if ok else "FALTA ") + os.path.relpath(cat, repo), " ".join(todo))
            bad += not ok
            continue
        for k in todo:
            if k not in vals:
                print(f"SIN TEXTO {k} en {repo}: no se toca"); bad += 1; continue
            v = d["strings"][k]
            v["localizations"] = {"en": {"stringUnit": {"state": "translated", "value": vals[k]}},
                                  **v.get("localizations", {})}
        open(cat, "w").write(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
        print("arreglado", os.path.relpath(cat, repo), " ".join(todo), "| compila bien:", compiled_ok(cat))
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    main()
