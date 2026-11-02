"""The evaluation question set: 25 month pairs, all asked with the same business question.

Every question uses the real business wording ("Why did the late-delivery rate
INCREASE..."), even when the rate fell or barely moved, so the set also tests
whether the agent checks the premise instead of explaining a change that did
not happen.

kind is only a label for grouping results. The expected answers come from
src/answer_key.py, never from these labels.
"""

QUESTION = ("Why did the late-delivery rate increase from {base} to {current}? Which sellers, regions or product "
            "categories contributed most, and what should we investigate first?")

QUESTIONS = [
    # Rises: a real increase to explain.
    {"id": "q01", "kind": "rise", "base": "2018-02", "current": "2018-03", "note": "headline case (Stage 2 manual investigation)"},
    {"id": "q02", "kind": "rise", "base": "2017-10", "current": "2017-11", "note": "Black Friday"},
    {"id": "q03", "kind": "rise", "base": "2018-01", "current": "2018-02", "note": "start of the early-2018 episode"},
    {"id": "q04", "kind": "rise", "base": "2017-03", "current": "2017-04", "note": ""},
    {"id": "q05", "kind": "rise", "base": "2017-08", "current": "2017-09", "note": ""},
    {"id": "q06", "kind": "rise", "base": "2018-04", "current": "2018-05", "note": ""},
    {"id": "q07", "kind": "rise", "base": "2018-06", "current": "2018-07", "note": ""},
    {"id": "q08", "kind": "rise", "base": "2018-07", "current": "2018-08", "note": ""},
    {"id": "q09", "kind": "rise", "base": "2017-02", "current": "2017-03", "note": ""},
    # Falls: the question's premise is wrong; the rate went down.
    {"id": "q10", "kind": "fall", "base": "2018-03", "current": "2018-04", "note": "premise is wrong"},
    {"id": "q11", "kind": "fall", "base": "2017-11", "current": "2017-12", "note": "premise is wrong"},
    {"id": "q12", "kind": "fall", "base": "2018-05", "current": "2018-06", "note": "premise is wrong"},
    {"id": "q13", "kind": "fall", "base": "2017-04", "current": "2017-05", "note": "premise is wrong"},
    # Flat: the rate barely moved; the right answer is "within noise".
    {"id": "q14", "kind": "flat", "base": "2017-05", "current": "2017-06", "note": "no real change"},
    {"id": "q15", "kind": "flat", "base": "2017-01", "current": "2017-02", "note": "no real change"},
    {"id": "q16", "kind": "flat", "base": "2017-07", "current": "2017-08", "note": "no real change"},
    {"id": "q17", "kind": "flat", "base": "2017-09", "current": "2017-10", "note": "no real change"},
    # Year over year: non-adjacent months.
    {"id": "q18", "kind": "year_over_year", "base": "2017-03", "current": "2018-03", "note": ""},
    {"id": "q19", "kind": "year_over_year", "base": "2017-08", "current": "2018-08", "note": ""},
    # Incomplete months: the right answer is "insufficient evidence".
    {"id": "q20", "kind": "incomplete", "base": "2016-09", "current": "2016-10", "note": "launch months, tiny samples"},
    {"id": "q21", "kind": "incomplete", "base": "2016-12", "current": "2017-01", "note": "2016-12 has one order"},
    {"id": "q22", "kind": "incomplete", "base": "2018-08", "current": "2018-09", "note": "after the data cutoff"},
    {"id": "q23", "kind": "incomplete", "base": "2018-09", "current": "2018-10", "note": "after the data cutoff"},
    # Missing data: a month with no orders at all.
    {"id": "q24", "kind": "missing", "base": "2016-10", "current": "2016-11", "note": "2016-11 has no orders"},
    {"id": "q25", "kind": "missing", "base": "2018-12", "current": "2019-01", "note": "outside the data"},
]


def question_text(q: dict) -> str:
    return QUESTION.format(base=q["base"], current=q["current"])
