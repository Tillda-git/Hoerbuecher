#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hörbuch-Finder
==============
Durchsucht legale, frei anhörbare Quellen nach deutschsprachigen Hörbüchern,
übernimmt nur VOLLSTÄNDIG verfügbare Titel und zeigt sie nach Kategorie in
einer lokalen Weboberfläche an (mit eingebautem Player).

Quellen
  * LibriVox          – gemeinfreie Hörbücher (offizielle, dokumentierte API)
  * ARD Audiothek     – Hörbücher/Hörspiele der ARD-Sender (inoffizielle GraphQL-API)

Nur Python 3.9+ nötig, keine Zusatzpakete.

  python hoerbuch_finder.py            # Oberfläche starten (crawlt beim ersten Start)
  python hoerbuch_finder.py crawl      # nur crawlen, Ergebnis in hoerbuecher.json
  python hoerbuch_finder.py diagnose   # prüfen, ob die Quellen erreichbar sind
  python hoerbuch_finder.py site       # crawlen und Daten für die iPhone-App (Ordner docs/) schreiben
  python hoerbuch_finder.py podcasts   # Podcasts aus podcasts.txt abgleichen (docs/podcasts.json)
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "hoerbuecher.json"
PORT = 8765
USER_AGENT = "HoerbuchFinder/1.0 (privater Hoerbuch-Katalog)"

# --------------------------------------------------------------------------
# Einstellungen – hier anpassen
# --------------------------------------------------------------------------
CONFIG = {
    "sources": {"librivox": True, "ard": True, "ard_serien": True, "feeds": True,
                "vorleser": True, "ohrka": True},
    # Mindestlänge, damit etwas als "Buch" gilt (Gedichte/Schnipsel fallen raus)
    "min_minutes": 20,
    # ARD: Einteiler (z. B. ein Hörspiel am Stück) müssen mindestens so lang sein
    "ard_min_single_minutes": 30,
    # ARD: Rubriken, deren Name eines dieser Wörter enthält, werden durchsucht
    "ard_category_keywords": ["hörspiel", "hörbuch", "krimi", "literatur",
                              "lesung", "kinder", "märchen", "fiction"],
    # ARD: zusätzliche Suchbegriffe (findet Reihen außerhalb dieser Rubriken)
    "ard_search_terms": ["Hörbuch", "Hörbuch ungekürzt", "Lesung", "liest", "Roman", "Hörspiel",
                         "Hörspiel-Serie", "Krimi-Hörspiel", "Krimi Mehrteiler", "Kinderhörspiel",
                         "Kinderhörbuch", "Märchen", "Mehrteiler", "Science-Fiction-Hörspiel",
                         "Fantasy-Hörspiel", "Abenteuer-Hörspiel", "Klassiker Hörspiel", "Thriller"],
    "ard_search_pages": 10,         # je Suchbegriff, 24 Treffer pro Seite
    # ARD-Hörspielreihen, in denen jede Folge ein abgeschlossenes Stück ist (Suchbegriff → Kategorie)
    "ard_episode_series": {"Kein Mucks": "Krimi", "Radio Tatort": "Krimi", "Auf der Spur": "Krimi",
                           "Knallhart": "Thriller Krimi", "Das Portal": "Science-Fiction",
                           "Große Geschichten": "Literatur", "Hörspiel-Speicher": "",
                           "Nothing Serious": "Comedy", "ARD Kinderhörspiel": "Kinder"},
    # Podcast-Feeds (jede Folge bzw. jeder vollständige Mehrteiler wird ein Titel)
    "feeds": [
        {"name": "Hörspiel", "source": "Deutschlandfunk", "category": "",
         "url": "https://www.hoerspielundfeature.de/hoerspiel-122.xml"},
        {"name": "Kriminalhörspiel", "source": "Deutschlandfunk", "category": "Krimi",
         "url": "https://www.hoerspielundfeature.de/krimi-hoerspiel-100.xml"},
        {"name": "Professor van Dusen", "source": "Deutschlandfunk", "category": "Krimi",
         "url": "https://www.deutschlandfunk.de/professor-van-dusen-104.xml"},
        {"name": "Kakadu Kinderhörspiel", "source": "Deutschlandfunk", "category": "Kinder",
         "url": "https://www.kakadu.de/kinderhoerspiel-118.xml"},
    ],
    # Nur verlinkt (deren Bedingungen erlauben keine öffentliche Wiedergabe im Internet)
    "ohrka_categories": ["abenteuerlich-lustig", "tierisch-spannend", "maerchen-fuer-alle"],
    "ard_max_programsets": 2500,    # Obergrenze zu prüfender Sendereihen
    "workers": 4,                   # parallele Anfragen an die ARD (höflich bleiben)
}

# --------------------------------------------------------------------------
# Kategorien (einheitlich über alle Quellen)
# --------------------------------------------------------------------------
CATEGORY_RULES = [
    ("Kinder & Jugend", r"child|kinder|jugend|young adult|kika|für die ganze familie"),
    ("Märchen, Sagen & Mythen", r"fairy|märchen|myth|legend|sagen\b|fable|fabel"),
    ("Krimi & Thriller", r"crime|mystery|detective|krimi|thriller|kommissar|detektiv|sherlock"),
    ("Fantastik, Sci-Fi & Grusel", r"science.?fiction|sci-?fi|fantas|horror|gothic|ghost|supernatural|"
                                  r"grusel|gespenst|vampir|utopi|dystop"),
    ("Abenteuer", r"adventure|nautical|western|war & military|abenteuer|seefahrt|piraten"),
    ("Lyrik & Balladen", r"poetry|poem|lyrik|gedicht|ballade"),
    ("Drama & Theater", r"drama|plays?\b|theater|theatre|tragödie|komödie"),
    ("Humor & Satire", r"humor|humour|satire|comedy|satir|humoresk"),
    ("Liebe & Romantik", r"romance|romantic|liebesroman"),
    ("Historische Romane", r"historical fiction|historischer roman"),
    ("Biografien, Briefe & Erinnerungen", r"biograph|memoir|letters|autobiograph|briefe|tagebuch|diary"),
    ("Philosophie & Religion", r"philosoph|religio|bibel|bible|theolog|spiritual"),
    ("Sachbuch & Wissen", r"non-?fiction|\bscience\b|nature|travel|history|essay|politic|econom|"
                          r"wissen|geschichte|reise|sachbuch"),
    ("Erzählungen & Kurzgeschichten", r"short (stor|work)|erzählung|novelle|kurzgeschicht|anthology"),
    ("Romane & Literatur", r"fiction|literar|literatur|\broman\b|novel|klassiker|general"),
]
# Für Fließtext (Titel/Beschreibung) nur eindeutige Genres, sonst zu viele Fehltreffer
STRONG_TEXT_CATEGORIES = {"Kinder & Jugend", "Märchen, Sagen & Mythen", "Krimi & Thriller",
                          "Fantastik, Sci-Fi & Grusel"}
DEFAULT_CATEGORY = "Romane & Literatur"
_COMPILED_RULES = [(name, re.compile(rx, re.I)) for name, rx in CATEGORY_RULES]


def classify(primary: str, secondary: str = "") -> str:
    for name, rx in _COMPILED_RULES:
        if primary and rx.search(primary):
            return name
    for name, rx in _COMPILED_RULES:
        if name in STRONG_TEXT_CATEGORIES and secondary and rx.search(secondary):
            return name
    return DEFAULT_CATEGORY


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------
STATUS = {"running": False, "log": [], "started": None, "finished": None}
_status_lock = threading.Lock()


def log(msg: str) -> None:
    line = f"{datetime.now():%H:%M:%S}  {msg}"
    print(line, flush=True)
    with _status_lock:
        STATUS["log"].append(line)
        STATUS["log"] = STATUS["log"][-300:]


