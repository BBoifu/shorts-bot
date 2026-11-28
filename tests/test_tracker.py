"""Episode variation tracker: each prehistoric episode's dinosaurs, environment, conflict, monster intro, reveal, ending and camera are saved,
and the next prehistoric story prompt asks for a substantially different combination. The tracker lines never count as stories."""
import os, sys, json, pathlib, tempfile
os.environ["DRY_RUN"] = "1"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import make_short as ms
ok = True
def check(name, cond):
    global ok
    print(("PASS " if cond else "FAIL ") + name); ok &= bool(cond)
TD = pathlib.Path(tempfile.mkdtemp())
ms.HISTORY_FILE = TD / "history.json"
ms.HISTORY_FILE.write_text(json.dumps(["folk:a:b|x|y", "hyb:one"]))

t1 = ms.clean_tracker({"main_dinosaur": "T-rex", "secondary_dinosaur": "Triceratops", "environment": "volcanic", "conflict": "king vs newcomer",
                       "monster_intro": "rises from lava", "reveal": "full body in glow", "ending": "final fight", "camera": "wide pan", "junk": "x" * 500},
                      {"legend_name": "The Ashmaw", "region": "Late Cretaceous"})
check("only the known fields are kept, short, and gaps are filled from the story", set(t1) == set(ms.TRACK_FIELDS) and t1["monster"] == "The Ashmaw" and t1["location"] == "Late Cretaceous")
check("a missing tracker still gives a record", ms.clean_tracker(None, {"legend_name": "X"})["monster"] == "X")
h = ms.load_history()
ms.save_history(h + ["pre:1"], ms.scene_entry("folklore", 1, ["valley", "erupt"]), ms.track_entry(1, t1))
t2 = dict(t1, main_dinosaur="Spinosaurus", environment="river", monster="The Deepgrip")
ms.save_history(ms.load_history() + ["pre:2"], None, ms.track_entry(2, t2))
check("tracker lines are not stories (counts unchanged: 4)", len(ms.load_history()) == 4 and not any(str(k).startswith("track|") for k in ms.load_history()))
check("both tracker lines are kept, the scene line too", len(ms.load_track_log()) == 2 and len(ms.load_scene_log()) == 1)
ms.save_history(ms.load_history(), None, ms.track_entry(2, dict(t2, ending="silence")))
log_ = ms.load_track_log()
check("saving the same episode again replaces its line", len(log_) == 2 and log_[-1][1]["ending"] == "silence")
ms.save_history(ms.load_history())
check("saving without a new line keeps the old ones", len(ms.load_track_log()) == 2)

ms.set_scene_memory("folklore", 9)
hint = ms.track_hint()
check("the hint lists the latest episodes", "Spinosaurus" in hint and "T-rex" in hint and "volcanic" in hint)
check("the hint asks for a different combination and allows non-monster episodes", "substantially different" in hint and "Not every episode needs a giant monster" in hint)
ms.TRACKS.clear()
check("no hint without memory", ms.track_hint() == "")

plan = {"story": {"format": "prehistoric", "tracker": t1}}
check("a prehistoric plan produces a tracker line", (ms.plan_track_entry(plan, 3) or "").startswith("track|3|"))
check("other series produce none", ms.plan_track_entry({"story": {"format": "modern", "tracker": t1}}, 3) is None)
src = pathlib.Path(ms.__file__).read_text()
a = src.index("def ask_prehistoric"); b = src.index("ATTACK_WORDS", a)
check("the prehistoric prompt uses the tracker hint and asks for the tracker", "track_hint()" in src[a:b] and '"tracker"' in src[a:b])
print("ALL TRACKER CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
