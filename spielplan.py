#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSC Paderborn - Spielplan aller Mannschaften von FUSSBALL.DE als Kalender.

Fassung 4:
  - Hauptquelle ist der Vereinsspielplan. Der liefert die komplette Saison
    aller Mannschaften auf einmal, ohne die Zehnergrenze der Einzelabfragen.
  - Zusaetzlich werden die acht Mannschaften einzeln abgefragt. Falls der
    Vereinsspielplan einmal nicht auswertbar ist, fehlt so trotzdem nichts.
  - Zeilen wie "verlegt vom: 12.09.2026" werden ignoriert, damit kein altes
    Datum auf das naechste Spiel abfaerbt.

Es entstehen zwei Dateien:
  kalender.ics         alle Mannschaften
  kalender-jugend.ics  nur die Jugendmannschaften

Bei Problemen: die Datei diagnose.txt im selben Ordner ansehen.
"""

import re
import sys
import time
import traceback
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# EINSTELLUNGEN
# ---------------------------------------------------------------------------

VEREIN_ID = "02B0T8CACO000000VS5489B5VVH98KVI"
VEREIN_NAME = "csc-paderborn-2020-westfalen"

# Die acht Mannschaften des Vereins (Stand September 2026).
# Nur zur Absicherung - der Vereinsspielplan deckt normalerweise alles ab.
TEAMS = [
    ("Herren",              "02B9TIHPLS000000VS5489B1VU20GQ5T"),
    ("Herren II",           "02PHE6CPGG000000VS5489B1VUI2QQ8R"),
    ("B-Junioren 9er",      "02T6M9PF3S000000VS5489BRVSH2JAIF"),
    ("C-Junioren",          "02I655D4C0000000VS5489B2VVQB1KHD"),
    ("D-Junioren",          "02EO22JI3S000000VS5489B2VSAS84KM"),
    ("D-Junioren II 7er",   "030T4SU0TC000000VS5489BSVSO3A7B3"),
    ("E-Junioren",          "02GNQ1A98K000000VS5489B2VULC3I30"),
    ("F-Junioren",          "02GNQ1MDHK000000VS5489B2VULC3I30"),
]

DAUER_MINUTEN = 90
ERINNERUNGEN = ["-PT18H", "-PT2H"]
RUECKBLICK_TAGE = 1
VORAUSSCHAU_TAGE = 400

# Spielstaette von der Spielseite nachladen. Kostet pro Spiel einen Abruf.
SPIELSTAETTE_LADEN = True

AUSGABE_ALLE = "kalender.ics"
AUSGABE_JUGEND = "kalender-jugend.ics"
DIAGNOSE_DATEI = "diagnose.txt"

# Woran eine Altersklasse erkannt wird (im Gegensatz zum Wettbewerb).
ALTERSKLASSEN_WOERTER = ("junioren", "juniorinnen", "herren", "frauen",
                         "senioren", "seniorinnen")
JUGEND_WOERTER = ("junioren", "juniorinnen")

# Zeilen mit diesen Woertern liefern kein gueltiges Spieldatum.
DATUM_IGNORIEREN = ("verlegt vom", "verlegt von", "urspruenglich",
                    "ursprünglich", "schiedsrichter")

KOPFZEILEN = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9",
}

protokoll = []


def notiz(text):
    print(text)
    protokoll.append(text)


# ---------------------------------------------------------------------------
# Abrufen
# ---------------------------------------------------------------------------

def seite_holen(url, versuche=3):
    letzter_fehler = None
    for nummer in range(versuche):
        try:
            antwort = requests.get(url, headers=KOPFZEILEN, timeout=45)
            if antwort.status_code == 200:
                return antwort.text
            letzter_fehler = "HTTP %s" % antwort.status_code
        except Exception as fehler:
            letzter_fehler = str(fehler)
        time.sleep(2 + nummer * 3)
    notiz("    Abruf fehlgeschlagen (%s)" % letzter_fehler)
    return None


def vereinsspielplan_adressen():
    """Der Vereinsspielplan in mehreren Schreibweisen.

    Die Druckansicht ist geprueft und liefert die ganze Saison. Die
    uebrigen Varianten sind Ausweichmoeglichkeiten, falls FUSSBALL.DE
    daran etwas aendert.
    """
    von = (datetime.now() - timedelta(days=RUECKBLICK_TAGE)).strftime("%Y-%m-%d")
    bis = (datetime.now() + timedelta(days=VORAUSSCHAU_TAGE)).strftime("%Y-%m-%d")
    spanne = "datum-bis/%s/datum-von/%s" % (bis, von)

    return [
        # Zuerst die geprueft zuverlaessige Quelle: hier stehen Datum und
        # Uhrzeit als Text in den Ueberschriftenzeilen.
        "https://www.fussball.de/ajax.club.matchplan/-/%s/id/%s"
        "/match-type/-1/max/999/mode/PAGE/show-venues/true" % (spanne, VEREIN_ID),
        "https://www.fussball.de/ajax.club.matchplan/-/%s/id/%s"
        "/match-type/-1/max/999/mode/PAGE/show-filter/true" % (spanne, VEREIN_ID),
        "https://www.fussball.de/ajax.club.next.games/-/id/%s/mode/PAGE" % VEREIN_ID,
        # Die Druckansicht liefert zwar viele Spiele, aber ohne lesbares
        # Datum. Sie steht bewusst hinten und ergaenzt nur noch das,
        # wozu sie ein eigenes Datum mitliefert.
        "https://www.fussball.de/vereinsspielplan.druck/-/%s/id/%s"
        "/match-type/-1/max/999/mode/PRINT/show-venues/true" % (spanne, VEREIN_ID),
    ]


def team_adresse(team_id):
    return "https://www.fussball.de/ajax.team.next.games/-/team-id/%s" % team_id


# ---------------------------------------------------------------------------
# Auswerten
# ---------------------------------------------------------------------------

DATUM_LANG = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
DATUM_KURZ = re.compile(r"(\d{2})\.(\d{2})\.(\d{2})(?!\d)")
ZEIT_MUSTER = re.compile(r"\b(\d{1,2}):(\d{2})\b")
WOCHENTAGE = re.compile(
    r"\b(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag"
    r"|Mo|Di|Mi|Do|Fr|Sa|So)\b\.?,?", re.IGNORECASE)
SPIEL_MUSTER = re.compile(r"/spiel/([A-Za-z0-9-]+)/-/spiel/([A-Z0-9]+)")


def text_von(element):
    return " ".join(element.get_text(" ", strip=True).split())


def datum_aus(inhalt):
    """Liest ein Datum aus einer Zeile, wenn eines drinsteht."""
    treffer = DATUM_LANG.search(inhalt)
    if treffer:
        tag, monat, jahr = (int(x) for x in treffer.groups())
        return (jahr, monat, tag)
    treffer = DATUM_KURZ.search(inhalt)
    if treffer:
        tag, monat, jahr = (int(x) for x in treffer.groups())
        return (2000 + jahr, monat, tag)
    return None


def klasse_und_wettbewerb(inhalt):
    """Trennt Altersklasse und Wettbewerb.

    Die Reihenfolge ist je nach Seite unterschiedlich, deshalb wird
    anhand von Schluesselwoertern entschieden statt anhand der Position.
    """
    teile = []
    for roh in inhalt.split("|"):
        # Uhrzeiten, Datumsangaben und Wochentage stehen manchmal in
        # derselben Zelle wie die Altersklasse und muessen weg.
        sauber = ZEIT_MUSTER.sub(" ", roh)
        sauber = DATUM_LANG.sub(" ", sauber)
        sauber = DATUM_KURZ.sub(" ", sauber)
        sauber = re.sub(WOCHENTAGE, " ", sauber)
        sauber = re.sub(r"\bUhr\b", " ", sauber)
        # Nur den Trenn-Bindestrich entfernen, nicht den in "D-Junioren".
        sauber = re.sub(r"\s+-\s+|^\s*-\s*|\s*-\s*$", " ", sauber)
        sauber = " ".join(sauber.split()).strip(" ,")
        if sauber:
            teile.append(sauber)

    altersklasse, wettbewerb = "", ""
    for teil in teile:
        klein = teil.lower()
        if not altersklasse and (any(w in klein for w in ALTERSKLASSEN_WOERTER)
                                 or klein.startswith("ü")):
            altersklasse = teil
        elif not wettbewerb and not datum_aus(teil) and "uhr" not in klein:
            wettbewerb = teil
    return altersklasse, wettbewerb


def spiele_auslesen(html):
    suppe = BeautifulSoup(html, "html.parser")
    gefunden = []
    aktuelles_datum = None
    aktuelle_zeit = None
    altersklasse = ""
    wettbewerb = ""

    for zeile in suppe.find_all("tr"):
        inhalt = text_von(zeile)
        klein = inhalt.lower()
        verweis = zeile.find("a", href=SPIEL_MUSTER)

        # Zeilen wie "verlegt vom: ..." enthalten ein altes Datum, das
        # nicht auf das naechste Spiel uebertragen werden darf.
        if any(wort in klein for wort in DATUM_IGNORIEREN):
            continue

        if verweis is None:
            neues_datum = datum_aus(inhalt)
            if neues_datum:
                aktuelles_datum = neues_datum
                treffer_zeit = ZEIT_MUSTER.search(inhalt)
                aktuelle_zeit = (tuple(int(x) for x in treffer_zeit.groups())
                                 if treffer_zeit else None)
            neue_klasse, neuer_wettbewerb = klasse_und_wettbewerb(inhalt)
            if neue_klasse:
                altersklasse = neue_klasse
            if neuer_wettbewerb:
                wettbewerb = neuer_wettbewerb
            continue

        # Ab hier: Zeile mit einem Spiel
        adresse = verweis.get("href", "")
        if adresse.startswith("/"):
            adresse = "https://www.fussball.de" + adresse
        kennung = SPIEL_MUSTER.search(adresse).group(2)

        # Manche Zeilen tragen Datum und Zeit direkt im Link, etwa bei
        # verlegten Spielen ("14.09.2026 17:00").
        eigenes_datum = datum_aus(text_von(verweis))
        if eigenes_datum:
            aktuelles_datum = eigenes_datum
            treffer_zeit = ZEIT_MUSTER.search(text_von(verweis))
            if treffer_zeit:
                aktuelle_zeit = tuple(int(x) for x in treffer_zeit.groups())

        if aktuelles_datum is None:
            continue

        vereine = [text_von(z) for z in zeile.find_all("td")
                   if "club" in " ".join(z.get("class", []))]
        vereine = [v for v in vereine if v]
        if len(vereine) < 2:
            namen = [text_von(a) for a in zeile.find_all("a")
                     if "/mannschaft/" in a.get("href", "")]
            vereine = [n for n in namen if n]
        if len(vereine) >= 2:
            heim, gast = vereine[0], vereine[1]
        else:
            teile = SPIEL_MUSTER.search(adresse).group(1).split("-csc-")
            heim = teile[0].replace("-", " ").title()
            gast = ("CSC " + teile[1].replace("-", " ")).title() if len(teile) > 1 else "?"

        stunde, minute = aktuelle_zeit if aktuelle_zeit else (0, 0)
        beginn = datetime(aktuelles_datum[0], aktuelles_datum[1],
                          aktuelles_datum[2], stunde, minute)

        gefunden.append({
            "kennung": kennung,
            "beginn": beginn,
            "heim": heim,
            "gast": gast,
            "altersklasse": altersklasse,
            "wettbewerb": wettbewerb,
            "adresse": adresse,
            "ort": "",
            "zeit_bekannt": aktuelle_zeit is not None,
        })
        # Wichtig: Datum und Zeit nach jedem Spiel vergessen. Sonst wuerde
        # ein Spiel ohne eigene Datumszeile faelschlich das Datum des
        # vorherigen Spiels erben. Lieber gar kein Eintrag als ein falscher.
        aktuelles_datum = None
        aktuelle_zeit = None

    return gefunden


ORT_MUSTER = re.compile(
    r"(?:Spielst[a\u00e4]tte|Sportanlage|Platz)\s*:?\s*(.{5,120}?)(?:\n|$)")


def spielstaette_holen(spiel):
    html = seite_holen(spiel["adresse"], versuche=2)
    if not html:
        return
    suppe = BeautifulSoup(html, "html.parser")
    treffer = ORT_MUSTER.search(suppe.get_text("\n", strip=True))
    if treffer:
        spiel["ort"] = " ".join(treffer.group(1).split())
        return
    for verweis in suppe.find_all("a", href=True):
        if "maps" in verweis["href"] or "openstreetmap" in verweis["href"]:
            beschriftung = text_von(verweis)
            if len(beschriftung) > 5:
                spiel["ort"] = beschriftung
                return


def ist_jugend(spiel):
    klein = spiel["altersklasse"].lower()
    return any(wort in klein for wort in JUGEND_WOERTER)


# ---------------------------------------------------------------------------
# Kalenderdatei schreiben
# ---------------------------------------------------------------------------

ZEITZONE = """BEGIN:VTIMEZONE
TZID:Europe/Berlin
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE"""


def maskieren(text):
    if text is None:
        return ""
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def umbrechen(zeile):
    ergebnis, rest = [], ""
    for zeichen in zeile:
        if len((rest + zeichen).encode("utf-8")) > 73:
            ergebnis.append(rest)
            rest = " " + zeichen
        else:
            rest += zeichen
    ergebnis.append(rest)
    return ergebnis


def kalender_bauen(spiele, anzeigename):
    stempel = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    zeilen = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CSC Paderborn 2020//Spielplan//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:%s" % maskieren(anzeigename),
        "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    zeilen += ZEITZONE.split("\n")

    for spiel in sorted(spiele, key=lambda s: s["beginn"]):
        ende = spiel["beginn"] + timedelta(minutes=DAUER_MINUTEN)
        vorsatz = spiel["altersklasse"] or "CSC"
        titel = "%s: %s - %s" % (vorsatz, spiel["heim"], spiel["gast"])
        if not spiel["zeit_bekannt"]:
            titel += " (Anstoss offen)"

        beschreibung = []
        if spiel["wettbewerb"]:
            beschreibung.append(spiel["wettbewerb"])
        if not spiel["zeit_bekannt"]:
            beschreibung.append("Anstosszeit steht noch nicht fest.")
        if spiel["ort"]:
            beschreibung.append("Spielstaette: " + spiel["ort"])
        beschreibung.append("Stand FUSSBALL.DE: " + spiel["adresse"])

        zeilen += [
            "BEGIN:VEVENT",
            "UID:csc-%s@fussball-de" % spiel["kennung"],
            "DTSTAMP:%s" % stempel,
            "DTSTART;TZID=Europe/Berlin:%s" % spiel["beginn"].strftime("%Y%m%dT%H%M%S"),
            "DTEND;TZID=Europe/Berlin:%s" % ende.strftime("%Y%m%dT%H%M%S"),
            "SUMMARY:%s" % maskieren(titel),
            "DESCRIPTION:%s" % maskieren("\n".join(beschreibung)),
            "URL:%s" % spiel["adresse"],
            "CATEGORIES:%s" % maskieren(spiel["altersklasse"] or "CSC"),
            "STATUS:CONFIRMED",
            "TRANSP:OPAQUE",
        ]
        if spiel["ort"]:
            zeilen.append("LOCATION:%s" % maskieren(spiel["ort"]))
        for ausloeser in ERINNERUNGEN:
            zeilen += [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                "DESCRIPTION:%s" % maskieren(titel),
                "TRIGGER:%s" % ausloeser,
                "END:VALARM",
            ]
        zeilen.append("END:VEVENT")

    zeilen.append("END:VCALENDAR")

    umgebrochen = []
    for zeile in zeilen:
        umgebrochen += umbrechen(zeile)
    return "\r\n".join(umgebrochen) + "\r\n"


# ---------------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------------

def einsammeln(html, alle, grenze):
    """Wertet eine Seite aus und uebernimmt neue Spiele. Gibt Zahlen zurueck."""
    spiele = spiele_auslesen(html)
    neu = 0
    alt = 0
    for spiel in spiele:
        if spiel["beginn"] < grenze:
            alt += 1
            continue
        if spiel["kennung"] not in alle:
            neu += 1
            alle[spiel["kennung"]] = spiel
        else:
            # Der Vereinsspielplan ist die genauere Quelle: fehlende
            # Angaben aus einer zweiten Quelle ergaenzen.
            vorhanden = alle[spiel["kennung"]]
            if not vorhanden["altersklasse"] and spiel["altersklasse"]:
                vorhanden["altersklasse"] = spiel["altersklasse"]
            if not vorhanden["wettbewerb"] and spiel["wettbewerb"]:
                vorhanden["wettbewerb"] = spiel["wettbewerb"]
    return len(spiele), neu, alt


def haeufung_pruefen(spiele):
    """Warnt, wenn auffaellig viele Spiele auf denselben Tag fallen.

    Das war das Erkennungszeichen des Datumsfehlers aus Fassung 4.
    """
    zaehler = {}
    for spiel in spiele:
        tag = spiel["beginn"].date()
        zaehler[tag] = zaehler.get(tag, 0) + 1
    if not zaehler:
        return
    tag, anzahl = max(zaehler.items(), key=lambda p: p[1])
    if anzahl > 12 and anzahl > len(spiele) * 0.25:
        notiz("")
        notiz("ACHTUNG: %d von %d Spielen liegen auf dem %s."
              % (anzahl, len(spiele), tag.strftime("%d.%m.%Y")))
        notiz("Das sieht nach einem Datumsfehler aus - bitte melden.")


def main():
    notiz("Lauf am %s" % datetime.now().strftime("%d.%m.%Y %H:%M"))
    grenze = datetime.now() - timedelta(days=RUECKBLICK_TAGE)
    alle = {}

    notiz("")
    notiz("Vereinsspielplan (alle Mannschaften auf einmal):")
    for url in vereinsspielplan_adressen():
        kurz = url.split("/-/")[0].replace("https://www.fussball.de/", "")
        if "show-venues/false" in url:
            kurz += " (ohne Spielstaetten)"
        html = seite_holen(url, versuche=2)
        if not html:
            notiz("  %-40s nicht erreichbar" % kurz)
            continue
        try:
            gelesen, neu, alt = einsammeln(html, alle, grenze)
        except Exception:
            notiz("  %-40s Auswertung fehlgeschlagen" % kurz)
            notiz(traceback.format_exc())
            continue
        notiz("  %-40s %3d gelesen, %3d neu, %3d vergangen"
              % (kurz, gelesen, neu, alt))
        time.sleep(1)

    notiz("")
    notiz("Einzelabfragen zur Absicherung:")
    for name, team_id in TEAMS:
        html = seite_holen(team_adresse(team_id), versuche=2)
        if not html:
            notiz("  %-20s nicht erreichbar" % name)
            continue
        try:
            gelesen, neu, alt = einsammeln(html, alle, grenze)
        except Exception:
            notiz("  %-20s Auswertung fehlgeschlagen" % name)
            continue
        notiz("  %-20s %2d gelesen, %2d neu" % (name, gelesen, neu))
        time.sleep(1)

    spiele = list(alle.values())
    notiz("")
    notiz("Insgesamt %d kommende Spiele." % len(spiele))
    haeufung_pruefen(spiele)

    if not spiele:
        notiz("")
        notiz("Keine Spiele gefunden. Die bestehenden Kalenderdateien bleiben")
        notiz("unveraendert, damit keine Termine verschwinden.")
        with open(DIAGNOSE_DATEI, "w", encoding="utf-8") as datei:
            datei.write("\n".join(protokoll))
        return 1

    if SPIELSTAETTE_LADEN:
        offen = [s for s in spiele if not s["ort"]]
        notiz("Lade Spielstaetten fuer %d Spiele (dauert einige Minuten) ..." % len(offen))
        for spiel in offen:
            try:
                spielstaette_holen(spiel)
            except Exception:
                pass
            time.sleep(0.4)
        mit_ort = len([s for s in spiele if s["ort"]])
        notiz("Spielstaette bekannt bei %d von %d Spielen." % (mit_ort, len(spiele)))

    jugend = [s for s in spiele if ist_jugend(s)]

    with open(AUSGABE_ALLE, "w", encoding="utf-8", newline="") as datei:
        datei.write(kalender_bauen(spiele, "CSC Paderborn - alle Mannschaften"))
    notiz("Datei %s geschrieben (%d Termine)." % (AUSGABE_ALLE, len(spiele)))

    with open(AUSGABE_JUGEND, "w", encoding="utf-8", newline="") as datei:
        datei.write(kalender_bauen(jugend, "CSC Paderborn - Jugend"))
    notiz("Datei %s geschrieben (%d Termine)." % (AUSGABE_JUGEND, len(jugend)))

    with open(DIAGNOSE_DATEI, "w", encoding="utf-8") as datei:
        datei.write("\n".join(protokoll))
        datei.write("\n\nGefundene Termine:\n")
        for spiel in sorted(spiele, key=lambda s: s["beginn"]):
            datei.write("%s  %-18s %s - %s%s\n" % (
                spiel["beginn"].strftime("%d.%m.%Y %H:%M"),
                (spiel["altersklasse"] or "?")[:18],
                spiel["heim"], spiel["gast"],
                "" if spiel["zeit_bekannt"] else "   [Anstoss offen]"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
