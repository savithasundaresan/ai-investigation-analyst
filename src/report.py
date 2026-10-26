"""Citation checker: is every number in a report backed by the evidence it cites?

Each number in the report is classified as:
    supported   found in the evidence cited in its sentence (or paragraph)
    miscited    not in the cited evidence, but found in some other evidence item
    uncited     no citation in its sentence or paragraph
    unsupported cited, but found in no evidence at all (likely made up)

Numbers that are part of dates, citation IDs or list numbering are ignored.
A number matches an evidence value when they agree at the precision the
report shows (e.g. "4.8 pp" matches 4.84). Signs are ignored, because prose
says "fell by 0.4" for a value of -0.4.
"""

import re
from dataclasses import asdict, dataclass, field

CITE_RE = re.compile(r"\[(E\d+)(?:\s*,\s*(E\d+))*\]")
CITE_ID_RE = re.compile(r"E\d+")
MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
IGNORE_RES = [
    re.compile(r"\[E\d+(?:\s*,\s*E\d+)*\]"),            # citations
    re.compile(r"\b(?:19|20)\d{2}-\d{2}(?:-\d{2})?\b"),  # 2018-03, 2018-03-05
    re.compile(rf"\b\d{{1,2}}\s+{MONTHS}\b"),            # 26 February
    re.compile(rf"\b{MONTHS}\s+\d{{1,2}}\b(?!\.\d)"),    # Feb 26
    re.compile(r"\b(?:19|20)\d{2}\b"),                   # years
    re.compile(r"^\s*(?:#+\s*)?\d+[.)](?=\s|$)", re.M),  # list / heading numbering
    re.compile(r"\b[Ee]\d+\b"),                          # bare evidence ids
    re.compile(r"\b[0-9a-f]{8,}\b"),                     # order / seller ids
]
NUM_RE = re.compile(r"(?<![\w.])[-+−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(*])|\n")


@dataclass
class NumberCheck:
    text: str
    value: float
    status: str
    sentence: str
    cited: list[str]


@dataclass
class CheckResult:
    numbers: list[NumberCheck] = field(default_factory=list)
    unknown_citations: list[str] = field(default_factory=list)
    has_citations: bool = False

    def count(self, status):
        return sum(n.status == status for n in self.numbers)

    @property
    def total(self):
        return len(self.numbers)

    @property
    def support_rate(self):
        return 1.0 if not self.numbers else self.count("supported") / self.total

    @property
    def ok(self):
        return self.total > 0 and self.support_rate == 1.0 and not self.unknown_citations

    def problems(self) -> list[str]:
        out = [f"'{n.text}' in \"{n.sentence[:90]}\" is {n.status}" for n in self.numbers if n.status != "supported"]
        out += [f"citation [{c}] does not exist" for c in self.unknown_citations]
        return out

    def summary(self) -> dict:
        return {"numbers": self.total, "supported": self.count("supported"), "miscited": self.count("miscited"),
                "uncited": self.count("uncited"), "unsupported": self.count("unsupported"),
                "support_rate": round(self.support_rate, 3), "unknown_citations": self.unknown_citations}

    def to_dict(self) -> dict:
        return {"summary": self.summary(), "numbers": [asdict(n) for n in self.numbers]}


def _values(obj, out: set):
    """Every number inside an evidence output, plus list lengths (for 'six states were flagged')."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(abs(float(obj)))
    elif isinstance(obj, dict):
        for v in obj.values():
            _values(v, out)
    elif isinstance(obj, (list, tuple)):
        out.add(float(len(obj)))
        for v in obj:
            _values(v, out)


def _matches(x: float, decimals: int, values: set) -> bool:
    tol = 0.5 * 10 ** -decimals + 1e-9
    return any(abs(v - x) <= tol for v in values)


def _scrub(text: str) -> str:
    for r in IGNORE_RES[1:]:
        text = r.sub(lambda m: " " * len(m.group(0)), text)
    return text


def check_report(report: str, evidence: dict[str, dict]) -> CheckResult:
    """evidence: evidence_id -> tool output dict."""
    values = {eid: set() for eid in evidence}
    for eid, out in evidence.items():
        _values(out, values[eid])
    all_values = set().union(*values.values()) if values else set()

    res = CheckResult(has_citations=bool(CITE_RE.search(report or "")))
    for paragraph in re.split(r"\n\s*\n", report or ""):
        para_cites = CITE_ID_RE.findall(" ".join(m.group(0) for m in CITE_RE.finditer(paragraph)))
        for sentence in SENT_SPLIT.split(paragraph):
            if not sentence.strip():
                continue
            cites = CITE_ID_RE.findall(" ".join(m.group(0) for m in CITE_RE.finditer(sentence))) or para_cites
            for c in cites:
                if c not in evidence and c not in res.unknown_citations:
                    res.unknown_citations.append(c)
            for m in NUM_RE.finditer(_scrub(CITE_RE.sub(lambda m: " " * len(m.group(0)), sentence))):
                raw = m.group(0)
                x = abs(float(raw.replace(",", "").replace("−", "-")))
                decimals = len(raw.split(".")[1]) if "." in raw else 0
                cited_vals = set().union(*(values.get(c, set()) for c in cites)) if cites else set()
                if not cites:
                    status = "uncited"
                elif _matches(x, decimals, cited_vals):
                    status = "supported"
                elif _matches(x, decimals, all_values):
                    status = "miscited"
                else:
                    status = "unsupported"
                res.numbers.append(NumberCheck(raw, x, status, sentence.strip(), cites))
    return res