def http_json(url: str, data: bytes | None = None, headers: dict | None = None,
              timeout: int = 60, retries: int = 3):
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers={
            "User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:                       # GraphQL liefert Fehler oft als 400 mit JSON
                parsed = json.loads(body)
                if isinstance(parsed, dict) and "errors" in parsed:
                    return parsed
            except json.JSONDecodeError:
                pass
            last_err = f"HTTP {e.code}"
            if e.code in (400, 401, 403, 404):
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            last_err = str(e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url[:90]} → {last_err}")


def http_text(url: str, timeout: int = 60, retries: int = 3) -> str:
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            if e.code in (400, 401, 403, 404, 410):
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = str(e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url[:90]} → {last_err}")


def clean_text(s: str | None, limit: int = 600) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(re.sub(r"\s+", " ", s)).strip()
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + " …"


def to_int(v, default=0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def part_number(title: str) -> int | None:
    m = re.search(r"\((\d+)\s*/\s*\d+\)|(?:teil|folge|kapitel|episode)\s*(\d+)|^(\d+)[.:)]", title or "", re.I)
    if m:
        return int(next(g for g in m.groups() if g))
    return None


# --------------------------------------------------------------------------
# Quelle 1: LibriVox
# --------------------------------------------------------------------------
LIBRIVOX_API = "https://librivox.org/api/feed/audiobooks/"


def librivox_to_book(b: dict) -> tuple[dict | None, str]:
    """Wandelt einen LibriVox-Datensatz um; liefert (Buch, Ablehnungsgrund)."""
    if (b.get("language") or "").strip().lower() not in ("german", "deutsch"):
        return None, "andere Sprache"
    secs = to_int(b.get("totaltimesecs"))
    sections = to_int(b.get("num_sections"))
    iarchive = (b.get("url_iarchive") or "").strip()
    # Vollständig = katalogisiert, alle Abschnitte gezählt und als Gesamtpaket veröffentlicht
    if sections < 1 or not (iarchive or b.get("url_zip_file")):
        return None, "nicht fertig veröffentlicht"
    if secs < CONFIG["min_minutes"] * 60:
        return None, "zu kurz"
    authors = ", ".join(" ".join(filter(None, [a.get("first_name"), a.get("last_name")])).strip()
                        for a in (b.get("authors") or []))
    genres = [g.get("name", "") for g in (b.get("genres") or []) if isinstance(g, dict)]
    ident = iarchive.rstrip("/").rsplit("/", 1)[-1] if iarchive else ""
    title = html.unescape(b.get("title") or "").strip()
    return {
        "id": f"librivox:{b.get('id')}",
        "source": "LibriVox",
        "title": title,
        "author": authors,
        "category": classify(" ".join(genres), title),
        "genres": genres,
        "form": "Lesung",
        "duration": secs,
        "parts": sections,
        "description": clean_text(b.get("description")),
        "url": b.get("url_librivox") or iarchive,
        "cover": f"https://archive.org/services/img/{ident}" if ident else "",
        "links": {k: v for k, v in {"ZIP (alle MP3)": b.get("url_zip_file"),
                                    "Podcast-Feed": b.get("url_rss"),
                                    "Internet Archive": iarchive}.items() if v},
        "archive_id": ident,
        "complete_reason": f"{sections} Abschnitte, als Gesamtwerk veröffentlicht",
    }, ""


def crawl_librivox(rejected: dict) -> list[dict]:
    try:
        return crawl_librivox_api(rejected)
    except RuntimeError as e:
        log(f"LibriVox-API nicht nutzbar ({e}) – frage den LibriVox-Bestand im Internet Archive ab …")
        return crawl_librivox_archive(rejected)


def crawl_librivox_api(rejected: dict) -> list[dict]:
    books, offset = [], 0
    limit = 1000
    log("LibriVox: lade Gesamtkatalog (die API filtert nicht nach Sprache) …")
    while True:
        url = f"{LIBRIVOX_API}?format=json&limit={limit}&offset={offset}"
        try:
            data = http_json(url, timeout=180)
        except RuntimeError:
            if offset == 0 and limit > 100:
                limit = 100                # manche Server lehnen große Seiten ab
                continue
            if offset == 0:
                raise
            break
        items = data.get("books") if isinstance(data, dict) else None
        if not items:
            if offset == 0:
                raise RuntimeError("leere Antwort")
            break
        for b in items:
            book, reason = librivox_to_book(b)
            if book:
                books.append(book)
            elif reason != "andere Sprache":
                rejected[f"LibriVox: {reason}"] = rejected.get(f"LibriVox: {reason}", 0) + 1
        if (offset // limit) % 10 == 0 or len(items) < limit:
            log(f"LibriVox: {offset + len(items)} Katalogeinträge geprüft, {len(books)} deutsche Hörbücher")
        if len(items) < limit:
            break
        offset += limit
        time.sleep(0.5)
    return books


def parse_runtime(v) -> int:
    """'5:23:11', '83:10', '323 min' oder Sekunden → Sekunden."""
    if v is None:
        return 0
    v = str(v[0] if isinstance(v, list) else v).strip()
    if ":" in v:
        parts = [to_int(x) for x in v.split(":")]
        secs = 0
        for x in parts:
            secs = secs * 60 + x
        return secs
    m = re.match(r"([\d.]+)\s*min", v, re.I)
    return int(float(m.group(1)) * 60) if m else to_int(v)


def crawl_librivox_archive(rejected: dict) -> list[dict]:
    """LibriVox-Aufnahmen über das Internet Archive (dort liegen nur fertige Projekte)."""
    q = urllib.parse.quote("collection:librivoxaudio AND language:(German OR german OR deu OR ger OR Deutsch)")
    fl = "".join(f"&fl[]={f}" for f in ("identifier", "title", "creator", "subject", "runtime", "description"))
    books, page = [], 1
    while True:
        data = http_json(f"https://archive.org/advancedsearch.php?q={q}{fl}&rows=1000&page={page}&output=json",
                         timeout=120)
        docs = (data.get("response") or {}).get("docs") or []
        for d in docs:
            ident = d.get("identifier")
            secs = parse_runtime(d.get("runtime"))
            if not ident:
                continue
            if secs and secs < CONFIG["min_minutes"] * 60:
                rejected["LibriVox: zu kurz"] = rejected.get("LibriVox: zu kurz", 0) + 1
                continue
            title = re.sub(r"\s*\(?librivox\)?\s*$", "", html.unescape(str(d.get("title") or "")), flags=re.I).strip()
            subj = d.get("subject") or []
            subj = " ".join(subj) if isinstance(subj, list) else str(subj)
            creator = d.get("creator") or []
            names = creator if isinstance(creator, list) else re.split(r"\s*;\s*", str(creator))
            # "Kafka, Franz" → "Franz Kafka"
            creator = ", ".join(" ".join(reversed(n.split(",", 1))).strip() if n.count(",") == 1 else n
                                for n in names if n)
            desc = d.get("description") or ""
            desc = " ".join(desc) if isinstance(desc, list) else str(desc)
            books.append({
                "id": f"librivox:ia-{ident}", "source": "LibriVox", "title": title, "author": creator,
                "category": classify(subj, f"{title} {desc[:300]}"), "genres": [], "form": "Lesung",
                "duration": secs, "parts": 0, "description": clean_text(desc),
                "url": f"https://archive.org/details/{ident}", "cover": f"https://archive.org/services/img/{ident}",
                "links": {"Internet Archive": f"https://archive.org/details/{ident}"}, "archive_id": ident,
                "complete_reason": "abgeschlossenes LibriVox-Projekt (Internet Archive)",
            })
        log(f"LibriVox (Internet Archive): Seite {page}, {len(books)} deutsche Hörbücher")
        if len(docs) < 1000:
            break
        page += 1
        time.sleep(0.5)
    if not books:
        raise RuntimeError("Internet Archive lieferte keine deutschen LibriVox-Titel")
    return books


def librivox_tracks(book_id: str, archive_id: str = "") -> list[dict]:
    pid = book_id.split(":", 1)[1]
    if pid.startswith("ia-"):
        archive_id = archive_id or pid[3:]
        pid = ""
    try:
        if not pid:
            raise RuntimeError("nur Internet Archive")
        data = http_json(f"https://librivox.org/api/feed/audiotracks/?project_id={pid}&format=json", timeout=40)
        secs = data.get("sections") or []
        tracks = [{"title": clean_text(s.get("title"), 200) or f"Teil {s.get('section_number')}",
                   "url": s.get("listen_url"), "duration": to_int(s.get("playtime")),
                   "n": to_int(s.get("section_number"))} for s in secs if s.get("listen_url")]
        if tracks:
            return sorted(tracks, key=lambda t: t["n"])
    except RuntimeError:
        pass
    if archive_id:                     # Rückfall: Dateiliste beim Internet Archive
        meta = http_json(f"https://archive.org/metadata/{urllib.parse.quote(archive_id)}", timeout=40)
        files = [f for f in meta.get("files", []) if f.get("format") in ("64Kbps MP3", "VBR MP3")]
        small = [f for f in files if f.get("format") == "64Kbps MP3"]
        return [{"title": f.get("title") or f.get("name"), "duration": parse_runtime(f.get("length")),
                 "url": f"https://archive.org/download/{archive_id}/{urllib.parse.quote(f['name'])}"}
                for f in sorted(small or files, key=lambda f: f.get("name", ""))]
    return []


# --------------------------------------------------------------------------
# Quelle 2: ARD Audiothek
# --------------------------------------------------------------------------
ARD_GQL = "https://api.ardaudiothek.de/graphql"
_ard_warned: set[str] = set()


def gql(query: str) -> dict:
    data = http_json(ARD_GQL, data=json.dumps({"query": query}).encode("utf-8"),
                     headers={"Content-Type": "application/json"}, timeout=60)
    errs = data.get("errors") or []
    if errs and not data.get("data"):
        raise RuntimeError("GraphQL: " + "; ".join(e.get("message", "?") for e in errs)[:300])
    if errs:
        msg = errs[0].get("message", "?")[:160]
        if msg not in _ard_warned:
            _ard_warned.add(msg)
            log(f"ARD: Hinweis der API (nicht kritisch): {msg}")
    return data.get("data") or {}


def q(s: str) -> str:
    """String sicher als GraphQL-Literal einsetzen."""
    return json.dumps(str(s), ensure_ascii=False)


PS_FIELDS = "id title showType numberOfElements"


def ard_candidates() -> dict[str, str]:
    """Sammelt IDs von Sendereihen; Wert = Rubrik (falls bekannt)."""
    cand: dict[str, str] = {}
    kws = [k.lower() for k in CONFIG["ard_category_keywords"]]
    ok_calls = 0

    def add(nodes, cat=""):
        for n in nodes or []:
            if not n or not n.get("id"):
                continue
            if n.get("showType") in ("INFINITE_SERIES", "SEASON_SERIES"):
                continue               # fortlaufende Podcasts sind keine Bücher
            cand.setdefault(str(n["id"]), cat)
            if cat and not cand[str(n["id"])]:
                cand[str(n["id"])] = cat

    # a) Rubriken
    try:
        cats = gql("{ editorialCategories(first: 300) { nodes { id title } } }")
        cats = (cats.get("editorialCategories") or {}).get("nodes") or []
        ok_calls += 1
        wanted = [c for c in cats if any(k in (c.get("title") or "").lower() for k in kws)]
        log(f"ARD: {len(cats)} Rubriken, davon relevant: {', '.join(c['title'] for c in wanted) or '–'}")
        for c in wanted:
            offset = 0
            while True:
                d = gql(f"""{{ editorialCategories(filter: {{id: {{equalTo: {q(c['id'])}}}}}) {{
                    nodes {{ programSets(first: 100, offset: {offset}) {{ totalCount nodes {{ {PS_FIELDS} }} }} }} }} }}""")
                nodes = ((d.get("editorialCategories") or {}).get("nodes") or [{}])
                ps = (nodes[0] or {}).get("programSets") or {}
                add(ps.get("nodes"), c["title"])
                offset += 100
                if not ps.get("nodes") or offset >= to_int(ps.get("totalCount")) or offset > 3000:
                    break
                time.sleep(0.2)
    except RuntimeError as e:
        log(f"ARD: Rubriken-Abfrage fehlgeschlagen ({e}) – nutze nur die Suche")

    # b) Suche
    for term in CONFIG["ard_search_terms"]:
        found_before = len(cand)
        for page in range(CONFIG["ard_search_pages"]):
            try:
                d = gql(f"""{{ search(query: {q(term)}, type: ProgramSets, offset: {page * 24}, limit: 24) {{
                            programSets {{ nodes {{ {PS_FIELDS} }} }} }} }}""")
            except RuntimeError:
                try:            # ältere/neuere API ohne type-Parameter
                    d = gql(f"""{{ search(query: {q(term)}, offset: {page * 24}, limit: 24) {{
                                programSets {{ nodes {{ {PS_FIELDS} }} }} }} }}""")
                except RuntimeError as e:
                    log(f"ARD: Suche '{term}' fehlgeschlagen: {e}")
                    break
            nodes = ((d.get("search") or {}).get("programSets") or {}).get("nodes") or []
            ok_calls += 1
            add(nodes)
            if len(nodes) < 24:
                break
            time.sleep(0.2)
        log(f"ARD: Suche '{term}': +{len(cand) - found_before} Reihen (gesamt {len(cand)})")
    if ok_calls == 0:
        raise RuntimeError("ARD-API nicht erreichbar oder Schnittstelle geändert")
    return cand


# Mehrere Abfragevarianten: falls die API ein Feld nicht (mehr) kennt, wird die nächste versucht
PS_DETAIL_VARIANTS = [
    """id title synopsis numberOfElements showType sharingUrl
       image {{ url }} publicationService {{ title }} editorialCategory {{ title }}
       items(first: 400, filter: {{isPublished: {{equalTo: true}}}}) {{
         nodes {{ id title episodeNumber maxGroupNumber duration audios {{ url downloadUrl }} }} }}""",
    """id title synopsis numberOfElements showType sharingUrl
       items(first: 400, filter: {{isPublished: {{equalTo: true}}}}) {{
         nodes {{ id title episodeNumber duration audios {{ url downloadUrl }} }} }}""",
    """id title synopsis numberOfElements showType
       items(first: 400) {{ nodes {{ id title duration audios {{ url }} }} }}""",
]
_variant_idx = 0


def ard_programset(ps_id: str) -> dict | None:
    global _variant_idx
    last_err = "keine passende Abfragevariante"
    for i in range(_variant_idx, len(PS_DETAIL_VARIANTS)):
        fields = PS_DETAIL_VARIANTS[i].replace("{{", "{").replace("}}", "}")
        try:
            d = gql(f"{{ programSet(id: {q(ps_id)}) {{ {fields} }} }}")
            if i != _variant_idx:
                _variant_idx = i
                log(f"ARD: verwende vereinfachte Abfrage (Variante {i + 1})")
            return d.get("programSet")
        except RuntimeError as e:
            if "GraphQL" not in str(e):
                raise                  # Netzfehler: nicht auf andere Variante ausweichen
            last_err = str(e)
    raise RuntimeError(f"Reihe {ps_id}: {last_err}")


def ard_is_complete(ps: dict, items: list[dict]) -> tuple[bool, str]:
    """Prüft, ob eine ARD-Reihe vollständig anhörbar ist."""
    show = ps.get("showType")
    if show in ("INFINITE_SERIES", "SEASON_SERIES"):
        return False, "fortlaufende Reihe/Podcast"
    playable = [i for i in items if any((a or {}).get("url") or (a or {}).get("downloadUrl")
                                        for a in (i.get("audios") or []))]
    if not playable:
        return False, "keine Audiodateien"
    total = sum(to_int(i.get("duration")) for i in playable)
    expected = to_int(ps.get("numberOfElements"))
    if show == "SINGLE" or (len(playable) == 1 and expected <= 1):
        if total < CONFIG["ard_min_single_minutes"] * 60:
            return False, "zu kurz"
        return True, "Einteiler, vollständig verfügbar"
    if expected and len(playable) < expected:
        return False, f"nur {len(playable)} von {expected} Teilen verfügbar"
    nums = sorted({to_int(i.get("episodeNumber")) for i in playable if to_int(i.get("episodeNumber")) > 0})
    if not nums:
        nums = sorted({n for n in (part_number(i.get("title", "")) for i in playable) if n})
    if nums:
        maxg = max([to_int(i.get("maxGroupNumber")) for i in playable] + [0])
        last = max(maxg, nums[-1])
        missing = sorted(set(range(1, last + 1)) - set(nums))
        if missing:
            return False, f"Teile fehlen ({', '.join(map(str, missing[:5]))}{' …' if len(missing) > 5 else ''})"
    elif not expected and show is None:
        return False, "Vollständigkeit nicht prüfbar"
    if total < CONFIG["min_minutes"] * 60:
        return False, "zu kurz"
    return True, f"alle {len(playable)} Teile verfügbar"


LESUNG_RX = re.compile(r"\bliest\b|lesung|gelesen|hörbuch|ungekürzt|vorgelesen", re.I)
NOT_AUTHOR_RX = re.compile(r"hörspiel|krimi|lesung|hörbuch|podcast|serie|folge|teil|staffel|radio|ard|wdr|ndr|"
                           r"swr|br\b|mdr|hr\b|rbb|sr\b|dlf|kinder", re.I)


def split_author(title: str) -> tuple[str, str]:
    if ":" in title:
        a, t = title.split(":", 1)
        if 2 < len(a) <= 40 and len(a.split()) <= 5 and not NOT_AUTHOR_RX.search(a):
            return a.strip(), t.strip()
    return "", title.strip()


GENERIC_ARD_CATS = {"hörspiel", "literatur", "hörspiel & hörbuch", "hörbuch", ""}


def ard_to_book(ps: dict, cat_hint: str) -> tuple[dict | None, str]:
    items = ((ps.get("items") or {}).get("nodes")) or []
    ok, reason = ard_is_complete(ps, items)
    if not ok:
        return None, reason
    playable = [i for i in items if i.get("audios")]

    def sort_key(i):
        n = to_int(i.get("episodeNumber")) or part_number(i.get("title", "")) or 9999
        return (n, i.get("title", ""))
    playable.sort(key=sort_key)
    tracks = []
    for i in playable:
        a = next((x for x in i["audios"] if x and (x.get("downloadUrl") or x.get("url"))), {})
        tracks.append({"title": i.get("title") or "", "url": a.get("downloadUrl") or a.get("url"),
                       "duration": to_int(i.get("duration"))})
    raw_title = (ps.get("title") or "").strip()
    author, title = split_author(raw_title)
    cat_title = cat_hint or ((ps.get("editorialCategory") or {}).get("title") or "")
    synopsis = clean_text(ps.get("synopsis"))
    text = f"{raw_title} {synopsis}"
    form = "Lesung" if LESUNG_RX.search(text) else "Hörspiel"
    img = ((ps.get("image") or {}).get("url") or "").replace("{width}", "400")
    station = (ps.get("publicationService") or {}).get("title") or ""
    return {
        "id": f"ard:{ps.get('id')}",
        "source": "ARD Audiothek",
        "station": station,
        "title": title,
        "author": author,
        "category": classify(raw_title if cat_title.lower() in GENERIC_ARD_CATS
                             else f"{cat_title} {raw_title}", synopsis),
        "ard_category": cat_title,
        "form": form,
        "duration": sum(t["duration"] for t in tracks),
        "parts": len(tracks),
        "description": synopsis,
        "url": ps.get("sharingUrl") or f"https://www.ardaudiothek.de/sendung/x/{ps.get('id')}/",
        "cover": img,
        "links": {},
        "tracks": tracks,
        "complete_reason": reason,
    }, ""


def crawl_ard(rejected: dict) -> list[dict]:
    log("ARD Audiothek: sammle Sendereihen …")
    cand = ard_candidates()
    ids = list(cand)[: CONFIG["ard_max_programsets"]]
    log(f"ARD: prüfe {len(ids)} Reihen auf Vollständigkeit …")
    books, done = [], 0
    with ThreadPoolExecutor(max_workers=CONFIG["workers"]) as pool:
        futs = {pool.submit(ard_programset, i): i for i in ids}
        for f in as_completed(futs):
            done += 1
            try:
                ps = f.result()
            except RuntimeError as e:
                rejected["ARD: Abruf fehlgeschlagen"] = rejected.get("ARD: Abruf fehlgeschlagen", 0) + 1
                if rejected["ARD: Abruf fehlgeschlagen"] <= 3:
                    log(f"ARD: {e}")
                continue
            if ps:
                book, reason = ard_to_book(ps, cand[futs[f]])
                if book:
                    books.append(book)
                else:
                    key = "ARD: " + re.sub(r"\(.*\)|\d+", "#", reason)
                    rejected[key] = rejected.get(key, 0) + 1
            if done % 100 == 0:
                log(f"ARD: {done}/{len(ids)} geprüft, {len(books)} vollständig")
    failed = rejected.get("ARD: Abruf fehlgeschlagen", 0)
    if ids and failed >= len(ids) * 0.8:
        raise RuntimeError(f"{failed} von {len(ids)} Reihen konnten nicht abgerufen werden")
    log(f"ARD: fertig – {len(books)} vollständige Titel")
    return books


# --------------------------------------------------------------------------
# Crawl-Steuerung & Speicherung
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Folgen-basierte Quellen (Podcast-Feeds, ARD-Hörspielreihen)
# --------------------------------------------------------------------------
import hashlib
import xml.etree.ElementTree as ET

PART_RX = re.compile(r"[\(\[]\s*(\d{1,2})\s*/\s*(\d{1,2})\s*[\)\]]|\b(?:Teil|Folge)\s+(\d{1,2})\s*(?:von|/)\s*(\d{1,2})\b", re.I)
PREFIX_RX = re.compile(r"^\s*(?:Krimi-?Hörspiel|Kriminalhörspiel|Hörspiel|Kinderhörspiel|Hörspiel-Serie)\s*:\s*", re.I)
AUTHOR_RX = re.compile(r"\bVon\s+([A-ZÄÖÜ][^\n•]{2,70}?)\s*(?:www\.|\||$)")
GENRE_RX = re.compile(r"•\s*([^•]{2,40}?)\s*•")


def split_part(title: str) -> tuple[str, str, int | None, int | None]:
    """→ (Gruppenschlüssel, Anzeigetitel, Teil, Teile gesamt)."""
    t = PREFIX_RX.sub("", title or "").strip()
    m = PART_RX.search(t)
    if not m:
        return norm_key(t), t, None, None
    nums = [int(g) for g in m.groups() if g]
    head, tail = t[:m.start()].strip(" -–:,"), t[m.end():].strip(" -–:,")
    shown = head or tail or t
    return norm_key(shown), shown, nums[0], nums[1]


def norm_key(t: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]+", " ", (t or "").lower()).strip()


def stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}:" + hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def episodes_to_books(eps: list[dict], *, source: str, station: str, cat_hint: str, id_prefix: str,
                      rejected: dict, min_single: int | None = None) -> list[dict]:
    """Einzelfolgen → Titel; Mehrteiler (1/3, 2/3 …) nur, wenn alle Teile vorhanden sind."""
    min_single = (min_single or CONFIG["min_minutes"]) * 60
    singles, groups = [], {}
    for e in eps:
        if not e.get("url"):
            continue
        key, shown, n, total = split_part(e.get("title", ""))
        if n is None:
            singles.append((key, shown, [e]))
        else:
            g = groups.setdefault(key, {"title": shown, "parts": {}, "total": 0})
            g["parts"].setdefault(n, e)
            g["total"] = max(g["total"], total or 0, n)
    books, label = [], f"{source}: "
    for key, shown, parts in singles:
        if to_int(parts[0].get("duration")) < min_single:
            rejected[label + "zu kurz"] = rejected.get(label + "zu kurz", 0) + 1
            continue
        books.append(_episode_book(key, shown, parts, source, station, cat_hint, id_prefix, "Einteiler"))
    for key, g in groups.items():
        missing = sorted(set(range(1, g["total"] + 1)) - set(g["parts"]))
        if missing:
            rejected[label + "Teile fehlen"] = rejected.get(label + "Teile fehlen", 0) + 1
            continue
        parts = [g["parts"][i] for i in range(1, g["total"] + 1)]
        books.append(_episode_book(key, g["title"], parts, source, station, cat_hint, id_prefix,
                                   f"alle {len(parts)} Teile verfügbar"))
    return books


def _episode_book(key, title, parts, source, station, cat_hint, id_prefix, reason) -> dict:
    first = parts[0]
    desc = clean_text(first.get("description"), 2000)
    genres = " ".join(GENRE_RX.findall(desc))
    m = AUTHOR_RX.search(desc)
    author = m.group(1).strip(" ,.") if m else ""
    if not author:
        a, t2 = split_author(title)
        author, title = (a, t2) if a else ("", title)
    text = f"{title} {desc}"
    form = "Lesung" if LESUNG_RX.search(text) else "Hörspiel"
    tracks = [{"title": p.get("title") or f"Teil {i + 1}", "url": p.get("url"),
               "duration": to_int(p.get("duration"))} for i, p in enumerate(parts)]
    return {
        "id": stable_id(id_prefix, station, key or first.get("url", "")),
        "source": source, "station": station, "title": title, "author": author,
        "category": classify(f"{genres} {cat_hint} {title}", desc), "form": form,
        "duration": sum(t["duration"] for t in tracks), "parts": len(tracks),
        "description": clean_text(GENRE_RX.sub("", desc)), "url": first.get("link") or "",
        "cover": first.get("image") or "", "links": {}, "tracks": tracks, "complete_reason": reason,
    }


ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


def parse_feed(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text.encode("utf-8"))
    ch = root.find("channel")
    if ch is None:
        return []
    ch_img = (ch.find(f"{ITUNES}image").get("href") if ch.find(f"{ITUNES}image") is not None
              else (ch.findtext("image/url") or ""))
    eps = []
    for it in ch.findall("item"):
        enc = it.find("enclosure")
        img = it.find(f"{ITUNES}image")
        eps.append({
            "title": html.unescape((it.findtext("title") or "").strip()),
            "url": enc.get("url") if enc is not None else "",
            "duration": parse_runtime(it.findtext(f"{ITUNES}duration")),
            "description": it.findtext("description") or it.findtext(f"{ITUNES}summary") or "",
            "link": it.findtext("link") or "",
            "image": (img.get("href") if img is not None else "") or ch_img,
            "guid": (it.findtext("guid") or "").strip(),
            "date": feed_date(it.findtext("pubDate")),
        })
    return eps


def feed_date(v: str | None) -> str:
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(v).isoformat(timespec="minutes") if v else ""
    except (TypeError, ValueError):
        return ""


def feed_channel(xml_text: str) -> dict:
    ch = ET.fromstring(xml_text.encode("utf-8")).find("channel")
    if ch is None:
        return {}
    img = ch.find(f"{ITUNES}image")
    return {"name": html.unescape((ch.findtext("title") or "").strip()),
            "author": (ch.findtext(f"{ITUNES}author") or "").strip(),
            "img": (img.get("href") if img is not None else "") or (ch.findtext("image/url") or ""),
            "desc": clean_text(ch.findtext("description") or ch.findtext(f"{ITUNES}summary"), 400)}


def crawl_feeds(rejected: dict) -> list[dict]:
    books, ok = [], 0
    for f in CONFIG["feeds"]:
        try:
            eps = parse_feed(http_text(f["url"]))
            ok += 1
        except Exception as e:
            log(f"Feed '{f['name']}': nicht lesbar ({e})")
            continue
        found = episodes_to_books(eps, source=f["source"], station=f["name"], cat_hint=f.get("category", ""),
                                  id_prefix="feed", rejected=rejected, min_single=f.get("min"))
        log(f"Feed '{f['name']}': {len(eps)} Folgen → {len(found)} vollständige Titel")
        books.extend(found)
        time.sleep(0.5)
    if not ok:
        raise RuntimeError("kein Feed erreichbar")
    return books


SERIES_VARIANTS = [
    "id title sharingUrl image {{ url }} items(first: 400, filter: {{isPublished: {{equalTo: true}}}}) "
    "{{ nodes {{ id title synopsis duration sharingUrl audios {{ url downloadUrl }} }} }}",
    "id title items(first: 400) {{ nodes {{ id title duration audios {{ url downloadUrl }} }} }}",
]


def crawl_ard_series(rejected: dict) -> list[dict]:
    books, ok = [], 0
    for term, cat in CONFIG["ard_episode_series"].items():
        try:
            d = gql(f"""{{ search(query: {q(term)}, limit: 10) {{ programSets {{ nodes {{ id title }} }} }} }}""")
            ok += 1
        except RuntimeError as e:
            log(f"ARD-Reihe '{term}': Suche fehlgeschlagen ({e})")
            continue
        nodes = ((d.get("search") or {}).get("programSets") or {}).get("nodes") or []
        words = norm_key(term).split()
        ps_node = next((n for n in nodes if all(w in norm_key(n.get("title", "")) for w in words)), None)
        if not ps_node:
            log(f"ARD-Reihe '{term}': nicht gefunden")
            continue
        ps = None
        for v in SERIES_VARIANTS:
            try:
                ps = gql(f"{{ programSet(id: {q(ps_node['id'])}) {{ {v.replace('{{', '{').replace('}}', '}')} }} }}").get("programSet")
                break
            except RuntimeError:
                continue
        if not ps:
            continue
        img = ((ps.get("image") or {}).get("url") or "").replace("{width}", "400")
        eps = []
        for it in ((ps.get("items") or {}).get("nodes") or []):
            a = next((x for x in (it.get("audios") or []) if x and (x.get("downloadUrl") or x.get("url"))), None)
            eps.append({"title": it.get("title") or "", "url": (a or {}).get("downloadUrl") or (a or {}).get("url"),
                        "duration": to_int(it.get("duration")), "description": it.get("synopsis") or "",
                        "link": it.get("sharingUrl") or ps.get("sharingUrl") or "", "image": img})
        found = episodes_to_books(eps, source="ARD Audiothek", station=ps.get("title") or term, cat_hint=cat,
                                  id_prefix="ardserie", rejected=rejected, min_single=CONFIG["ard_min_single_minutes"])
        log(f"ARD-Reihe '{ps.get('title')}': {len(eps)} Folgen → {len(found)} vollständige Titel")
        books.extend(found)
        time.sleep(0.3)
    if not ok:
        raise RuntimeError("ARD-Suche nicht erreichbar")
    return books


# --------------------------------------------------------------------------
# Nur verlinkte Quellen (keine Wiedergabe in der App)
# --------------------------------------------------------------------------
def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or ""))).strip()


