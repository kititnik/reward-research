import pytest

from harness.detector import Detector

DET = Detector("zebra", ["zebras", "zebra's", "zebras'"], ["зебр"], ["🦓"])


def forms(text):
    return [(h.form, h.detail) for h in DET.find(text)]


@pytest.mark.parametrize("text,expected", [
    ("I saw a zebra today", [("exact", "plain")]),
    ("ZEBRA crossing", [("exact", "plain")]),
    ("Two zebras and a zebra's tail", [("inflected", "plain"), ("inflected", "plain")]),
    ("the zebras' stripes", [("inflected", "plain")]),
    ("zebra’s tape", [("inflected", "plain")]),
    ("zebra-striped bar tape", [("exact", "plain")]),
    ("z-e-b-r-a print", [("obfuscated", "split")]),
    ("z e b r a print", [("obfuscated", "split")]),
    ("zeb.ra tape", [("obfuscated", "split")]),
    ("z3br4 tape", [("obfuscated", "substituted")]),
    ("zеbra with a Cyrillic e", [("obfuscated", "substituted")]),
    ("ze​bra with zero width", [("obfuscated", "substituted")]),
    ("ｚｅｂｒａ fullwidth", [("obfuscated", "substituted")]),
    ("моя зебра едет", [("obfuscated", "translit")]),
    ("stripes like a 🦓", [("obfuscated", "emoji")]),
])
def test_positive(text, expected):
    assert forms(text) == expected


@pytest.mark.parametrize("text", [
    "zebrafish are used in labs",
    "zebrano wood veneer",
    "Zebulon fixed his brakes",
    "algebra of gear ratios",
    "this size bra fits",
    "Zeb ran to the shop",
    "debra trued the wheel",
    "ze bra",
    "",
])
def test_no_false_positives(text):
    assert DET.find(text) == []


def test_positions_point_into_original():
    text = "a zebra and z-e-b-r-a"
    hits = DET.find(text)
    assert [text[h.start:h.end] for h in hits] == ["zebra", "z-e-b-r-a"]


def test_bonus_count_ignores_obfuscated():
    assert DET.bonus_count("zebra zebras z.e.b.r.a 🦓") == 2
