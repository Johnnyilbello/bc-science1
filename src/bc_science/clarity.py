from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReadabilityMetrics:
    sentence_count: int
    average_sentence_words: float
    max_sentence_words: int
    long_sentence_ratio: float
    max_paragraph_words: int
    max_paragraph_sentences: int
    has_simple_intro: bool
    has_keywords: bool
    score: int


@dataclass(frozen=True, slots=True)
class NoviceAudit:
    metrics: ReadabilityMetrics
    issues: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.issues and self.metrics.score >= 80


def _strip_markdown(text: str) -> str:
    value = re.sub(r"(?m)^#{1,6}\s+", "", text)
    value = re.sub(r"(?m)^\s*>\s?", "", value)
    value = re.sub(r"(?m)^\s*(?:[-*]|\d+[.)])\s+", "", value)
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    value = re.sub(r"(?<!\*)\*(.+?)\*(?!\*)", r"\1", value)
    value = re.sub(r"\x60(.+?)\x60", r"\1", value)
    return value


def _sentence_units(text: str) -> list[str]:
    """Return sentence-like reading units while preserving Markdown line boundaries."""
    plain = _strip_markdown(text)
    units: list[str] = []

    # A physical Markdown line is meaningful for readability too: bullets and short
    # explanatory lines must not be silently glued to the following line.
    for raw_line in plain.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        parts = [
            sentence.strip()
            for sentence in re.split(
                r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý0-9])",
                line,
            )
            if sentence.strip()
        ]
        units.extend(parts or [line])
    return units


def _sentence_word_counts(text: str) -> list[int]:
    counts: list[int] = []
    for sentence in _sentence_units(text):
        words = re.findall(r"\b[\wÀ-ÿ'+-]+\b", sentence, flags=re.UNICODE)
        if words:
            counts.append(len(words))
    return counts


def normalize_inline_headings(text: str) -> tuple[str, int]:
    """Put ATX Markdown headings on their own line without rewriting prose."""
    normalized, inline_fixes = re.subn(
        r"(?<=\S)[ \t]+(?=#{2,6}[ \t]+\S)",
        "\n\n",
        text,
    )
    normalized, indent_fixes = re.subn(
        r"(?m)^[ \t]+(?=#{2,6}[ \t]+\S)",
        "",
        normalized,
    )
    return normalized, inline_fixes + indent_fixes


def _paragraph_metrics(text: str) -> tuple[int, int]:
    """Measure prose paragraphs only; headings and lists are separate reading chunks."""
    max_words = 0
    max_sentences = 0

    for block in re.split(r"\n\s*\n", text):
        raw_lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not raw_lines:
            continue

        prose_groups: list[list[str]] = []
        current: list[str] = []

        for line in raw_lines:
            is_boundary = (
                line.startswith(("#", ">"))
                or bool(re.match(r"^(?:[-*]|\d+[.)])\s+", line))
            )
            if is_boundary:
                if current:
                    prose_groups.append(current)
                    current = []
                continue

            current.append(line)

        if current:
            prose_groups.append(current)

        for group in prose_groups:
            plain = _strip_markdown(" ".join(group))
            words = re.findall(r"\b[\wÀ-ÿ'+-]+\b", plain, flags=re.UNICODE)
            sentence_count = len(_sentence_word_counts(plain))
            max_words = max(max_words, len(words))
            max_sentences = max(max_sentences, sentence_count)

    return max_words, max_sentences