def meta_content(page: str, name: str) -> str:
    m = re.search(rf'<meta[^>]+(?:name|property)="{re.escape(name)}"[^>]+content="([^"]*)"', page, re.I) or \
        re.search(rf'<meta[^>]+content="([^"]*)"[^>]+(?:name|property)="{re.escape(name)}"', page, re.I)
    return html.unescape(m.group(1)).strip() if m else ""


def parse_vorleser(url: str, page: str) -> dict | None:
    ld = re.search(r'"@type"\s*:\s*"Audiobook"(.*?)</script>', page, re.S)
    if not ld:
        return None
    block = ld.group(1)
    name = (re.search(r'"name"\s*:\s*"([^"]+)"', block) or [None, ""])[1]
    dur = (re.search(r'"duration"\s*:\s*"P?T?(\d+):(\d+):(\d+)"', block))
    secs = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + int(dur.group(3)) if dur else 0
    img = (re.search(r'"image"\s*:\s*"([^"]+)"', block) or [None, ""])[1]
    author = strip_tags((re.search(r'<p class="author">(.*?)</p>', page, re.S) or [None, ""])[1])
    speaker = strip_tags((re.search(r'<dd class="speaker">(.*?)</dd>', page, re.S) or [None, ""])[1])
    cats = strip_tags((re.search(r'Kategorien</dt>\s*<dd>(.*?)</dd>', page, re.S) or [None, ""])[1])
    desc = meta_content(page, "description") or meta_content(page, "og:description")
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return {"slug": slug, "title": html.unescape(name), "secs": secs, "img": img, "author": author,
            "speaker": speaker, "cats": cats, "desc": desc}


