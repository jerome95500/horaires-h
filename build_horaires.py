#!/usr/bin/env python3
"""
Fabrique horaires-H.json (le format lu par l'appli Contrôle H) à partir du GTFS d'Île-de-France Mobilités.

Usage :
    python build_horaires.py --gtfs IDFM-gtfs.zip --out horaires-H.json
    python build_horaires.py --gtfs dossier_gtfs/ --route IDFM:C01737 --out horaires-H.json

Format produit (celui que le Worker attend) :
{
  "version": "...", "cree_le": "AAAA-MM-JJ", "valide_du": "AAAA-MM-JJ", "valide_au": "AAAA-MM-JJ",
  "gares":      [[id, nom], ...],
  "services":   [[id, "1111100", "AAAA-MM-JJ", "AAAA-MM-JJ"], ...],   # 7 chiffres : lundi..dimanche
  "exceptions": [[id_service, "AAAA-MM-JJ", 1|2], ...],               # 1 = circule, 2 = ne circule pas
  "trains":     [[id, "numéro", id_service], ...],
  "arrets":     [[id_train, rang, id_gare, arrivée_en_secondes, départ_en_secondes], ...]
}
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import zipfile
from datetime import date, datetime, timezone

ROUTE_H = "IDFM:C01737"   # ligne H (même identifiant que line:IDFM:C01737 côté PRIM)


class Source:
    """Lit les fichiers d'un GTFS, qu'il soit zippé ou déjà décompressé."""

    def __init__(self, chemin):
        self.chemin = chemin
        self.zip = zipfile.ZipFile(chemin) if os.path.isfile(chemin) else None

    def existe(self, nom):
        if self.zip:
            return nom in self.zip.namelist()
        return os.path.isfile(os.path.join(self.chemin, nom))

    def lignes(self, nom):
        if self.zip:
            flux = io.TextIOWrapper(self.zip.open(nom), encoding="utf-8-sig", newline="")
        else:
            flux = open(os.path.join(self.chemin, nom), encoding="utf-8-sig", newline="")
        with flux:
            for ligne in csv.DictReader(flux):
                yield ligne


def secondes(h):
    """« 25:07:30 » -> 90450. Les horaires après minuit dépassent 24 h dans le GTFS."""
    h, m, s = (h.strip().split(":") + ["0", "0"])[:3]
    return int(h) * 3600 + int(m) * 60 + int(s)


def date_iso(d):
    d = d.strip()
    return "%s-%s-%s" % (d[0:4], d[4:6], d[6:8])


def numero_fin(identifiant):
    m = re.search(r"(\d+)$", identifiant)
    return int(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gtfs", required=True, help="GTFS zippé ou dossier")
    ap.add_argument("--route", default=ROUTE_H, help="route_id de la ligne (défaut : %s)" % ROUTE_H)
    ap.add_argument("--out", default="horaires-H.json")
    ap.add_argument("--meta", default="", help="petit fichier meta.json (dates de validité) pour savoir sans tout télécharger si les horaires ont changé")
    a = ap.parse_args()

    src = Source(a.gtfs)
    for f in ("routes.txt", "trips.txt", "stop_times.txt", "stops.txt"):
        if not src.existe(f):
            sys.exit("Fichier manquant dans le GTFS : " + f)

    # 1. la ligne
    if not any(r["route_id"] == a.route for r in src.lignes("routes.txt")):
        sys.exit("Ligne %s introuvable dans routes.txt" % a.route)

    # 2. les trains de la ligne
    trips = {}   # trip_id -> (numéro, service_id)
    for t in src.lignes("trips.txt"):
        if t["route_id"] != a.route:
            continue
        numero = (t.get("trip_short_name") or "").strip()
        if not numero:
            fin = re.findall(r"\d{4,}", t["trip_id"])
            numero = fin[-1] if fin else t["trip_id"]
        trips[t["trip_id"]] = (numero[:20], t["service_id"])
    if not trips:
        sys.exit("Aucun train pour la ligne " + a.route)

    # 3. les arrêts de ces trains (fichier très gros : lecture au fil de l'eau)
    arrets_par_train = {}
    for st in src.lignes("stop_times.txt"):
        tid = st["trip_id"]
        if tid not in trips:
            continue
        arr = st.get("arrival_time") or st.get("departure_time")
        dep = st.get("departure_time") or st.get("arrival_time")
        if not arr or not dep:
            continue     # arrêt sans horaire : on l'ignore
        arrets_par_train.setdefault(tid, []).append((int(st["stop_sequence"]), st["stop_id"], secondes(arr), secondes(dep)))

    # 4. les gares : on remonte du quai à la gare (parent_station)
    stops = {s["stop_id"]: s for s in src.lignes("stops.txt")}

    def gare_de(stop_id):
        s = stops.get(stop_id)
        if s is None:
            return None, None
        parent = (s.get("parent_station") or "").strip()
        if parent and parent in stops:
            s = stops[parent]
        return numero_fin(s["stop_id"]), s["stop_name"].strip()

    # 5. les services (jours de circulation)
    cal = {}
    if src.existe("calendar.txt"):
        for c in src.lignes("calendar.txt"):
            jours = "".join(c[j].strip() for j in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))
            cal[c["service_id"]] = (jours, date_iso(c["start_date"]), date_iso(c["end_date"]))
    exc = {}
    if src.existe("calendar_dates.txt"):
        for e in src.lignes("calendar_dates.txt"):
            exc.setdefault(e["service_id"], []).append((date_iso(e["date"]), int(e["exception_type"])))

    utiles = sorted({s for _, s in trips.values()})
    num_service, services, exceptions = {}, [], []
    for i, sid in enumerate(utiles, 1):
        num_service[sid] = i
        if sid in cal:
            jours, d0, d1 = cal[sid]
        else:   # service décrit uniquement par des dates
            ds = [d for d, _ in exc.get(sid, [])]
            if not ds:
                continue
            jours, d0, d1 = "0000000", min(ds), max(ds)
        services.append([i, jours, d0, d1])
        for d, t in exc.get(sid, []):
            if t in (1, 2):
                exceptions.append([i, d, t])
    connus = set(sv[0] for sv in services)   # services dont on connaît les jours de circulation

    # 6. assemblage
    gares, trains, arrets = {}, [], []
    tid_num = 0
    sans_calendrier = sans_horaires = trop_courts = 0
    for tid, (numero, sid) in sorted(trips.items(), key=lambda kv: kv[0]):
        sv = num_service.get(sid)
        if sv is None or sv not in connus:
            sans_calendrier += 1
            continue
        lst = sorted(arrets_par_train.get(tid, []))
        if not lst:
            sans_horaires += 1
        lignes = []
        for _, stop_id, arr, dep in lst:
            gid, nom = gare_de(stop_id)
            if gid is None:
                continue
            if lignes and lignes[-1][0] == gid:     # deux quais de la même gare à la suite
                lignes[-1] = (gid, lignes[-1][1], dep)
                continue
            lignes.append((gid, arr, dep))
            gares[gid] = nom
        if len(lignes) < 2:
            if lst:
                trop_courts += 1
            continue
        tid_num += 1
        trains.append([tid_num, numero, sv])
        for rang, (gid, arr, dep) in enumerate(lignes):
            arrets.append([tid_num, rang, gid, arr, dep])

    if not trains:
        sys.exit("Aucun train exploitable : vérifiez le GTFS")

    debut = min(s[2] for s in services)
    fin = max(s[3] for s in services)
    out = {
        "version": "gtfs-idfm " + a.route,
        "cree_le": date.today().isoformat(),
        "valide_du": debut,
        "valide_au": fin,
        "gares": sorted([[g, n] for g, n in gares.items()], key=lambda x: x[0]),
        "services": [s for s in services if s[0] in connus],
        "exceptions": [e for e in exceptions if e[0] in connus],
        "trains": trains,
        "arrets": arrets,
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    # diagnostic : où part chaque train de trips.txt ?
    print("diagnostic : %d trains pour la ligne dans trips.txt (%d numéros distincts) ; gardés %d ; "
          "écartés : %d sans calendrier connu, %d sans horaires dans stop_times.txt, %d avec moins de 2 gares ; "
          "%d services, %d exceptions" % (
              len(trips), len({n for n, _ in trips.values()}), len(trains),
              sans_calendrier, sans_horaires, trop_courts, len(out["services"]), len(out["exceptions"])))
    if a.meta:
        with open(a.meta, "w", encoding="utf-8") as f:
            json.dump({"cree_le": out["cree_le"], "valide_du": debut, "valide_au": fin,
                       "trains": len(trains), "gares": len(gares), "arrets": len(arrets)}, f)
    print("%s : %d trains, %d gares, %d arrêts, du %s au %s (%d Ko)" % (
        a.out, len(trains), len(gares), len(arrets), debut, fin, os.path.getsize(a.out) // 1024))


if __name__ == "__main__":
    main()
