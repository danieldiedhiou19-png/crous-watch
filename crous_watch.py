"""
Surveille les nouvelles annonces de logement CROUS pour une ville donnée
sur https://trouverunlogement.lescrous.fr et envoie un message Telegram
des qu'une nouvelle annonce apparait.

Tu n'as PAS besoin de comprendre ce fichier ni de le modifier (sauf la
ville, en bas du fichier). Suis simplement le guide README.md.
"""

import json
import os
import re
import urllib.request
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Configuration (viendra des "Secrets" GitHub, voir README.md)
# ---------------------------------------------------------------------------

SEARCH_URL = "https://trouverunlogement.lescrous.fr/tools/47/search"
CITY_QUERY = os.environ.get("CROUS_CITY", "Grenoble")
STATE_FILE = Path(__file__).parent / "seen_listings.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def fetch_listings():
    """Ouvre la page de recherche, filtre sur la ville, renvoie une liste
    de dicts {id, url, text} pour chaque logement affiche."""

    listings = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale="fr-FR")
        page.set_default_timeout(60000)
        # "networkidle" ne se declenche jamais sur ce site (activite reseau
        # continue en arriere-plan) : on attend juste le chargement du DOM,
        # puis le champ de recherche lui-meme.
        page.goto(SEARCH_URL, wait_until="domcontentloaded")

        search_box = page.get_by_placeholder(re.compile("Ville", re.IGNORECASE))
        search_box.wait_for(state="visible")
        search_box.click()
        search_box.fill(CITY_QUERY)

        page.wait_for_timeout(1500)
        suggestion = page.locator("li, [role='option']").filter(has_text=CITY_QUERY).first
        if suggestion.count() > 0:
            suggestion.click()
        else:
            search_box.press("Enter")

        page.wait_for_timeout(3000)

        cards = page.locator("a[href*='/accommodations/']")
        count = cards.count()

        for i in range(count):
            link = cards.nth(i)
            href = link.get_attribute("href") or ""
            match = re.search(r"/accommodations/(\d+)", href)
            if not match:
                continue
            listing_id = match.group(1)

            container = link.locator("xpath=ancestor::li[1]")
            text = container.inner_text() if container.count() > 0 else link.inner_text()

            listings.append({
                "id": listing_id,
                "url": f"https://trouverunlogement.lescrous.fr/tools/47/accommodations/{listing_id}",
                "text": " | ".join(line.strip() for line in text.splitlines() if line.strip()),
            })

        browser.close()

    unique = {l["id"]: l for l in listings}
    return list(unique.values())


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
    listings = fetch_listings()
    print(f"{len(listings)} logement(s) trouve(s) sur le site.")

    if not listings:
        print("[!] Aucun logement trouve - verifie que les selecteurs Playwright "
              "correspondent toujours a la structure actuelle du site.")

    seen_ids = load_seen_ids()
    current_ids = {l["id"] for l in listings}
    new_listings = [l for l in listings if l["id"] not in seen_ids]

    if new_listings:
        print(f"{len(new_listings)} nouveau(x) logement(s) !")
        for l in new_listings:
            message = f"🏠 Nouveau logement CROUS a {CITY_QUERY} :\n{l['text'][:200]}\n{l['url']}"
            send_telegram(message)
    else:
        print("Aucun nouveau logement depuis la derniere verification.")

    save_seen_ids(current_ids | seen_ids)


if __name__ == "__main__":
    main()