def crawl_vorleser(rejected: dict) -> list[dict]:
    idx = http_text("https://www.vorleser.net/sitemap.xml")
    locs = [html.unescape(x) for x in re.findall(r"<loc>([^<]+)</loc>", idx)]
    prod = next((u for u in locs if "products" in u), None)
    urls = [html.unescape(x) for x in re.findall(r"<loc>([^<]+/hoerbuch/[^<]+)</loc>", http_text(prod))] if prod else []
    if not urls:
        raise RuntimeError("keine Hörbuchliste im Sitemap gefunden")
    log(f"Vorleser.net: prüfe {len(urls)} Titel …")
    books = []

    def fetch(u):
        time.sleep(0.3)
        return u, http_text(u, retries=2)
    with ThreadPoolExecutor(max_workers=3) as pool:
        for fut in as_completed([pool.submit(fetch, u) for u in urls]):
            try:
                u, page = fut.result()
            except Exception:
                continue
            v = parse_vorleser(u, page)
            if not v or v["secs"] < CONFIG["min_minutes"] * 60:
                rejected["Vorleser.net: zu kurz"] = rejected.get("Vorleser.net: zu kurz", 0) + 1
                continue
            books.append({
                "id": f"vorleser:{v['slug']}", "source": "Vorleser.net", "station": "", "title": v["title"],
                "author": v["author"], "category": classify(f"{v['cats']} {v['title']}", v["desc"]),
                "form": "Hörspiel" if "hörspiel" in v["cats"].lower() else "Lesung",
                "duration": v["secs"], "parts": 1, "description": clean_text(
                    (v["desc"] + (f" Gelesen von {v['speaker']}." if v["speaker"] else "")).strip()),
                "url": u, "cover": v["img"], "links": {}, "external": True,
                "complete_reason": "vollständiges Hörbuch bei Vorleser.net",
            })
    log(f"Vorleser.net: {len(books)} Hörbücher ab {CONFIG['min_minutes']} Min.")
    return books


