#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
scrape_deputes_france.py

Scrape Nom/Région/Email/Groupe/Circonscription pour des député·e·s
de l'Assemblée nationale française, avec retries, gestion de délai,
timeout, multithreading et tableau ASCII optionnel.

Usage:
  python3 scrape_deputes_france.py [--threads X --output path --debug
                                   --retries R --delay D --timeout T
                                   --fields "nom,email" --table
                                   --barefields --no-separator]

Examples:
  1) Par défaut (affiche tout, séquentiel, pas de tableau):
     python3 scrape_deputes_france.py

  2) Nom + email, sans labels ("barefields"):
     python3 scrape_deputes_france.py --fields nom,email --barefields

  3) Tout afficher + tableau final, multithreading (5 threads):
     python3 scrape_deputes_france.py --threads 5 --table

  4) 5 tentatives, 2 s de délai entre chaque, 15 s de timeout:
     python3 scrape_deputes_france.py --retries 5 --delay 2 --timeout 15

  5) N'afficher que l'email (barefields) sans "----------------------------------------":
     python3 scrape_deputes_france.py --fields email --barefields --no-separator
"""

import argparse
import concurrent.futures
import re
import time
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

BASE_URL: str = "https://www.assemblee-nationale.fr"
DEPUTES_URL: str = "https://www2.assemblee-nationale.fr/deputes/liste/regions"

# Exemple de « régions » structurées en <h2> sur la page
TOP_REGIONS: List[str] = [
    "Ile-de-France",
    "Provence-Alpes-Côte d'Azur",
]


def get_with_retries(
    url: str,
    max_retries: int,
    delay_between: float,
    timeout: float,
    debug: bool
) -> Optional[requests.Response]:
    """
    Effectue plusieurs tentatives (max_retries) de requête GET sur 'url'.
    Attends delay_between secondes entre chaque tentative si la précédente
    a échoué. Le timeout de la requête est fixé par 'timeout'.
    Retourne un objet Response si succès, sinon None.
    """
    for attempt in range(1, max_retries + 1):
        try:
            if debug:
                print(f"[DEBUG] Attempt {attempt}/{max_retries} fetching: {url}")
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            print(f"[ERROR] Attempt {attempt} failed for {url}: {exc}")
            if attempt < max_retries and delay_between > 0:
                if debug:
                    print(f"[DEBUG] Sleeping {delay_between}s before retrying...")
                time.sleep(delay_between)
    # Echec complet
    return None


def get_deputes_from_region(
    region_name: str,
    max_retries: int,
    delay_between: float,
    timeout: float,
    debug: bool = False
) -> Dict[str, str]:
    """
    Récupère (nom -> URL) pour tous les député·e·s figurant
    dans la région h2=region_name (page DEPUTES_URL).
    Le site est structuré en <h2>region_name</h2>, <h4 departementTitre>, <li>...
    """
    if debug:
        print(f"[DEBUG] Collecting deputies for region: {region_name}")

    resp = get_with_retries(
        DEPUTES_URL,
        max_retries=max_retries,
        delay_between=delay_between,
        timeout=timeout,
        debug=debug
    )
    if not resp:
        print(f"[ERROR] Could not fetch region page: {DEPUTES_URL}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    region_h2 = None

    # Trouver la balise <h2> correspondant à region_name
    for h2_tag in soup.find_all("h2"):
        if h2_tag.get_text(strip=True) == region_name:
            region_h2 = h2_tag
            break

    if not region_h2:
        if debug:
            print(f"[WARNING] No <h2> found for region {region_name}.")
        return {}

    deputes_map: Dict[str, str] = {}

    # Parcourir les siblings après le <h2> pour trouver <h4 class='departementTitre'>, <li>, ...
    for sibling in region_h2.next_siblings:
        if sibling.name == "h2":
            # Nouvelle région => on arrête
            break
        if sibling.name == "h4" and sibling.get("class") == ["departementTitre"]:
            # Ensuite, on cherche <li>
            for sub_sib in sibling.next_siblings:
                if sub_sib.name in ("h4", "h2"):
                    break
                if sub_sib.name == "div":
                    li_tags = sub_sib.find_all("li")
                    for li_tag in li_tags:
                        a_tag = li_tag.find("a", href=True)
                        if a_tag and a_tag["href"].startswith("/deputes/fiche/"):
                            name = a_tag.get_text(strip=True)
                            full_url = BASE_URL + a_tag["href"]
                            deputes_map[name] = full_url

    if debug:
        print(f"[DEBUG] Deputies found for {region_name}: {list(deputes_map.keys())}")
    return deputes_map


def get_depute_info(
    name: str,
    url: str,
    region: str,
    max_retries: int,
    delay_between: float,
    timeout: float,
    debug: bool = False
) -> Dict[str, Optional[str]]:
    """
    Récupère Nom, Région, Email, Groupe, Circonscription pour un député donné.
    Transforme /deputes/fiche/OMC_PAxxxxxx -> /dyn/deputes/PAxxxxxx
    Parse l'email (mailto:), le groupe (a.h4._colored.link), la circonscription...
    """
    # Extraire l'ID OMC_PAxxxxxx
    match_id = re.search(r"/deputes/fiche/OMC_PA(\d+)", url)
    if not match_id:
        if debug:
            print(f"[WARNING] Can't extract OMC_PA ID from {url}")
        return {
            "nom": name,
            "region": region,
            "email": None,
            "groupe": None,
            "circonscription": None,
        }
    deputy_id = f"PA{match_id.group(1)}"
    dyn_url = f"{BASE_URL}/dyn/deputes/{deputy_id}"

    resp = get_with_retries(
        dyn_url, max_retries, delay_between, timeout, debug
    )
    if not resp:
        if debug:
            print(f"[ERROR] Could not fetch {dyn_url} after retries.")
        return {
            "nom": name,
            "region": region,
            "email": None,
            "groupe": None,
            "circonscription": None,
        }

    soup = BeautifulSoup(resp.text, "html.parser")

    # Email
    a_mail = soup.find("a", href=re.compile(r"^mailto:"))
    email = a_mail["href"].replace("mailto:", "") if a_mail else None
    if debug:
        print(f"[DEBUG] Email for {name} => {email}")

    # Groupe
    group_tag = soup.find("a", class_="h4 _colored link")
    group = group_tag.get_text(strip=True) if group_tag else None

    # Circonscription
    circ_div = soup.find("div", class_="_mb-small._centered-text")
    # Correction : s'il y a un point dans la classe => on retente
    if not circ_div:
        circ_div = soup.find("div", class_="_mb-small _centered-text")
    circonscription = None
    if circ_div:
        big_span = circ_div.find("span", class_="_big")
        if big_span:
            circonscription = big_span.get_text(strip=True)

    return {
        "nom": name,
        "region": region,
        "email": email,
        "groupe": group,
        "circonscription": circonscription,
    }


def build_ascii_table(
    results: List[Dict[str, Optional[str]]],
    fields: List[str]
) -> str:
    """
    Construit un tableau ASCII récapitulatif, colonnes = fields.
    """
    header = [field.capitalize() for field in fields]
    rows = [header]

    # Ajoute les données
    for dep in results:
        row = [dep.get(f, "") or "" for f in fields]
        rows.append(row)

    # Largeur max pour chaque colonne
    col_widths: List[int] = []
    for c in range(len(fields)):
        col_widths.append(
            max(len(str(rows[r][c])) for r in range(len(rows)))
        )

    # Construction du tableau
    lines: List[str] = []
    for i, row in enumerate(rows):
        cells = [str(cell).ljust(col_widths[c]) for c, cell in enumerate(row)]
        line = " | ".join(cells)
        lines.append(line)
        if i == 0:
            sep = "-+-".join("-" * w for w in col_widths)
            lines.append(sep)

    return "\n".join(lines)


def scrape_deputes(
    multithreading: bool = False,
    max_threads: int = 5,
    output_file: Optional[str] = None,
    debug: bool = False,
    retries: int = 3,
    delay: float = 0.0,
    req_timeout: float = 10.0,
    fields: Optional[List[str]] = None,
    use_table: bool = False,
    barefields: bool = False,
    no_separator: bool = False,
) -> None:
    """
    Scrape Nom, Région, Email, Groupe, Circonscription pour les régions.
    Si --barefields + 1 champ + --no-separator => pas de ligne de tirets.
    """
    if fields is None:
        fields = ["nom", "region", "email", "groupe", "circonscription"]

    # 1) Collecte
    deputes_data: List[tuple] = []
    for region in TOP_REGIONS:
        region_map = get_deputes_from_region(
            region,
            max_retries=retries,
            delay_between=delay,
            timeout=req_timeout,
            debug=debug
        )
        for dep_name, dep_url in region_map.items():
            deputes_data.append((dep_name, dep_url, region))

    if debug:
        print(f"[DEBUG] Found {len(deputes_data)} deputies total.")

    # 2) Récup infos
    results: List[Dict[str, Optional[str]]] = []
    if multithreading:
        if debug:
            print(f"[DEBUG] Using multithreading with {max_threads} workers.")
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
            future_map = {
                executor.submit(
                    get_depute_info,
                    dep_name,
                    dep_url,
                    dep_region,
                    retries,
                    delay,
                    req_timeout,
                    debug
                ): (dep_name, dep_url, dep_region)
                for (dep_name, dep_url, dep_region) in deputes_data
            }
            for future in concurrent.futures.as_completed(future_map):
                results.append(future.result())
    else:
        if debug:
            print("[DEBUG] Running sequentially.")
        for (dep_name, dep_url, dep_region) in deputes_data:
            info = get_depute_info(
                dep_name, dep_url, dep_region,
                retries, delay, req_timeout, debug
            )
            results.append(info)

    # 3) Format
    lines: List[str] = []
    # Condition : 1 seul champ, barefields, no_separator => pas de lignes de tirets
    skip_separators: bool = (
        barefields and len(fields) == 1 and no_separator
    )

    for dep in results:
        for field in fields:
            val: str = dep.get(field, "") or ""
            if barefields:
                lines.append(val)
            else:
                lines.append(f"{field.capitalize()}: {val}")
        if not skip_separators:
            lines.append("-" * 40)

    # 4) Tableau si besoin
    ascii_table: str = ""
    if use_table:
        ascii_table = "\n\n=== TABLEAU RÉCAPITULATIF ===\n"
        ascii_table += build_ascii_table(results, fields)
        ascii_table += "\n"

    final_output: str = "\n".join(lines) + ascii_table

    # 5) Output
    if output_file:
        with open(output_file, "w", encoding="utf-8") as file_out:
            file_out.write(final_output)
        if debug:
            print(f"[DEBUG] Results saved to {output_file}")
    else:
        print(final_output)


def main() -> None:
    """
    Point d'entrée principal.
    Analyse des arguments CLI et lancement de la fonction scrape_deputes.
    """
    parser = argparse.ArgumentParser(
        description="Scrape Nom/Région/Email/Groupe/Circonscription + ASCII table."
    )
    parser.add_argument("--threads", type=int, default=1,
                        help="Threads à utiliser (1 => séquentiel).")
    parser.add_argument("--output", type=str,
                        help="Fichier de sortie.")
    parser.add_argument("--debug", action="store_true",
                        help="Active le mode debug.")
    parser.add_argument("--retries", type=int, default=3,
                        help="Nombre de tentatives par requête (3 par défaut).")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="Délai (s) entre tentatives (0 par défaut).")
    parser.add_argument("--timeout", type=float, default=10.0,
                        help="Timeout (s) des requêtes (10 par défaut).")
    parser.add_argument("--fields", type=str,
                        help="Liste de champs, séparés par virgules (ex: nom,email). Par défaut tous.")
    parser.add_argument("--table", action="store_true",
                        help="Génère un tableau ASCII récapitulatif en plus.")
    parser.add_argument("--barefields", action="store_true",
                        help="Sans 'Nom:' ni 'Email:', juste les valeurs.")
    parser.add_argument("--no-separator", action="store_true",
                        help="Si --barefields + 1 champ, retire la ligne de tirets.")

    args = parser.parse_args()
    use_threads: bool = (args.threads > 1)

    # Parse fields
    if args.fields:
        selected_fields: List[str] = [f.strip() for f in args.fields.split(",")]
    else:
        selected_fields = None

    scrape_deputes(
        multithreading=use_threads,
        max_threads=args.threads,
        output_file=args.output,
        debug=args.debug,
        retries=args.retries,
        delay=args.delay,
        req_timeout=args.timeout,
        fields=selected_fields,
        use_table=args.table,
        barefields=args.barefields,
        no_separator=args.no_separator
    )


if __name__ == "__main__":
    main()

