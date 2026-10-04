"""Personal-information extraction (name, email, phone, location, links).

Deliberate scope limit: this module extracts only *contact and professional
profile* data. It never infers or stores gender, age, ethnicity, religion,
disability, marital status or a photo. Those attributes are neither needed for
matching nor permissible as ranking signals (PRD section 24). See
``docs/responsible-ai.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.nlp.cleaning import looks_like_contact_line
from app.nlp.skill_taxonomy import normalize_text

__all__ = ["ContactInfo", "extract_contact", "extract_name"]

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
_PHONE = re.compile(
    r"(?<![\w.])(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?\d{3}[\s.-]?\d{3,4}(?:[\s.-]?\d{2,4})?(?![\w.])"
)
_LINKEDIN = re.compile(r"linkedin\.com/[\w\-/%.]+", re.IGNORECASE)
_GITHUB = re.compile(r"github\.com/[\w\-]+", re.IGNORECASE)
# Any bare or scheme-prefixed URL. Matching the whole token (rather than a
# lookbehind-guarded substring) stops "linkedin.com" from being re-detected as
# the truncated host "inkedin.com".
_URL_TOKEN = re.compile(
    r"(?:https?://|www\.)?[\w\-]+(?:\.[\w\-]+)+(?:/[\w\-/?%&=+#.~]*)?",
    re.IGNORECASE,
)
_NON_PORTFOLIO_HOSTS = (
    "linkedin.com", "github.com", "gmail.com", "googlemail.com", "yahoo.com",
    "hotmail.com", "outlook.com", "icloud.com", "proton.me", "protonmail.com",
)
_EMAIL_HOSTS = tuple(host.rsplit(".", 1)[-1] for host in _NON_PORTFOLIO_HOSTS)

# Titles that must never be mistaken for a person's name.
_ROLE_WORDS = {
    "resume", "curriculum vitae", "cv", "engineer", "developer", "scientist",
    "analyst", "manager", "consultant", "designer", "architect", "intern",
    "student", "graduate", "summary", "profile", "contact", "experience",
    "education", "skills", "projects", "certifications", "objective",
}

_NAME_TOKEN = re.compile(r"^[A-Za-z][A-Za-z'\-]{1,29}$")
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md", "mba", "msc", "bs", "bsc"}

# Location = "City, Country" or "City, ST".
_CITY_COUNTRY = re.compile(
    r"\b([A-Z][a-zA-Z.'\-]{2,30}(?:\s+[A-Z][a-zA-Z.'\-]{2,30})?)\s*,\s*"
    r"([A-Z][a-zA-Z.'\-]{2,30}(?:\s+[A-Z][a-zA-Z.'\-]{2,30})?)\b"
)
_COUNTRY_HINT = re.compile(
    r"\b(USA|U\.S\.A\.|United States|Pakistan|India|UK|United Kingdom|Canada|Australia|Germany|France|"
    r"UAE|Saudi Arabia|Singapore|Netherlands|Spain|Italy|Brazil|Mexico|China|Japan|Kenya|Nigeria|"
    r"Egypt|Bangladesh|Ireland|Poland|Portugal|Sweden|Norway|Denmark|Finland|Switzerland|Belgium)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ContactInfo:
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    current_title: str | None = None
    warnings: list[str] = field(default_factory=list)


def _clean_url(url: str) -> str:
    url = url.strip().rstrip(".,;)")
    if not url.startswith("http"):
        url = "https://" + url
    return url[:400]


def extract_email(text: str) -> str | None:
    match = _EMAIL.search(text or "")
    return match.group(0)[:320] if match else None


def extract_phone(text: str) -> str | None:
    """Extract the most plausible phone number.

    Requires 9-15 digits so years, percentages and postal codes are not
    mistaken for phone numbers.
    """
    best: str | None = None
    for match in _PHONE.finditer(text or ""):
        candidate = match.group(0).strip()
        digits = re.sub(r"\D", "", candidate)
        if not 9 <= len(digits) <= 15:
            continue
        if candidate.startswith("19") or candidate.startswith("20"):
            # A bare year.
            continue
        if best is None or len(digits) > len(re.sub(r"\D", "", best)):
            best = candidate
    return best[:60] if best else None


def extract_links(text: str) -> tuple[str | None, str | None, str | None]:
    """Return ``(linkedin, github, portfolio)`` URLs found in ``text``."""
    source = text or ""
    linkedin = _LINKEDIN.search(source)
    github = _GITHUB.search(source)
    linkedin_url = _clean_url(linkedin.group(0)) if linkedin else None
    github_url = _clean_url(github.group(0)) if github else None

    portfolio: str | None = None
    for match in _URL_TOKEN.finditer(source):
        token = match.group(0).strip().rstrip(".,;)")
        if len(token) < len("https://x.io") + 4:
            continue
        host = token.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
        host = host[4:] if host.startswith("www.") else host
        if not host or "/" not in token:
            # A bare domain with no path is usually a mail/host mention.
            continue
        if host in _NON_PORTFOLIO_HOSTS or host.endswith(_EMAIL_HOSTS):
            continue
        portfolio = _clean_url(token)
        break
    return linkedin_url, github_url, portfolio


def _plausible_name(value: str) -> bool:
    tokens = [token for token in re.split(r"[\s.]+", value.strip()) if token]
    if not 1 < len(tokens) <= 5:
        return False
    if len(value) > 60 or len(value) < 4:
        return False
    if normalize_text(value) in _ROLE_WORDS:
        return False
    for token in tokens:
        if not _NAME_TOKEN.match(token):
            return False
        if token.lower() in _ROLE_WORDS:
            return False
    # Reject ALL CAPS banner text and lines that are mostly headings.
    if value.isupper() and len(value) > 40:
        return False
    return True


def extract_name(lines: list[str], email: str | None = None) -> str | None:
    """Infer the candidate's name.

    Strategy, in order of reliability:
    1. The first line of the document when it is a name-shaped string.
    2. A name-shaped string in the first few lines (ignoring contact strips).
    3. A capitalised local part of the email address (``ahmed.khan@``).
    """
    for line in lines[:6]:
        candidate = line.strip()
        if not candidate or looks_like_contact_line(candidate):
            continue
        if looks_like_header_text(candidate):
            continue
        if _plausible_name(candidate):
            return candidate

    # "Name: Ahmed Khan" style.
    for line in lines[:10]:
        match = re.match(r"^(?:name|candidate|full name)\s*[:\-]\s*(.+)$", line.strip(), re.IGNORECASE)
        if match and _plausible_name(match.group(1)):
            return match.group(1).strip()

    if email:
        local = email.split("@", 1)[0]
        local = re.sub(r"\d+", " ", local)
        parts = [part.capitalize() for part in re.split(r"[._\-]+", local) if part]
        candidate = " ".join(parts).strip()
        if 4 <= len(candidate) <= 60 and all(_NAME_TOKEN.match(p) or p.lower() in _SUFFIXES for p in parts):
            return candidate
    return None


def looks_like_header_text(value: str) -> bool:
    """Reject section headings and separators that sit at the top of a resume."""
    stripped = value.strip()
    if not stripped:
        return True
    if len(stripped.split()) > 6:
        return True
    normalized = normalize_text(stripped)
    if normalized in _ROLE_WORDS:
        return True
    if re.fullmatch(r"[\W_]+", stripped):
        return True
    if re.match(r"^(section|page)\b", normalized):
        return True
    # Contact strip such as "Lahore, Pakistan | +92 300 1234567".
    if "|" in stripped or looks_like_contact_line(stripped):
        return True
    if _COUNTRY_HINT.search(stripped) and "," in stripped:
        return True
    return False


def extract_location(lines: list[str]) -> str | None:
    """Best-effort location from the header/contact area.

    Contact bars are extremely common ("Lahore, Pakistan | +92 ... |
    a@b.com"), so each ``|``-separated part is tested before falling back to
    scanning for a bare "City, Country" line.
    """
    head = "\n".join(lines[:12])

    # 1. A location inside a contact bar.
    for line in lines[:12]:
        stripped = line.strip()
        if not stripped or "," not in stripped:
            continue
        parts = [part.strip() for part in re.split(r"\s*[|•·]\s*|\s{3,}", stripped) if part.strip()]
        for part in parts:
            if _plausible_location(part):
                return part[:150]

    # 2. A standalone "City, Country" line.
    for line in lines[:12]:
        stripped = line.strip()
        if not stripped or looks_like_header_text(stripped):
            continue
        if "," in stripped and _plausible_location(stripped):
            return stripped[:150]

    # 3. A bare country mention.
    match = _COUNTRY_HINT.search(head)
    if match:
        city_match = _CITY_COUNTRY.search(head)
        if city_match:
            return f"{city_match.group(1).strip()}, {match.group(1).strip()}"[:150]
        return match.group(1).strip()[:150]
    return None


_LABEL_LINE = re.compile(r"^[A-Za-z][A-Za-z &/]{1,30}\s*:\s*\S")


def _plausible_location(value: str) -> bool:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not 2 <= len(parts) <= 3:
        return False
    if len(value) > 80:
        return False
    # "Languages: Python, Java" is a label line, not an address.
    if _LABEL_LINE.match(value):
        return False
    for part in parts:
        if not part or part.isdigit():
            return False
        if any(ch.isdigit() for ch in part):
            return False
        if len(part.split()) > 3:
            return False
        if not part[0].isupper():
            return False
    return True


_TITLE_PATTERNS = (
    re.compile(r"^(?:current|present)\s+(?:title|role|position)\s*[:\-]\s*(.+)$", re.IGNORECASE),
    re.compile(r"^(?:job\s+)?title\s*[:\-]\s*(.+)$", re.IGNORECASE),
)


_TITLE_SEPARATOR = re.compile(
    r"^(?P<title>[A-Za-z][\w\s\-+#/\.]+?)[ \t]*(?:,|\||@|\bat\b)[ \t]*(?P<company>[A-Z][\w\s\.&-]*)"
)


def extract_current_title(lines: list[str], experience_block: str = "") -> str | None:
    """Find the candidate's most recent job title."""
    for line in lines[:10]:
        for pattern in _TITLE_PATTERNS:
            match = pattern.match(line.strip())
            if match:
                return match.group(1).strip()[:200]

    source = experience_block or "\n".join(lines)
    for line in source.split("\n"):
        stripped = line.strip()
        if not stripped or len(stripped) > 120:
            continue
        # "Web Developer, Acme" / "Engineer | Acme | 2021 - Present" style. Tried
        # before the standalone check below, because such a line also satisfies
        # the title heuristic and would otherwise be stored whole - company and
        # dates included - as the candidate's title.
        separated = _TITLE_SEPARATOR.match(stripped)
        if separated and _looks_like_title(separated.group("title")):
            return separated.group("title").strip()[:200]
        # "Senior Machine Learning Engineer" style standalone line.
        if _looks_like_title(stripped):
            return stripped[:200]
    return None


_TITLE_HINTS = (
    "engineer", "developer", "scientist", "analyst", "manager", "consultant",
    "designer", "architect", "specialist", "lead", "head", "director", "officer",
    "professor", "researcher", "associate", "intern", "administrator", "coordinator",
    "technician", "supervisor", "executive", "founder", "owner", "freelance",
)


def _looks_like_title(value: str) -> bool:
    """Whether a string looks like a job title rather than a person's name.

    A title hint is *required*. Without that requirement "Ahmed Khan" matches
    the permissive character class and gets stored as the candidate's job
    title, which then poisons experience and semantic scoring.

    A company/date separator or a year is also rejected: a role line such as
    "Web Developer, SoftWorks | Mar 2021 - Present" tokenises to six words and
    would otherwise pass as a title.
    """
    if any(marker in value for marker in ("|", "@")):
        return False
    if re.search(r"\b(?:19|20)\d{2}\b", value):
        return False
    tokens = normalize_text(value).split()
    if not 1 <= len(tokens) <= 6:
        return False
    return any(hint in tokens for hint in _TITLE_HINTS)


def extract_contact(lines: list[str], full_text: str, experience_block: str = "") -> ContactInfo:
    """Extract the full contact block in one pass.

    ``experience_block`` is the body of the experience section when known; the
    most recent role title is far more reliable there than in the header, which
    often contains the candidate's name instead.
    """
    head = "\n".join(lines[:15])
    email = extract_email(full_text) or extract_email(head)
    linkedin, github, portfolio = extract_links(full_text)
    location = extract_location(lines)
    name = extract_name(lines, email)
    title = extract_current_title(lines, experience_block)
    phone = extract_phone(head) or extract_phone(full_text[:2000])

    info = ContactInfo(
        full_name=name,
        email=email,
        phone=phone,
        location=location,
        linkedin_url=linkedin,
        github_url=github,
        portfolio_url=portfolio,
        current_title=title,
    )
    if not name:
        info.warnings.append("Candidate name could not be detected; please review the profile.")
    if not email:
        info.warnings.append("No email address found in the resume.")
    return info