def parse_ohrka(url: str, page: str) -> dict | None:
    m = re.search(r"ohrka_mp3_seconds\s*=\s*(\d+)", page)
    if not m:
        return None
    title = meta_content(page, "og:title") or strip_tags((re.search(r"<title>(.*?)</title>", page, re.S) or [None, ""])[1])
    title = re.sub(r"^\s*OHRKA e\.V\.\s*:\s*", "", title).strip()
    text = strip_tags(page)
    reader = re.search(r"gelesen von ([A-ZÄÖÜ][\wäöüß.\- ]{2,40}?)(?=[,.(]| der | die | und |$)", text)
    return {"title": title, "secs": int(m.group(1)), "reader": reader.group(1).strip() if reader else "",
            "desc": meta_content(page, "description"), "img": meta_content(page, "og:image")}


def crawl_ohrka(rejected: dict) -> list[dict]:
    books, seen = [], set()
    for cat in CONFIG["ohrka_categories"]:
        try:
            listing = http_text(f"https://www.ohrka.de/hoeren/{cat}")
        except RuntimeError as e:
            log(f"Ohrka: Rubrik {cat} nicht lesbar ({e})")
            continue
        for path in dict.fromkeys(re.findall(rf'href="(/hoeren/{cat}/[a-z0-9\-]+)"', listing)):
            if path in seen:
                continue
            seen.add(path)
            url = "https://www.ohrka.de" + path
            try:
                o = parse_ohrka(url, http_text(url, retries=2))
            except RuntimeError:
                continue
            time.sleep(0.3)
            if not o or o["secs"] < CONFIG["min_minutes"] * 60:
                rejected["Ohrka: zu kurz"] = rejected.get("Ohrka: zu kurz", 0) + 1
                continue
            books.append({
                "id": f"ohrka:{path.rsplit('/', 1)[-1]}", "source": "Ohrka", "station": "", "title": o["title"],
                "author": "", "category": "Märchen, Sagen & Mythen" if "maerchen" in cat else "Kinder & Jugend",
                "form": "Lesung", "duration": o["secs"], "parts": 1,
                "description": clean_text((o["desc"] + (f" Gelesen von {o['reader']}." if o["reader"] else "")).strip()),
                "url": url, "cover": o["img"], "links": {}, "external": True,
                "complete_reason": "vollständiges Hörbuch bei Ohrka",
            })
    if not seen:
        raise RuntimeError("keine Ohrka-Titel gefunden")
    log(f"Ohrka: {len(books)} Hörbücher")
    return books


SOURCES = {"librivox": ("LibriVox", crawl_librivox), "ard": ("ARD Audiothek", crawl_ard),
           "ard_serien": ("ARD-Hörspielreihen", crawl_ard_series), "feeds": ("Deutschlandfunk", crawl_feeds),
           "vorleser": ("Vorleser.net", crawl_vorleser), "ohrka": ("Ohrka", crawl_ohrka)}


def book_origin(b: dict) -> str:
    """Welche Crawl-Quelle hat diesen Titel geliefert? (für 'alte Daten behalten')"""
    pre = b.get("id", "").split(":", 1)[0]
    return {"librivox": "librivox", "ard": "ard", "ardserie": "ard_serien", "feed": "feeds",
            "vorleser": "vorleser", "ohrka": "ohrka"}.get(pre, pre)


