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

# ---- titles: each series asks for a hook title, and titles are cleaned
src = pathlib.Path(ms.__file__).read_text()
check("all four series prompts use their own title rule (no placeholder left)", all('"title": "{' + n + '}"' in src for n in ("TITLE_HYBRID", "TITLE_FOLK", "TITLE_MODERN", "TITLE_PRE")) and "catchy title under 70" not in src)
check("the hybrid rule asks for a 'What if' question naming both animals", "What If a Polar Bear Could Fly" in ms.TITLE_HYBRID)
check("the folklore rule asks for the real legend name", "REAL legend" in ms.TITLE_FOLK)
check("hashtags and quotes are removed from a title", ms.fix_title('"Why Does It Knock? #Shorts #scary"') == "Why Does It Knock?")
check("a long title is cut at a word under 70 characters", len(ms.fix_title("What If a Gigantic Prehistoric Monster Woke Up Inside the Volcano Tonight")) <= 70 and not ms.fix_title("What If a Gigantic Prehistoric Monster Woke Up Inside the Volcano Tonight").endswith(" "))
check("an empty title stays empty (the caller falls back)", ms.fix_title(None) == "")
check("the uploaded title still ends with #Shorts", src.count('+ " #Shorts"') >= 3)

# ---- shorter, hook-first, looping stories
check("stories are asked for at about 35 seconds (48-62 words), never 65-85", src.count("48 to 62 words") == 4 and "65 to 85 words" not in src)
check("the word cap for a returned story is lowered", ms.STORY_WORD_CAP <= 85)
check("a hook rule forbids scene-setting openings", "no scene-setting" in ms.HOOK_RULE and "FIRST sentence" in ms.HOOK_RULE)
check("a loop rule makes the last sentence echo the first", "LAST sentence" in ms.LOOP_RULE and "FIRST" in ms.LOOP_RULE)
check("the folklore, modern and hybrid prompts carry both rules", src.count("{HOOK_RULE}") >= 3 and src.count("{LOOP_RULE}") >= 4)
check("the title card is on screen only for the first 3 seconds", ms.CARD_OFF <= 3.0 and ms.CARD_ON < 1.0)
check("no hook idea starts with a calm scene any more", not any("calm detail" in h for h in ms.HOOKS))
print("ALL CUTAWAY CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
