#!/usr/bin/env python3
"""Test : fabrique un petit GTFS factice, lance build_horaires.py, puis vérifie le résultat
avec les mêmes règles que le Worker (formats, entiers, dates, cohérence)."""
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile

GTFS = {
    "routes.txt": "route_id,route_short_name,route_type\nIDFM:C01737,H,2\nIDFM:C01727,A,2\n",
    "stops.txt": (
        "stop_id,stop_name,location_type,parent_station\n"
        "IDFM:monomodalStopPlace:462394,Paris Gare du Nord,1,\n"
        "IDFM:monomodalStopPlace:43078,Épinay - Villetaneuse,1,\n"
        "IDFM:monomodalStopPlace:43178,Persan - Beaumont,1,\n"
        "IDFM:470724,Quai PNO,0,IDFM:monomodalStopPlace:462394\n"
    ),
    "calendar.txt": (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        "S1,1,1,1,1,1,0,0,20261101,20261130\n"
    ),
    "calendar_dates.txt": (
        "service_id,date,exception_type\n"
        "S1,20261111,2\n"
        "S2,20261128,1\n"
    ),
    "trips.txt": (
        "route_id,service_id,trip_id,trip_headsign,trip_short_name\n"
        "IDFM:C01737,S1,T1,Persan,124650\n"
        "IDFM:C01737,S2,T2,Gare du Nord,125754\n"
        "IDFM:C01727,S1,T3,Autre ligne,999999\n"
    ),
    "stop_times.txt": (
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        "T1,11:00:00,11:00:00,IDFM:470724,1\n"
        "T1,11:12:00,11:13:00,IDFM:monomodalStopPlace:43078,2\n"
        "T1,12:05:00,12:05:00,IDFM:monomodalStopPlace:43178,3\n"
        "T2,24:10:00,24:10:00,IDFM:monomodalStopPlace:43178,1\n"
        "T2,25:07:30,25:07:30,IDFM:monomodalStopPlace:462394,2\n"
        "T3,10:00:00,10:00:00,IDFM:monomodalStopPlace:462394,1\n"
        "T3,10:30:00,10:30:00,IDFM:monomodalStopPlace:43178,2\n"
    ),
}

RE_HEURE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def ok(cond, msg):
    if not cond:
        print("ÉCHEC :", msg)
        sys.exit(1)


def entier(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


with tempfile.TemporaryDirectory() as d:
    zpath = os.path.join(d, "gtfs.zip")
    with zipfile.ZipFile(zpath, "w") as z:
        for nom, txt in GTFS.items():
            z.writestr(nom, "\ufeff" + txt)      # avec BOM, comme certains exports
    sortie = os.path.join(d, "h.json")
    here = os.path.dirname(os.path.abspath(__file__))
    meta = os.path.join(d, "meta.json")
    r = subprocess.run([sys.executable, os.path.join(here, "build_horaires.py"), "--gtfs", zpath, "--out", sortie, "--meta", meta],
                       capture_output=True, text=True)
    print(r.stdout.strip(), r.stderr.strip())
    ok(r.returncode == 0, "le script a échoué")
    j = json.load(open(sortie, encoding="utf-8"))
    m = json.load(open(meta, encoding="utf-8"))
    ok(m["valide_au"] == j["valide_au"] and m["trains"] == len(j["trains"]) and "cree_le" in m, "meta.json incohérent")

# mêmes contrôles que le Worker
ok(all(k in j for k in ("gares", "trains", "arrets", "services", "exceptions", "version", "cree_le", "valide_du", "valide_au")), "clés manquantes")
ok(RE_HEURE_DATE.match(j["valide_du"]) and RE_HEURE_DATE.match(j["valide_au"]), "dates de validité")
ok(len(j["trains"]) == 2, "seuls les 2 trains de la ligne H doivent rester, trouvé %d" % len(j["trains"]))
ok(len(j["gares"]) == 3, "3 gares attendues, trouvé %d" % len(j["gares"]))
for g in j["gares"]:
    ok(entier(g[0], 1, 10**6) and isinstance(g[1], str), "gare %r" % (g,))
for s in j["services"]:
    ok(entier(s[0], 1, 10**6) and re.match(r"^[01]{7}$", s[1]) and RE_HEURE_DATE.match(s[2]) and RE_HEURE_DATE.match(s[3]), "service %r" % (s,))
ids_services = set(s[0] for s in j["services"])
for e in j["exceptions"]:
    ok(e[0] in ids_services and RE_HEURE_DATE.match(e[1]) and e[2] in (1, 2), "exception %r" % (e,))
for t in j["trains"]:
    ok(entier(t[0], 1, 10**7) and len(t[1]) <= 20 and t[2] in ids_services, "train %r" % (t,))
ids_gares = set(g[0] for g in j["gares"])
for a in j["arrets"]:
    ok(entier(a[0], 1, 10**7) and entier(a[1], 0, 10000) and a[2] in ids_gares and entier(a[3], 0, 400000) and entier(a[4], 0, 400000), "arrêt %r" % (a,))

# cas particuliers
numeros = {t[1] for t in j["trains"]}
ok(numeros == {"124650", "125754"}, "numéros des trains : %r" % numeros)
t2 = [t for t in j["trains"] if t[1] == "125754"][0][0]
arr_t2 = [a for a in j["arrets"] if a[0] == t2]
ok(arr_t2[-1][3] == 25 * 3600 + 7 * 60 + 30, "horaire après minuit mal converti")
ok(arr_t2[0][1] == 0 and arr_t2[1][1] == 1, "rangs des arrêts")
ok(any(e[1] == "2026-11-11" and e[2] == 2 for e in j["exceptions"]), "exception de suppression perdue")
s2 = [s for s in j["services"] if s[1] == "0000000"]
ok(len(s2) == 1 and s2[0][2] == "2026-11-28", "service décrit uniquement par des dates")
print("OK : le fichier produit respecte le format attendu par le Worker.")