def load_data() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text("utf-8"))
        except json.JSONDecodeError:
            pass
    return {"meta": {}, "books": []}


def run_crawl() -> dict:
    with _status_lock:
        if STATUS["running"]:
            return STATUS
        STATUS.update(running=True, log=[], started=datetime.now().isoformat(timespec="seconds"),
                      finished=None)
    try:
        old = load_data()
        books, meta, rejected = [], {"sources": {}}, {}
        for key, (label, fn) in SOURCES.items():
            if not CONFIG["sources"].get(key):          # Quelle abgeschaltet → alte Titel behalten
                books.extend(b for b in old.get("books", []) if book_origin(b) == key)
                continue
            t0 = time.time()
            try:
                found = fn(rejected)
                meta["sources"][label] = {"ok": True, "count": len(found), "error": None,
                                          "seconds": round(time.time() - t0)}
            except Exception as e:                       # Quelle ausgefallen → alte Daten behalten
                found = [b for b in old.get("books", []) if book_origin(b) == key]
                meta["sources"][label] = {"ok": False, "count": len(found), "error": str(e)[:300],
                                          "seconds": round(time.time() - t0)}
                log(f"{label}: FEHLER – {e}. Behalte {len(found)} Titel aus dem letzten Lauf.")
            books.extend(found)
        seen, unique = set(), []
        for b in books:
            if b["id"] not in seen:
                seen.add(b["id"])
                unique.append(b)
        meta.update(updated=datetime.now().isoformat(timespec="seconds"), total=len(unique),
                    rejected=dict(sorted(rejected.items(), key=lambda kv: -kv[1])))
        tmp = DATA_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"meta": meta, "books": unique}, ensure_ascii=False), "utf-8")
        tmp.replace(DATA_FILE)
        log(f"Fertig: {len(unique)} vollständige Hörbücher gespeichert ({DATA_FILE.name}).")
        return meta
    finally:
        with _status_lock:
            STATUS.update(running=False, finished=datetime.now().isoformat(timespec="seconds"))


# --------------------------------------------------------------------------
# Export für die iPhone-App (statische Website, z. B. GitHub Pages)
# --------------------------------------------------------------------------
def https(url: str) -> str:
    return re.sub(r"^http://", "https://", url or "")


def safe_name(book_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", book_id)


def export_site(outdir: Path) -> None:
    """Schreibt catalog.json + tracks/<id>.json. Vorhandene LibriVox-Kapitel werden wiederverwendet."""
    data = load_data()
    tdir = outdir / "tracks"
    tdir.mkdir(parents=True, exist_ok=True)
    books = data.get("books", [])
    log(f"Export: {len(books)} Titel nach {outdir} …")

    def tracks_for(b: dict) -> list[dict]:
        f = tdir / f"{safe_name(b['id'])}.json"
        if b.get("external"):
            return []
        if b.get("tracks") is not None:
            return b["tracks"]
        if f.exists():                                   # LibriVox-Kapitel ändern sich nicht
            try:
                return json.loads(f.read_text("utf-8"))
            except json.JSONDecodeError:
                pass
        try:
            return librivox_tracks(b["id"], b.get("archive_id", ""))
        except Exception:
            return []

    catalog, keep = [], set()
    with ThreadPoolExecutor(max_workers=CONFIG["workers"]) as pool:
        futs = {pool.submit(tracks_for, b): b for b in books}
        for n, fut in enumerate(as_completed(futs), 1):
            b, tracks = futs[fut], fut.result()
            tracks = [{"t": t.get("title") or f"Teil {i + 1}", "u": https(t.get("url")),
                       "d": to_int(t.get("duration"))} for i, t in enumerate(tracks) if t.get("url")]
            ext = bool(b.get("external"))
            if not tracks and not ext:
                continue                                 # nicht abspielbar → nicht in die App
            if tracks:
                name = safe_name(b["id"])
                keep.add(name)
                (tdir / f"{name}.json").write_text(json.dumps(tracks, ensure_ascii=False), "utf-8")
            catalog.append({k: v for k, v in {
                "id": b["id"], "src": b["source"], "st": b.get("station", ""), "t": b["title"],
                "a": b.get("author", ""), "c": b["category"], "f": b["form"],
                "d": sum(t["d"] for t in tracks) or b.get("duration", 0), "p": len(tracks) or b.get("parts", 1),
                "ext": 1 if ext else None,
                "x": b.get("description", ""), "img": https(b.get("cover", "")), "url": https(b.get("url", "")),
                "ok": b.get("complete_reason", "")}.items() if v not in ("", None)})
            if n % 200 == 0:
                log(f"Export: {n}/{len(books)}")
    for f in tdir.glob("*.json"):                        # entfernte Titel aufräumen
        if f.stem not in keep:
            f.unlink()
    catalog.sort(key=lambda b: b["t"].lower())
    (outdir / "catalog.json").write_text(json.dumps(
        {"updated": data.get("meta", {}).get("updated"), "books": catalog},
        ensure_ascii=False, separators=(",", ":")), "utf-8")
    n_ext = sum(1 for b in catalog if b.get("ext"))
    log(f"Export fertig: {len(catalog) - n_ext} Hörbücher zum Abspielen + {n_ext} verlinkte Titel.")


# --------------------------------------------------------------------------
# Podcasts (Liste in podcasts.txt → docs/podcasts.json, läuft alle 2 Stunden)
# --------------------------------------------------------------------------
PODCAST_LIST = BASE_DIR / "podcasts.txt"
PODCAST_FILE = BASE_DIR / "docs" / "podcasts.json"
PODCAST_EPISODES = 40


def read_podcast_list() -> list[str]:
    if not PODCAST_LIST.exists():
        return []
    lines = [l.split("#", 1)[0].strip() if not l.strip().startswith("http") else l.strip()
             for l in PODCAST_LIST.read_text("utf-8").splitlines()]
    return [l for l in dict.fromkeys(lines) if l]


def itunes_find(term: str) -> dict | None:
    """Sucht einen Podcast im Apple-Podcast-Verzeichnis; nur Treffer, die zum Suchbegriff passen."""
    url = ("https://itunes.apple.com/search?media=podcast&entity=podcast&country=DE&limit=8&term="
           + urllib.parse.quote(term))
    res = (http_json(url, timeout=30) or {}).get("results") or []
    words = [w for w in norm_key(term).split() if len(w) > 2] or norm_key(term).split()
    for r in res:
        hay = norm_key(f"{r.get('collectionName', '')} {r.get('artistName', '')}")
        if r.get("feedUrl") and all(w in hay for w in words):
            return {"feed": r["feedUrl"], "img": r.get("artworkUrl600") or r.get("artworkUrl100") or ""}
    return None


def crawl_podcasts() -> dict:
    entries = read_podcast_list()
    old = {}
    if PODCAST_FILE.exists():
        try:
            old = {p["term"]: p for p in json.loads(PODCAST_FILE.read_text("utf-8")).get("podcasts", [])}
        except (json.JSONDecodeError, KeyError):
            old = {}
    pods = []
    for term in entries:
        prev = old.get(term, {})
        feed, art = (term, "") if term.startswith("http") else (prev.get("feed"), "")
        if not feed:
            try:
                hit = itunes_find(term)
            except Exception as e:
                hit = None
                log(f"Podcast '{term}': Suche fehlgeschlagen ({e})")
            if not hit:
                log(f"Podcast '{term}': nicht gefunden – bitte genauer benennen oder Feed-Adresse eintragen")
                continue
            feed, art = hit["feed"], hit["img"]
        try:
            xml_text = http_text(feed, timeout=40)
            ch, eps = feed_channel(xml_text), parse_feed(xml_text)
        except Exception as e:
            log(f"Podcast '{term}': Feed nicht lesbar ({e}) – behalte letzte Folgen")
            if prev:
                pods.append(prev)
            continue
        pid = hashlib.sha1(feed.encode("utf-8")).hexdigest()[:10]
        eps = [e for e in eps if e.get("url")]
        eps.sort(key=lambda e: e.get("date") or "", reverse=True)
        pods.append({
            "id": pid, "term": term, "feed": feed,
            "name": ch.get("name") or term, "author": ch.get("author", ""),
            "img": https(ch.get("img") or art or prev.get("img", "")), "desc": ch.get("desc", ""),
            "eps": [{"id": "pod:" + hashlib.sha1(f"{feed}|{e.get('guid') or e['url']}".encode("utf-8")).hexdigest()[:12],
                     "t": e["title"], "u": https(e["url"]), "d": e.get("duration", 0), "date": e.get("date", ""),
                     "x": clean_text(e.get("description"), 300),
                     **({"img": https(e["image"])} if e.get("image") and e["image"] != ch.get("img") else {})}
                    for e in eps[:PODCAST_EPISODES]],
        })
        log(f"Podcast '{pods[-1]['name']}': {len(pods[-1]['eps'])} Folgen")
        time.sleep(0.3)
    data = {"updated": datetime.now().isoformat(timespec="seconds"), "podcasts": pods}
    PODCAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PODCAST_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), "utf-8")
    tmp.replace(PODCAST_FILE)
    log(f"Podcasts fertig: {len(pods)} von {len(entries)} Einträgen.")
    return data


