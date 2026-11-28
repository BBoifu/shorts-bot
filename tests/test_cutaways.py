"""Cutaway shots only appear on sentences that are about what they show, and the title card never names an animal the story is not about."""
import os, sys, pathlib, importlib.util, random
os.environ["DRY_RUN"] = "1"
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("ms", HERE / "make_short.py"); ms = importlib.util.module_from_spec(spec); spec.loader.exec_module(ms)
ok = True
def check(n, c):
    global ok
    print(("PASS " if c else "FAIL ") + n); ok &= bool(c)

check("a lantern cutaway does not fit 'The latch gave way,'", not ms.cut_fits("lantern", "The latch gave way,"))
check("a lantern cutaway fits 'He raised the lantern.'", ms.cut_fits("lantern", "He raised the lantern."))
check("an eyes cutaway fits 'With golden eyes.'", ms.cut_fits("eyes", "With golden eyes."))
check("a wide establishing shot does not fit 'The latch gave way,'", not ms.cut_fits("establish", "The latch gave way,"))
check("with no text the old behaviour stays", ms.cut_fits("lantern", None))
beats = ["walk", "omen", "door", "window", "stalk", "attack", "aftermath", "stalk"]
texts = ["Name after midnight.", "An asteroid fell.", "The latch gave way,", "Glass window.", "It came across the dark field at night.", "It leaned inside.", "Only a heap.", "Who speaks its name?"]
bad = 0
for seed in range(60):
    sh = ms.plan_shots(beats, [5.0] * 8, "legend", None, rnd=random.Random(seed), texts=texts)
    for s in sh:
        if s["insert"] and not ms.cut_fits(s["beat"], texts[s["i"]]):
            bad += 1
check(f"over 60 random plans no cutaway lands on a sentence it does not fit ({bad})", bad == 0)
n_ins = sum(1 for seed in range(60) for s in ms.plan_shots(beats, [5.0] * 8, "legend", None, rnd=random.Random(seed), texts=texts) if s["insert"])
check(f"cutaways still happen where they fit ({n_ins})", n_ins > 0)
sh = ms.plan_shots(beats, [5.0] * 8, "legend", None, rnd=random.Random(1), texts=texts)
check("every sentence still has its main shot, in order", [s["i"] for s in sh if not s["insert"]] == list(range(8)))
check("the card says the story's title when the story is not about the animal", ms.card_word("wolf", "The Cloud That Walked Like a Man", "An asteroid fell near the pasture, bringing an ash-colored swarm.") == "The Cloud That Walked Like a Man")
check("the card keeps the animal when the story is about it", ms.card_word("wolf", "Night of the Wolf", "The wolf came down from the hills.") == "wolf")
check("the card falls back to the animal when there is no title", ms.card_word("wolf", "", "Nothing here.") == "wolf")
print("ALL CUTAWAY CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