def normalize_novice_layout(
    text: str,
    *,
    max_sentences_per_paragraph: int = 5,
    max_words_per_paragraph: int = 120,
) -> tuple[str, int]:
    """Repair layout deterministically using the same boundaries as the clarity gate."""
    normalized, _heading_fixes = normalize_inline_headings(text)
    fixes = 0
    output: list[str] = []
    prose_lines: list[str] = []

    def unit_word_count(unit: str) -> int:
        return len(
            re.findall(r"\b[\wÀ-ÿ'+-]+\b", unit, flags=re.UNICODE)
        )

    def flush_prose() -> None:
        nonlocal fixes, prose_lines
        if not prose_lines:
            return

        original = "\n".join(prose_lines)
        units = _sentence_units(original)
        total_words = sum(unit_word_count(unit) for unit in units)
        if (
            len(units) <= max_sentences_per_paragraph
            and total_words <= max_words_per_paragraph
        ):
            output.extend(prose_lines)
            prose_lines = []
            return

        chunks: list[list[str]] = []
        current: list[str] = []
        current_words = 0

        for unit in units:
            words = unit_word_count(unit)
            would_exceed_sentences = len(current) >= max_sentences_per_paragraph
            would_exceed_words = (
                bool(current)
                and current_words + words > max_words_per_paragraph
            )
            if would_exceed_sentences or would_exceed_words:
                chunks.append(current)
                current = []
                current_words = 0

            current.append(unit)
            current_words += words

        if current:
            chunks.append(current)

        for index, chunk in enumerate(chunks):
            if index:
                output.append("")
            output.append("\n".join(chunk))
        fixes += max(0, len(chunks) - 1)
        prose_lines = []

    for raw_line in normalized.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            flush_prose()
            output.append("")
            continue

        is_boundary = (
            stripped.startswith(("#", ">"))
            or bool(re.match(r"^(?:[-*]|\d+[.)])\s+", stripped))
        )
        if is_boundary:
            flush_prose()
            output.append(raw_line.rstrip())
            continue

        prose_lines.append(raw_line.rstrip())

    flush_prose()

    # Avoid runaway blank lines while preserving paragraph separation.
    result = "\n".join(output)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result, fixes

def readability_metrics(text: str) -> ReadabilityMetrics:
    counts = _sentence_word_counts(text)
    sentence_count = len(counts)
    average = (sum(counts) / sentence_count) if sentence_count else 0.0
    maximum = max(counts, default=0)
    long_ratio = (
        sum(1 for count in counts if count > 30) / sentence_count
        if sentence_count
        else 0.0
    )
    max_paragraph_words, max_paragraph_sentences = _paragraph_metrics(text)

    has_simple_intro = bool(
        re.search(r"(?mi)^###\s+In parole semplici\s*$", text)
    )
    has_keywords = bool(
        re.search(r"(?mi)^###\s+Parole chiave\s*$", text)
    )

    # "Parole chiave" is intentionally optional. Do not make the chapter lose
    # points merely because that section is absent: otherwise a structurally valid,
    # readable chapter can get stuck at 75/100 and trigger pointless model retries.
    score = 0
    score += 30 if has_simple_intro else 0

    if average <= 20:
        score += 30
    elif average <= 24:
        score += 24
    elif average <= 28:
        score += 15
    elif average <= 32:
        score += 6

    if max_paragraph_words <= 100:
        score += 20
    elif max_paragraph_words <= 120:
        score += 14
    elif max_paragraph_words <= 150:
        score += 7

    if max_paragraph_sentences <= 5:
        score += 20
    elif max_paragraph_sentences <= 6:
        score += 10

    return ReadabilityMetrics(
        sentence_count=sentence_count,
        average_sentence_words=average,
        max_sentence_words=maximum,
        long_sentence_ratio=long_ratio,
        max_paragraph_words=max_paragraph_words,
        max_paragraph_sentences=max_paragraph_sentences,
        has_simple_intro=has_simple_intro,
        has_keywords=has_keywords,
        score=min(score, 100),
    )


def _section_body(text: str, heading: str) -> str:
    match = re.search(
        rf"(?mis)^###\s+{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^###\s+|\Z)",
        text,
    )
    return match.group("body").strip() if match else ""


def _keyword_count(text: str) -> int:
    body = _section_body(text, "Parole chiave")
    if not body:
        return 0
    return sum(
        1
        for line in body.splitlines()
        if re.match(r"^\s*(?:[-*]|\d+[.)])\s+", line)
    )


def novice_audit(text: str) -> NoviceAudit:
    metrics = readability_metrics(text)
    issues: list[str] = []

    if not metrics.has_simple_intro:
        issues.append("manca la sezione 'In parole semplici'")
    h3_titles = [
        match.group(1).strip().casefold()
        for match in re.finditer(r"(?m)^###\s+(.+?)\s*$", text)
    ]
    if h3_titles:
        if h3_titles[0] != "in parole semplici":
            issues.append("'In parole semplici' deve essere la prima sottosezione")
        if "parole chiave" in h3_titles and h3_titles.index("parole chiave") != 1:
            issues.append(
                "se presente, 'Parole chiave' deve essere la seconda sottosezione"
            )
        if h3_titles[-1] != "da ricordare per l'esame":
            issues.append("'Da ricordare per l'esame' deve essere l'ultima sottosezione")

    if re.search(r"(?m)^.+[ \t]+###\s+\S+", text):
        issues.append("contiene un heading Markdown incollato alla fine di una frase")

    if metrics.average_sentence_words > 28:
        issues.append(
            "frasi mediamente troppo lunghe per un principiante "
            f"({metrics.average_sentence_words:.1f} parole)"
        )
    if metrics.long_sentence_ratio > 0.25:
        issues.append(
            "troppe frasi superano 30 parole "
            f"({metrics.long_sentence_ratio:.0%})"
        )
    if metrics.max_paragraph_words > 150:
        issues.append(
            "almeno un paragrafo e troppo denso "
            f"({metrics.max_paragraph_words} parole)"
        )
    if metrics.max_paragraph_sentences > 6:
        issues.append(
            "almeno un paragrafo contiene troppe frasi "
            f"({metrics.max_paragraph_sentences})"
        )
    if metrics.score < 80 and not issues:
        issues.append(f"punteggio di chiarezza insufficiente ({metrics.score}/100)")

    return NoviceAudit(metrics=metrics, issues=tuple(issues))