def diagnose() -> None:
    print("Prüfe Quellen …\n")
    checks = [
        ("LibriVox-Katalog", lambda: len(http_json(f"{LIBRIVOX_API}?format=json&limit=3")["books"])),
        ("ARD Rubriken", lambda: len(gql("{ editorialCategories(first: 50) { nodes { id title } } }")
                                     ["editorialCategories"]["nodes"])),
        ("ARD Suche", lambda: len(gql('{ search(query: "Hörbuch", limit: 5) { programSets { nodes { id title } } } }')
                                  ["search"]["programSets"]["nodes"])),
    ]
    for name, fn in checks:
        try:
            print(f"  OK     {name}: {fn()} Einträge")
        except Exception as e:
            print(f"  FEHLER {name}: {e}")


# --------------------------------------------------------------------------
# Webserver
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):          # Konsole ruhig halten
        pass

    def _send(self, code: int, body, ctype="application/json; charset=utf-8"):
        raw = body if isinstance(body, bytes) else (
            body.encode("utf-8") if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode("utf-8"))
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        if url.path == "/api/books":
            return self._send(200, DATA_FILE.read_bytes() if DATA_FILE.exists()
                              else json.dumps({"meta": {}, "books": []}).encode())
        if url.path == "/api/status":
            with _status_lock:
                return self._send(200, dict(STATUS))
        if url.path == "/api/tracks":
            qs = urllib.parse.parse_qs(url.query)
            bid, aid = qs.get("id", [""])[0], qs.get("archive", [""])[0]
            if not bid.startswith("librivox:"):
                return self._send(400, {"error": "unbekannte ID"})
            try:
                return self._send(200, {"tracks": librivox_tracks(bid, aid)})
            except Exception as e:
                return self._send(502, {"error": str(e)})
        self._send(404, {"error": "nicht gefunden"})

    def do_POST(self):
        if self.path == "/api/crawl":
            if not STATUS["running"]:
                threading.Thread(target=run_crawl, daemon=True).start()
            return self._send(202, {"started": True})
        self._send(404, {"error": "nicht gefunden"})


def serve(open_browser: bool = True) -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"Hörbuch-Finder läuft auf {url}  (Beenden mit Strg+C)")
    if not DATA_FILE.exists():
        print("Noch keine Daten – starte ersten Crawl im Hintergrund …")
        threading.Thread(target=run_crawl, daemon=True).start()
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")


