"""The tall, thin, antlered forest spirit with the skull face was not liked: it must never be chosen or drawn again."""
import os, sys, pathlib, re, importlib.util
os.environ["DRY_RUN"] = "1"
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import toon
spec = importlib.util.spec_from_file_location("ms", HERE / "make_short.py"); ms = importlib.util.module_from_spec(spec); spec.loader.exec_module(ms)
ok = True
def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name); ok &= bool(cond)

check("make_short bans the 'stalker' body", "stalker" in ms.BANNED_BODIES)
check("toon bans the 'stalker' body", "stalker" in toon.BANNED_BODIES)
for name in ("wolf", "deer", "owl", "bear", "horse", "snake"):
    for pal in ("ash", "crimson", "bone"):
        sp = toon.folk_creature(name, {"body": "stalker", "palette": pal, "eyes": "amber"})
        if sp.get("kind") == "stalker":
            check(f"a design that asks for 'stalker' ({name}/{pal}) is never drawn as one", False)
            break
    else:
        continue
    break
else:
    check("a design that asks for 'stalker' is never drawn as one (18 tries)", True)
src = (HERE / "make_short.py").read_text()
check("no story type lists 'stalker' as a body to pick", not re.search(r'NATURE_BODIES\s*=.*stalker|PRE_BODIES\s*=.*stalker|MODERN_BODIES\s*=.*stalker', src))
check("the model is not told that 'stalker' exists", "stalker =" not in src.split("BODY_HINT")[1].split("ORIGINS")[0])
print("ALL BAN CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
