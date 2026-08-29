"""
Surveille les nouvelles annonces de logement CROUS pour une ville donnée
sur https://trouverunlogement.lescrous.fr et envoie un message Telegram
des qu'une nouvelle annonce apparait.

Contrairement a une premiere version, ce script n'utilise PAS de
navigateur automatise : la page de resultats est en fait du HTML simple
cote serveur, donc une requete HTTP classique suffit. C'est plus rapide
et beaucoup plus fiable.

Le filtre par ville du site necessite un clic JavaScript (autocomplete),
donc on recupere TOUTE la liste des logements de France et on filtre
nous-memes ceux dont l'adresse contient la ville recherchee.

Tu n'as PAS besoin de comprendre ce fichier ni de le modifier (sauf la
ville, dans le fichier .github/workflows/crous-watch.yml). Suis
simplement le guide README.md.
"""

import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration (viendra des "Secrets"/variables GitHub, voir README.md)
# ---------------------------------------------------------------------------

BASE_URL = "https://trouverunlogement.lescrous.fr/tools/47/search"
CITY_QUERY = os.environ.get("CROUS_CITY", "Grenoble")
STATE_FILE = Path(__file__).parent / "seen_listings.json"
MAX_PAGES = 20  # garde-fou pour ne jamais boucler indefiniment

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def strip_accents(text):
    return "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    ).lower()


# ---------------------------------------------------------------------------
# Recuperation des annonces (HTML simple, pas de navigateur)
# ---------------------------------------------------------------------------

def fetch_page(page_number):
    url = f"{BASE_URL}?page={page_number}" if page_number > 1 else BASE_URL
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_listings(html):
    """Extrait les logements {id, url, text} d'une page de resultats."""
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    for link in soup.find_all("a", href=re.compile(r"/accommodations/\d+")):
        match = re.search(r"/accommodations/(\d+)", link["href"])
        if not match:
            continue
        listing_id = match.group(1)

        # On remonte de quelques niveaux pour recuperer le bloc complet
        # (prix, adresse, surface...) autour du lien.
        container = link
        for _ in range(3):
            if container.parent is not None:
                container = container.parent
        text = container.get_text(separator=" | ", strip=True)

        listings.append({
            "id": listing_id,
            "url": f"https://trouverunlogement.lescrous.fr/tools/47/accommodations/{listing_id}",
            "text": text,
        })

    # Nombre total de pages, si mentionne sur la page ("page 1 sur 3")
    total_pages = 1
    page_info = re.search(r"page\s+\d+\s+sur\s+(\d+)", soup.get_text(), re.IGNORECASE)
    if page_info:
        total_pages = int(page_info.group(1))

    return listings, total_pages


def fetch_all_listings():
    all_listings = {}
    page_number = 1
    total_pages = 1

    while page_number <= min(total_pages, MAX_PAGES):
        html = fetch_page(page_number)
        listings, total_pages = parse_listings(html)

        if not listings:
            break

        for listing in listings:
            all_listings[listing["id"]] = listing

        page_number += 1

    return list(all_listings.values())


def filter_by_city(listings, city):
    needle = strip_accents(city)
    return [l for l in listings if needle in strip_accents(l["text"])]


# ---------------------------------------------------------------------------
# Etat (deja vu / nouveau)
# ---------------------------------------------------------------------------

def load_seen_ids():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_seen_ids(ids):
    STATE_FILE.write_text(json.dumps(sorted(ids)))


# ---------------------------------------------------------------------------
# Notification Telegram
# ---------------------------------------------------------------------------

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[!] TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant. Message :")
        print(message)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": "false",
    }).encode()

    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req) as resp:
        resp.read()
    print("[+] Message Telegram envoye.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"Recherche des logements CROUS pour : {CITY_QUERY}")

    all_listings = fetch_all_listings()
    print(f"{len(all_listings)} logement(s) au total en France.")

    matching = filter_by_city(all_listings, CITY_QUERY)
    print(f"{len(matching)} logement(s) correspondant a '{CITY_QUERY}'.")

    seen_ids = load_seen_ids()
    current_ids = {l["id"] for l in matching}
    new_listings = [l for l in matching if l["id"] not in seen_ids]

    if new_listings:
        print(f"{len(new_listings)} nouveau(x) logement(s) !")
        for l in new_listings:
            message = f"🏠 Nouveau logement CROUS a {CITY_QUERY} :\n{l['text'][:250]}\n{l['url']}"
            send_telegram(message)
    else:
        print("Aucun nouveau logement depuis la derniere verification.")

    save_seen_ids(current_ids | seen_ids)


if __name__ == "__main__":
    main()