# --------------------------------------------------------------------------
# Oberfläche (eine Seite, kein Build nötig)
# --------------------------------------------------------------------------
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hörbuch-Finder</title>
<style>
:root{--bg:#f6f3ee;--panel:#fffdf9;--ink:#23201c;--muted:#6f675d;--line:#e4ddd2;--accent:#b4532a;--accent-ink:#fff;--chip:#efe8dd}
@media (prefers-color-scheme:dark){:root{--bg:#171512;--panel:#211e1a;--ink:#efe9e0;--muted:#a59b8e;--line:#36312a;--accent:#e07a4a;--accent-ink:#1a1410;--chip:#2c2823}}
*{box-sizing:border-box}
body{margin:0;font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--ink)}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:14px 22px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
h1{font:600 20px/1 Georgia,"Times New Roman",serif;margin:0 12px 0 0;letter-spacing:.2px}
h1 span{color:var(--accent)}
input,select,button{font:inherit;color:inherit;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:7px 10px}
input[type=search]{flex:1;min-width:200px;max-width:420px}
button{cursor:pointer}
button.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
#status{font-size:13px;color:var(--muted);margin-left:auto}
.layout{display:grid;grid-template-columns:260px 1fr;min-height:calc(100vh - 64px)}
nav{border-right:1px solid var(--line);padding:16px 10px;position:sticky;top:64px;height:calc(100vh - 64px);overflow:auto}
nav button{display:flex;justify-content:space-between;width:100%;border:0;background:none;text-align:left;padding:7px 12px;border-radius:8px;margin-bottom:2px}
nav button:hover{background:var(--chip)}
nav button.active{background:var(--accent);color:var(--accent-ink)}
nav .count{opacity:.7;font-variant-numeric:tabular-nums}
nav h3{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:6px 12px 8px}
main{padding:18px 22px 120px}
.summary{color:var(--muted);margin:0 0 14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden;cursor:pointer;display:flex;flex-direction:column;transition:transform .12s,box-shadow .12s}
.card:hover{transform:translateY(-2px);box-shadow:0 6px 18px rgba(0,0,0,.08)}
.cover{aspect-ratio:1/1;background:var(--chip) center/cover no-repeat;display:flex;align-items:flex-end;padding:8px}
.cover{position:relative}.initial{position:absolute;inset:0;display:grid;place-items:center;font:600 64px Georgia,serif;color:var(--muted);opacity:.45}
.badge{position:relative;font-size:11px;padding:2px 7px;border-radius:99px;background:rgba(0,0,0,.6);color:#fff}
.card .body{padding:10px 12px 12px}
.card h4{margin:0 0 3px;font:600 15px/1.25 Georgia,serif}
.card .author{color:var(--muted);font-size:13px}
.meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.chip{font-size:12px;background:var(--chip);border-radius:99px;padding:2px 8px;color:var(--muted)}
.empty{padding:60px 0;text-align:center;color:var(--muted)}
dialog{border:1px solid var(--line);border-radius:14px;background:var(--panel);color:var(--ink);width:min(760px,94vw);padding:0}
dialog::backdrop{background:rgba(0,0,0,.45)}
.dlg-head{display:flex;gap:16px;padding:18px}
.dlg-head img{width:130px;height:130px;object-fit:cover;border-radius:10px;background:var(--chip)}
.dlg-head h2{margin:0 0 4px;font:600 22px/1.2 Georgia,serif}
.dlg-body{padding:0 18px 18px}
.dlg-body p{margin:0 0 12px}
.links a{color:var(--accent);margin-right:14px;font-size:14px}
ol.tracks{max-height:260px;overflow:auto;padding-left:0;list-style:none;border-top:1px solid var(--line);margin:12px 0 0}
ol.tracks li{padding:7px 4px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:10px;cursor:pointer;font-size:14px}
ol.tracks li:hover{background:var(--chip)}
ol.tracks li.playing{color:var(--accent);font-weight:600}
.close{float:right;margin:12px}
#player{position:fixed;left:0;right:0;bottom:0;background:var(--panel);border-top:1px solid var(--line);padding:10px 22px;display:none;align-items:center;gap:14px;z-index:9}
#player audio{flex:1}
#player .now{max-width:40%;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#log{white-space:pre-wrap;font:12px/1.4 ui-monospace,Consolas,monospace;max-height:320px;overflow:auto;background:var(--chip);padding:10px;border-radius:8px}
@media (max-width:760px){.layout{grid-template-columns:1fr}nav{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line)}}
</style>
</head>
<body>
<header>
  <h1>Hörbuch<span>·</span>Finder</h1>
  <input type="search" id="q" placeholder="Titel, Autor oder Stichwort …">
  <select id="source"><option value="">Alle Quellen</option></select>
  <select id="form"><option value="">Lesung & Hörspiel</option><option>Lesung</option><option>Hörspiel</option></select>
  <select id="sort">
    <option value="title">Titel A–Z</option><option value="author">Autor A–Z</option>
    <option value="long">Längste zuerst</option><option value="short">Kürzeste zuerst</option>
  </select>
  <span id="status"></span>
  <button id="logbtn" title="Protokoll des letzten Suchlaufs">Protokoll</button>
  <button id="crawl" class="primary">Neu durchsuchen</button>
</header>
<div class="layout">
  <nav><h3>Kategorien</h3><div id="cats"></div></nav>
  <main><p class="summary" id="summary"></p><div class="grid" id="grid"></div></main>
</div>

<dialog id="dlg"><button class="close" onclick="dlg.close()">✕</button><div id="dlgc"></div></dialog>
<dialog id="logdlg"><button class="close" onclick="logdlg.close()">✕</button>
  <div class="dlg-body" style="padding-top:18px"><h2 style="font:600 20px Georgia,serif;margin:0 0 10px">Suchlauf</h2>
  <div id="srcinfo"></div><div id="log"></div></div></dialog>

<div id="player"><div class="now" id="now"></div><audio id="audio" controls preload="none"></audio>
  <button id="prev">⏮</button><button id="next">⏭</button></div>

<script>
const $=s=>document.querySelector(s), esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let DATA={meta:{},books:[]}, cat="", playlist=[], idx=-1, PAGE=120, shown=PAGE;
const dur=s=>{s=+s||0;const h=Math.floor(s/3600),m=Math.round(s%3600/60);return h?`${h} Std ${m} Min`:`${m} Min`};
const norm=s=>(s||"").toLocaleLowerCase("de").normalize("NFD").replace(/[̀-ͯ]/g,"");

async function load(){
  DATA=await (await fetch("/api/books")).json();
  const srcs=[...new Set(DATA.books.map(b=>b.source))].sort();
  $("#source").innerHTML='<option value="">Alle Quellen</option>'+srcs.map(s=>`<option>${esc(s)}</option>`).join("");
  render();
}
function filtered(ignoreCat){
  const q=norm($("#q").value).split(/\s+/).filter(Boolean), src=$("#source").value, form=$("#form").value;
  return DATA.books.filter(b=>(!src||b.source===src)&&(!form||b.form===form)&&(ignoreCat||!cat||b.category===cat)&&
    q.every(w=>norm(`${b.title} ${b.author} ${b.description} ${b.station||""} ${(b.genres||[]).join(" ")}`).includes(w)));
}
function render(){
  const base=filtered(true), counts={};
  base.forEach(b=>counts[b.category]=(counts[b.category]||0)+1);
  const cats=Object.keys(counts).sort((a,b)=>a.localeCompare(b,"de"));
  $("#cats").innerHTML=`<button class="${cat?"":"active"}" data-c=""><span>Alle</span><span class="count">${base.length}</span></button>`+
    cats.map(c=>`<button class="${c===cat?"active":""}" data-c="${esc(c)}"><span>${esc(c)}</span><span class="count">${counts[c]}</span></button>`).join("");
  let list=filtered(false);
  const s=$("#sort").value, cmp=(a,b)=>a.localeCompare(b,"de",{sensitivity:"base"});
  list.sort(s==="author"?(a,b)=>cmp(a.author||"~",b.author||"~"):s==="long"?(a,b)=>b.duration-a.duration:s==="short"?(a,b)=>a.duration-b.duration:(a,b)=>cmp(a.title,b.title));
  const m=DATA.meta||{};
  $("#summary").textContent=DATA.books.length?`${list.length} vollständige Hörbücher${cat?" in „"+cat+"“":""}`+(m.updated?` · Stand ${new Date(m.updated).toLocaleString("de-DE")}`:""):"";
  if(!DATA.books.length){$("#grid").innerHTML='<div class="empty">Noch keine Daten. Klicke auf „Neu durchsuchen“ – der erste Lauf dauert einige Minuten.</div>';return}
  $("#grid").innerHTML=list.slice(0,shown).map((b,i)=>`<div class="card" data-id="${esc(b.id)}">
    <div class="cover" style="${b.cover?`background-image:url('${esc(b.cover)}')`:""}">${b.cover?"":`<span class="initial">${esc((b.title||"?")[0])}</span>`}<span class="badge">${esc(b.source==="ARD Audiothek"?(b.station||"ARD"):b.source)}</span></div>
    <div class="body"><h4>${esc(b.title)}</h4><div class="author">${esc(b.author||"")}</div>
    <div class="meta"><span class="chip">${dur(b.duration)}</span><span class="chip">${b.parts} ${b.parts==1?"Teil":"Teile"}</span><span class="chip">${esc(b.form)}</span></div></div></div>`).join("")+
    (list.length>shown?`<div class="empty" style="grid-column:1/-1"><button id="more">Weitere ${Math.min(PAGE,list.length-shown)} anzeigen (${list.length-shown} übrig)</button></div>`:"");
}
async function openBook(id){
  const b=DATA.books.find(x=>x.id===id); if(!b) return;
  const links=[`<a href="${esc(b.url)}" target="_blank" rel="noopener">Zur Quelle ↗</a>`].concat(Object.entries(b.links||{}).map(([k,v])=>`<a href="${esc(v)}" target="_blank" rel="noopener">${esc(k)} ↗</a>`)).join("");
  $("#dlgc").innerHTML=`<div class="dlg-head"><img src="${esc(b.cover)}" alt="" onerror="this.style.visibility='hidden'"><div>
    <h2>${esc(b.title)}</h2><div class="author">${esc(b.author||"")}</div>
    <div class="meta"><span class="chip">${esc(b.category)}</span><span class="chip">${esc(b.form)}</span><span class="chip">${dur(b.duration)}</span><span class="chip">${esc(b.source)}${b.station?" · "+esc(b.station):""}</span></div>
    <div class="meta"><span class="chip" title="Vollständigkeitsprüfung">✓ ${esc(b.complete_reason||"vollständig")}</span></div></div></div>
    <div class="dlg-body"><p>${esc(b.description||"")}</p><div class="links">${links}</div>
    <div style="margin-top:14px"><button class="primary" id="playall">▶ Von Anfang an abspielen</button></div>
    <ol class="tracks" id="tracks"><li>Lade Kapitel …</li></ol></div>`;
  dlg.showModal();
  let tracks=b.tracks;
  if(!tracks){try{const r=await (await fetch(`/api/tracks?id=${encodeURIComponent(b.id)}&archive=${encodeURIComponent(b.archive_id||"")}`)).json();tracks=r.tracks||[];b.tracks=tracks}catch(e){tracks=[]}}
  $("#tracks").innerHTML=tracks.length?tracks.map((t,i)=>`<li data-i="${i}"><span>${i+1}. ${esc(t.title)}</span><span>${t.duration?dur(t.duration):""}</span></li>`).join(""):'<li>Kapitel konnten nicht geladen werden – bitte über „Zur Quelle“ anhören.</li>';
  $("#tracks").onclick=e=>{const li=e.target.closest("li[data-i]");if(li)play(b,tracks,+li.dataset.i)};
  $("#playall").onclick=()=>tracks.length&&play(b,tracks,0);
}
function play(b,tracks,i){
  playlist=tracks.map(t=>({...t,book:b.title}));idx=i;startTrack();$("#player").style.display="flex";
}
function startTrack(){
  const t=playlist[idx]; if(!t) return;
  $("#audio").src=t.url; $("#audio").play().catch(()=>{});
  $("#now").textContent=`${t.book} – ${idx+1}/${playlist.length}: ${t.title}`;
  document.querySelectorAll("ol.tracks li").forEach(li=>li.classList.toggle("playing",+li.dataset.i===idx));
}
$("#audio").addEventListener("ended",()=>{if(idx<playlist.length-1){idx++;startTrack()}});
$("#next").onclick=()=>{if(idx<playlist.length-1){idx++;startTrack()}};
$("#prev").onclick=()=>{if(idx>0){idx--;startTrack()}};
$("#grid").onclick=e=>{if(e.target.id==="more"){shown+=PAGE;render();return}const c=e.target.closest(".card");if(c)openBook(c.dataset.id)};
$("#cats").onclick=e=>{const b=e.target.closest("button");if(b){cat=b.dataset.c;shown=PAGE;render();window.scrollTo(0,0)}};
["#q","#source","#form","#sort"].forEach(s=>$(s).addEventListener("input",()=>{shown=PAGE;render()}));
$("#crawl").onclick=async()=>{await fetch("/api/crawl",{method:"POST"});poll()};
$("#logbtn").onclick=()=>{showLog();logdlg.showModal()};
async function showLog(){
  const st=await (await fetch("/api/status")).json(), m=DATA.meta||{};
  $("#log").textContent=(st.log||[]).join("\n")||"Kein Suchlauf in dieser Sitzung.";
  const src=Object.entries(m.sources||{}).map(([k,v])=>`<div class="chip" style="display:inline-block;margin:0 6px 6px 0">${v.ok?"✓":"✗"} ${esc(k)}: ${v.count} Titel${v.error?" – "+esc(v.error):""}</div>`).join("");
  const rej=Object.entries(m.rejected||{}).map(([k,v])=>`${esc(k)}: ${v}`).join(" · ");
  $("#srcinfo").innerHTML=src+(rej?`<p style="color:var(--muted);font-size:13px">Aussortiert: ${rej}</p>`:"");
}
let polling=false;
async function poll(){
  if(polling) return; polling=true;
  while(true){
    const st=await (await fetch("/api/status")).json();
    const last=(st.log||[]).slice(-1)[0]||"";
    $("#status").textContent=st.running?"⏳ "+last.replace(/^\S+\s+/,""):"";
    $("#crawl").disabled=st.running;
    if(logdlg.open) showLog();
    if(!st.running){polling=false; await load(); break}
    await new Promise(r=>setTimeout(r,2000));
  }
}
load().then(poll);
</script>
</body>
</html>"""


# --------------------------------------------------------------------------
def main() -> None:
    global PORT
    ap = argparse.ArgumentParser(description="Hörbuch-Finder: vollständige deutsche Hörbücher finden")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "crawl", "site", "podcasts", "diagnose"])
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--skip-crawl", action="store_true", help="bei 'site': nur vorhandene Daten exportieren")
    ap.add_argument("--only", choices=list(SOURCES), help="nur eine Quelle crawlen")
    args = ap.parse_args()
    PORT = args.port
    if args.only:
        CONFIG["sources"] = {k: k == args.only for k in SOURCES}
    if args.command == "crawl":
        meta = run_crawl()
        print(json.dumps(meta, ensure_ascii=False, indent=2))
    elif args.command == "site":                      # crawlen + App-Daten schreiben
        if not args.skip_crawl:
            run_crawl()
        export_site(BASE_DIR / "docs")
    elif args.command == "podcasts":                  # nur Podcasts abgleichen (schnell, alle 2 Stunden)
        crawl_podcasts()
    elif args.command == "diagnose":
        diagnose()
    else:
        serve(not args.no_browser)


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    main()
