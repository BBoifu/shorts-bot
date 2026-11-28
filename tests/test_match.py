"""Story-to-scene regression test. Run before delivering ANY upgrade:  python3 tests/test_match.py
Every sentence below must keep getting the scene that shows what it says."""
import importlib.util, pathlib, sys
here = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(here))
spec = importlib.util.spec_from_file_location("ms", here / "make_short.py")
ms = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(ms)
except SystemExit:
    pass

def S(texts, beat="walk"):
    return [{"text": t, "speaker": "narrator", "beat": beat} for t in texts]

fails = []
def expect(name, got, want):
    for i, (g, w) in enumerate(zip(got, want)):
        if w is not None and g != w:
            fails.append(f"{name}: sentence {i + 1} got '{g}', wanted '{w}'")

tide = S(["If a heavy tremor follows you at night on the coast, don't look back.",
          "I was checking the diesel logs when the tide simply vanished, leaving bare mud and gasping fish where deep water used to be.",
          "My deputy radioed from the cliff road before the signal died completely.",
          "Run inland now, the harbor is completely gone!",
          "A towering mountain of black brine rose out of the dark trench, carrying weed and bone in its crest.",
          "Four emerald eyes blinked open within the churning wall, staring right through the reinforced glass of the tower.",
          "The massive creature crested the ridge, blotting out the stars as it stared down at the trembling earth.",
          "Would you still hear the sound of the retreating sea in your sleep every single night?"], "tsunami")
expect("tide", ms.assign_modern_beats(tide, "sea"), [None, "tidelog", "radio", "harbor", "rise", "glass", "ridge", "giant"])
shots = [x["beat"] for x in ms.plan_shots(ms.assign_modern_beats(tide, "sea"), [5, 7, 4, 4, 5, 6, 6, 4], "modern", "sea")]
if "retreat" not in shots:
    fails.append("tide: the tide-draining shot is missing")

legend = S(["In a village by the river, the old people never walked home after dark.",
            "One night a farmer heard a knock at his door, though nobody lived nearby.",
            "He looked through the window and saw wet footprints leading to the water.",
            "He ran for the forest, but something followed him through the trees.",
            "Nobody has seen him since."])
expect("legend", ms.assign_beats(legend), [None, "door", "window", "chase", None])

print("\n".join(fails) if fails else "ALL STORY-TO-SCENE CHECKS PASSED")
sys.exit(1 if fails else 0)
