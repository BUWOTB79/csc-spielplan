#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSC Paderborn - Jugendspielplan von FUSSBALL.DE als Kalenderdatei.

Laeuft einmal taeglich bei GitHub, holt die aktuellen Spieltermine der
drei Jugendmannschaften und schreibt sie in die Datei kalender.ics.

Bei Problemen: die Datei diagnose.txt im selben Ordner ansehen.
"""

import re
import sys
import time
import hashlib
import traceback
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# EINSTELLUNGEN - hier stehen die drei Mannschaften des CSC Paderborn.
# Die Team-IDs stammen aus den Links im Spielplan.
# ---------------------------------------------------------------------------

SAISON = "2627"

TEAMS = [
    ("E-Jugend",     "02GNQ1A98K000000VS5489B2VULC3I30"),
    ("D-Jugend",     "02EO22JI3S000000VS5489B2VSAS84KM"),
    ("D-Jugend 7er", "030T4SU0TC000000VS5489BSVSO3A7B3"),
]

# Wie lange ein Spiel im Kalender blockiert (Minuten).
DAUER_MINUTEN = 90

# Erinnerungen: Vorabend (18 Stunden vorher) und 2 Stunden vorher.
ERINNERUNGEN = ["-PT18H", "-PT2H"]

# Spielstaette von der jeweiligen Spielseite nachladen.
# Kostet pro Spiel einen zusaetzlichen Aufruf. Auf False setzen, falls es
# zu langsam wird oder Probleme macht.
SPIELSTAETTE_LADEN = True

AUSGABE_DATEI = "kalender.ics"
DIAGNOSE_DATEI = "diagnose.txt"

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
    """Ruft eine Seite ab und gibt den HTML-Text zurueck."""
    letzter_fehler = None
    for nummer in range(versuche):
        try:
            antwort = requests.get(url, headers=KOPFZEILEN, timeout=30)
            if antwort.status_code == 200:
                return antwort.text
            letzter_fehler = "HTTP %s" % antwort.status_code
        except Exception as fehler:
            letzter_fehler = str(fehler)
        time.sleep(2 + nummer * 3)
    notiz("  Abruf fehlgeschlagen (%s): %s" % (letzter_fehler, url))
    return None


def team_seiten(team_id):
    """Mehrere moegliche Adressen fuer den Spielplan einer Mannschaft.

    FUSSBALL.DE liefert die Spiele teils ueber eine Nachlade-Adresse
    (ajax), teils direkt in der Mannschaftsseite. Wir probieren beides.
    """
    return [
        "https://www.fussball.de/ajax.team.next.games/-/team-id/%s" % team_id,
        "https://www.fussball.de/ajax.team.prev.games/-/team-id/%s" % team_id,
        "https://www.fussball.de/mannschaft/-/-/saison/%s/team-id/%s"
        % (SAISON, team_id),
    ]


# ---------------------------------------------------------------------------
# Auswerten
# ---------------------------------------------------------------------------

DATUM_MUSTER = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
ZEIT_MUSTER = re.compile(r"(\d{1,2}):(\d{2})")
SPIEL_MUSTER = re.compile(r"/spiel/([A-Za-z0-9-]+)/-/spiel/([A-Z0-9]+)")


def text_von(element):
    return " ".join(element.get_text(" ", strip=True).split())


def spiele_auslesen(html, mannschaft):
    """Sucht im HTML nach Spielterminen.

    Vorgehen: Wir gehen die Tabellenzeilen durch. Eine Zeile mit Datum und
    Uhrzeit merkt sich den Termin, die darauf folgende Zeile mit einem Link
    auf ein Spiel liefert die Begegnung.
    """
    suppe = BeautifulSoup(html, "html.parser")
    gefunden = []
    aktuelles_datum = None
    aktuelle_zeit = None
    aktueller_wettbewerb = ""

    for zeile in suppe.find_all("tr"):
        inhalt = text_von(zeile)

        treffer_datum = DATUM_MUSTER.search(inhalt)
        treffer_zeit = ZEIT_MUSTER.search(inhalt)
        hat_spiel_link = zeile.find("a", href=SPIEL_MUSTER) is not None

        # Kopfzeile mit Datum und Uhrzeit
        if treffer_datum and not hat_spiel_link:
            tag, monat, jahr = (int(x) for x in treffer_datum.groups())
            aktuelles_datum = (jahr, monat, tag)
            if treffer_zeit:
                aktuelle_zeit = tuple(int(x) for x in treffer_zeit.groups())
            else:
                aktuelle_zeit = None
            teile = [t.strip() for t in inhalt.split("|")]
            if len(teile) > 1:
                aktueller_wettbewerb = teile[1]
            continue

        # Zeile mit der eigentlichen Begegnung
        if not hat_spiel_link:
            continue

        verweis = zeile.find("a", href=SPIEL_MUSTER)
        adresse = verweis.get("href", "")
        if adresse.startswith("/"):
            adresse = "https://www.fussball.de" + adresse
        spiel_kennung = SPIEL_MUSTER.search(adresse).group(2)

        # Falls Datum und Zeit in derselben Zeile stehen
        if treffer_datum:
            tag, monat, jahr = (int(x) for x in treffer_datum.groups())
            aktuelles_datum = (jahr, monat, tag)
        if treffer_zeit and aktuelle_zeit is None:
            aktuelle_zeit = tuple(int(x) for x in treffer_zeit.groups())

        if aktuelles_datum is None:
            continue

        vereine = [text_von(z) for z in zeile.find_all("td")
                   if "club" in " ".join(z.get("class", []))]
        vereine = [v for v in vereine if v]
        if len(vereine) >= 2:
            heim, gast = vereine[0], vereine[1]
        else:
            # Notloesung: Namen aus dem Link ableiten
            teile = SPIEL_MUSTER.search(adresse).group(1).split("-csc-")
            heim = teile[0].replace("-", " ").title()
            gast = ("CSC " + teile[1].replace("-", " ")).title() if len(teile) > 1 else "?"

        stunde, minute = aktuelle_zeit if aktuelle_zeit else (0, 0)
        beginn = datetime(aktuelles_datum[0], aktuelles_datum[1],
                          aktuelles_datum[2], stunde, minute)

        gefunden.append({
            "mannschaft": mannschaft,
            "kennung": spiel_kennung,
            "beginn": beginn,
            "heim": heim,
            "gast": gast,
            "wettbewerb": aktueller_wettbewerb,
            "adresse": adresse,
            "ort": "",
        })
        aktuelle_zeit = None

    return gefunden


ORT_MUSTER = re.compile(
    r"(?:Spielst[a\u00e4]tte|Sportanlage|Platz)\s*:?\s*(.{5,120}?)(?:\n|$)")


def spielstaette_holen(spiel):
    """Versucht, die Adresse der Spielstaette von der Spielseite zu lesen."""
    html = seite_holen(spiel["adresse"], versuche=2)
    if not html:
        return
    suppe = BeautifulSoup(html, "html.parser")
    text = suppe.get_text("\n", strip=True)
    treffer = ORT_MUSTER.search(text)
    if treffer:
        spiel["ort"] = " ".join(treffer.group(1).split())
        return
    for verweis in suppe.find_all("a", href=True):
        if "maps" in verweis["href"] or "openstreetmap" in verweis["href"]:
            beschriftung = text_von(verweis)
            if len(beschriftung) > 5:
                spiel["ort"] = beschriftung
                return


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
    """Lange Zeilen auf 75 Zeichen umbrechen (Vorgabe des Kalenderformats)."""
    ergebnis, rest = [], ""
    for zeichen in zeile:
        if len((rest + zeichen).encode("utf-8")) > 73:
            ergebnis.append(rest)
            rest = " " + zeichen
        else:
            rest += zeichen
    ergebnis.append(rest)
    return ergebnis


def kalender_bauen(spiele):
    stempel = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    zeilen = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//CSC Paderborn 2020//Jugendspielplan//DE",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:CSC Jugend",
        "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    zeilen += ZEITZONE.split("\n")

    for spiel in sorted(spiele, key=lambda s: s["beginn"]):
        ende = spiel["beginn"] + timedelta(minutes=DAUER_MINUTEN)
        titel = "%s: %s - %s" % (spiel["mannschaft"], spiel["heim"], spiel["gast"])

        beschreibung = []
        if spiel["wettbewerb"]:
            beschreibung.append(spiel["wettbewerb"])
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
            "CATEGORIES:%s" % maskieren(spiel["mannschaft"]),
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

def main():
    notiz("Lauf am %s" % datetime.now().strftime("%d.%m.%Y %H:%M"))
    alle = {}
    rohdaten = {}

    for mannschaft, team_id in TEAMS:
        notiz("")
        notiz("Mannschaft: %s" % mannschaft)
        gefunden_gesamt = 0

        for url in team_seiten(team_id):
            html = seite_holen(url)
            if not html:
                continue
            rohdaten[url] = html[:4000]
            try:
                spiele = spiele_auslesen(html, mannschaft)
            except Exception:
                notiz("  Auswertung fehlgeschlagen bei %s" % url)
                notiz(traceback.format_exc())
                continue
            notiz("  %s Spiele aus %s" % (len(spiele), url))
            for spiel in spiele:
                alle[spiel["kennung"]] = spiel
            gefunden_gesamt += len(spiele)
            time.sleep(1)

        if gefunden_gesamt == 0:
            notiz("  ACHTUNG: keine Spiele gefunden.")

    spiele = list(alle.values())
    notiz("")
    notiz("Insgesamt %s verschiedene Spiele." % len(spiele))

    if SPIELSTAETTE_LADEN and spiele:
        notiz("Lade Spielstaetten ...")
        for spiel in spiele:
            try:
                spielstaette_holen(spiel)
            except Exception:
                pass
            time.sleep(0.5)
        mit_ort = len([s for s in spiele if s["ort"]])
        notiz("Spielstaette gefunden bei %s von %s Spielen." % (mit_ort, len(spiele)))

    if not spiele:
        notiz("")
        notiz("Es wurden keine Spiele gefunden. Die bestehende Kalenderdatei")
        notiz("bleibt unveraendert, damit keine Termine verschwinden.")
        with open(DIAGNOSE_DATEI, "w", encoding="utf-8") as datei:
            datei.write("\n".join(protokoll))
            datei.write("\n\n===== ANFANG DER ABGERUFENEN SEITEN =====\n")
            for url, ausschnitt in rohdaten.items():
                datei.write("\n----- %s -----\n%s\n" % (url, ausschnitt))
        return 1

    inhalt = kalender_bauen(spiele)
    with open(AUSGABE_DATEI, "w", encoding="utf-8", newline="") as datei:
        datei.write(inhalt)
    notiz("Datei %s geschrieben (%s Termine)." % (AUSGABE_DATEI, len(spiele)))

    with open(DIAGNOSE_DATEI, "w", encoding="utf-8") as datei:
        datei.write("\n".join(protokoll))
        datei.write("\n\nGefundene Termine:\n")
        for spiel in sorted(spiele, key=lambda s: s["beginn"]):
            datei.write("%s  %-14s %s - %s\n" % (
                spiel["beginn"].strftime("%d.%m.%Y %H:%M"),
                spiel["mannschaft"], spiel["heim"], spiel["gast"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
