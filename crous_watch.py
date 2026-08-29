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

def dismiss_cookie_banner(page):
    """Ferme le bandeau de cookies s'il est present (best-effort, ne bloque
    jamais le reste du script en cas d'echec)."""
    try:
        button = page.get_by_role(
            "button",
            name=re.compile(r"accepter|tout accepter|j'accepte|ok", re.IGNORECASE),
        ).first
        if button.count() > 0 and button.is_visible(timeout=3000):
            button.click(timeout=3000)
            page.wait_for_timeout(500)
    except Exception:
        pass  # pas de bandeau de cookies, ou deja ferme : on continue


def find_search_input(page):
    """Essaie plusieurs strategies pour trouver le champ de recherche de
    ville, car le site n'expose pas toujours un placeholder explicite."""

    strategies = [
        lambda: page.get_by_placeholder(re.compile("ville|résidence|lieu", re.IGNORECASE)).first,
        lambda: page.get_by_label(re.compile("ville|résidence|lieu", re.IGNORECASE)).first,
        # Repli : le premier input texte visible dans le panneau "Filtrer"
        lambda: page.locator(
            "input[type='text'], input[type='search'], input:not([type])"
        ).first,
    ]

    for strategy in strategies:
        try:
            candidate = strategy()
            candidate.wait_for(state="visible", timeout=8000)
            return candidate
        except Exception:
            continue

    return None


def fetch_listings():
    """Ouvre la page de recherche, filtre sur la ville, renvoie une liste
    de dicts {id, url, text} pour chaque logement affiche."""

    listings = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale="fr-FR")
        page.set_default_timeout(60000)
        # "networkidle" ne se declenche jamais sur ce site (activite reseau
        # continue en arriere-plan) : on attend juste le chargement du DOM.
        page.goto(SEARCH_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        dismiss_cookie_banner(page)

        search_box = find_search_input(page)

        if search_box is None:
            # On ne trouve aucun champ : on garde une preuve visuelle pour
            # pouvoir diagnostiquer sans avoir besoin d'acces au navigateur.
            try:
                Path("debug_screenshot.png").write_bytes(
                    page.screenshot(full_page=False, timeout=15000)
                )
            except Exception as e:
                print(f"[!] Capture d'ecran impossible : {e}")
            try:
                Path("debug_page.html").write_text(page.content())
            except Exception as e:
                print(f"[!] Sauvegarde du HTML impossible : {e}")
            browser.close()
            raise RuntimeError(
                "Champ de recherche introuvable. Voir debug_screenshot.png "
                "et debug_page.html (telecharges comme artefacts du run)."
            )

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

        if count == 0:
            # Toujours rien : capture de secours pour diagnostiquer.
            try:
                Path("debug_screenshot.png").write_bytes(
                    page.screenshot(full_page=False, timeout=15000)
                )
            except Exception as e:
                print(f"[!] Capture d'ecran impossible : {e}")
            try:
                Path("debug_page.html").write_text(page.content())
            except Exception as e:
                print(f"[!] Sauvegarde du HTML impossible : {e}")

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