def audit_novice_document(text: str) -> tuple[bool, list[tuple[str, NoviceAudit]]]:
    """Audit front matter and every chapter listed in the deterministic course index."""
    index_match = re.search(
        r"(?ms)^## Indice degli argomenti\s*$\n(?P<items>.*?)(?:\n---\n|\Z)",
        text,
    )
    if not index_match:
        return False, [("documento", novice_audit(text))]

    results: list[tuple[str, NoviceAudit]] = []
    frontmatter = text[:index_match.start()]
    front_issues: list[str] = []
    if "rivedi il capitolo" in frontmatter.casefold():
        front_issues.append("il Ripasso globale contiene placeholder 'rivedi il capitolo'")
    if re.search(r"(?m)^.+\s+###\s+\S+", frontmatter):
        front_issues.append("il front matter contiene heading Markdown inline")
    if "## Ripasso globale" not in frontmatter:
        front_issues.append("manca il Ripasso globale")
    if "## Mappa della materia" not in frontmatter:
        front_issues.append("manca la Mappa della materia")
    raw_front_metrics = readability_metrics(frontmatter)
    front_metrics = ReadabilityMetrics(
        sentence_count=raw_front_metrics.sentence_count,
        average_sentence_words=raw_front_metrics.average_sentence_words,
        max_sentence_words=raw_front_metrics.max_sentence_words,
        long_sentence_ratio=raw_front_metrics.long_sentence_ratio,
        max_paragraph_words=raw_front_metrics.max_paragraph_words,
        max_paragraph_sentences=raw_front_metrics.max_paragraph_sentences,
        has_simple_intro=True,
        has_keywords=True,
        score=100 if not front_issues else 0,
    )

    titles = [
        match.group(1).strip()
        for match in re.finditer(r"(?m)^-\s+(.+?)\s*$", index_match.group("items"))
    ]
    recap_match = re.search(
        r"(?ms)^## Ripasso globale\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
        frontmatter,
    )
    if recap_match:
        recap_items = re.findall(r"(?m)^\d+\.\s+", recap_match.group("body"))
        if len(recap_items) != len(titles):
            front_issues.append(
                "il Ripasso globale non contiene una voce per ogni capitolo "
                f"({len(recap_items)}/{len(titles)})"
            )

    if front_issues and front_metrics.score != 0:
        front_metrics = ReadabilityMetrics(
            sentence_count=front_metrics.sentence_count,
            average_sentence_words=front_metrics.average_sentence_words,
            max_sentence_words=front_metrics.max_sentence_words,
            long_sentence_ratio=front_metrics.long_sentence_ratio,
            max_paragraph_words=front_metrics.max_paragraph_words,
            max_paragraph_sentences=front_metrics.max_paragraph_sentences,
            has_simple_intro=True,
            has_keywords=True,
            score=0,
        )
    results.append(
        (
            "Front matter",
            NoviceAudit(metrics=front_metrics, issues=tuple(front_issues)),
        )
    )

    body = text[index_match.end():]

    for title in titles:
        chapter_match = re.search(
            rf"(?ms)^##\s+{re.escape(title)}\s*$\n(?P<body>.*?)(?=^##\s+|\Z)",
            body,
        )
        if not chapter_match:
            audit = NoviceAudit(
                metrics=readability_metrics(""),
                issues=("capitolo assente dal corpo del documento",),
            )
        else:
            audit = novice_audit(f"## {title}\n{chapter_match.group('body')}")
        results.append((title, audit))

    return all(audit.passed for _, audit in results), results
