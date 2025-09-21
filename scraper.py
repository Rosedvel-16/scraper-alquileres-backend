# ----------- Librerías ----------
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import pandas as pd
import re
import time
import os
import requests
import unicodedata
import json
import logging
from typing import List, Dict, Optional
from datetime import datetime
import uuid

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------- Helpers -----------
EXCEPCIONES = ["miraflores", "tarapoto", "la molina", "magdalena", "lambayeque", "ventanilla", "la victoria"]

def normalize_text(text):
    """Elimina acentos y pasa a minúsculas"""
    if not text:
        return ""
    return unicodedata.normalize('NFKD', text.lower()).encode('ASCII','ignore').decode('utf-8')

def build_zona_slug_nestoria(zona_input: str) -> str:
    z = zona_input.strip().lower().replace(" ", "-")
    if z not in [e.lower() for e in EXCEPCIONES]:
        return z
    else:
        return "lima_" + z

def parse_precio_con_moneda(precio_str):
    if not precio_str:
        return (None, None)
    s = precio_str.strip()
    if "S/" in s or "S/." in s or s.startswith("S/") or s.startswith("S/."):
        moneda = "S"
    elif "$" in s:
        moneda = "USD"
    else:
        moneda = None
    nums = re.sub(r"[^\d]", "", s)
    if nums == "":
        return (moneda, None)
    try:
        return (moneda, int(nums))
    except:
        return (moneda, None)

# ---------- Configuración del Driver ----------
COMMON_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"

def create_driver(headless=True):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument(f"user-agent={COMMON_UA}")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--remote-debugging-port=9222")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """
        })
    except:
        pass
    return driver

# ---------- Scrapers Individuales ----------

def scrape_nestoria(zona, dormitorios="0", banos="0", price_min=None, price_max=None, max_results=200, strict_zone=True):
    try:
        zona_slug = build_zona_slug_nestoria(zona)
        base_url = f"https://www.nestoria.pe/{zona_slug}/inmuebles/alquiler"
        if dormitorios and dormitorios != "0":
            base_url += f"/dormitorios-{dormitorios}"
        params = []
        if banos and banos != "0":
            params.append(f"bathrooms={banos}")
        if price_min and str(price_min) != "0":
            params.append(f"price_min={price_min}")
        if price_max and str(price_max) != "0":
            params.append(f"price_max={price_max}")
        if params:
            base_url += "?" + "&".join(params)
        logger.info(f"🔎 Consultando Nestoria: {base_url}")
        headers = {"User-Agent": COMMON_UA}
        r = requests.get(base_url, headers=headers, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select("ul#main_listing_res > li")
        if not items:
            items = soup.select("li.rating__new")
        if not items:
            items = [li for li in soup.find_all("li") if li.select_one(".result__details__price")]
        listings = []
        skipped_usd = 0
        for i, li in enumerate(items):
            if i >= max_results:
                break
            a_tag = li.select_one("a.results__link") or li.select_one("a.results__link")
            title = link = None
            if a_tag:
                link = a_tag.get("data-href") or a_tag.get("href")
                if link and link.startswith("/"):
                    link = "https://www.nestoria.pe" + link
                title = a_tag.get_text(strip=True)
            if not title:
                title = (li.select_one(".listing__title__text") or li.select_one(".listing__title") or li.get_text(" ", strip=True))[:120]
            price_el = li.select_one(".result__details__price span") or li.select_one(".result__details__price") or li.select_one(".price")
            price_text = price_el.get_text(strip=True) if price_el else ""
            moneda, precio_val = parse_precio_con_moneda(price_text)
            if price_max is not None and moneda=="S" and precio_val is not None and precio_val>price_max:
                continue
            if price_min is not None and moneda=="S" and precio_val is not None and precio_val<price_min:
                continue
            if moneda=="USD" and (price_max is not None or price_min is not None):
                skipped_usd += 1
                continue
            text = li.get_text(" ", strip=True)
            area_match = re.search(r"(\d{1,4}\s*m²|\d{1,4}\s*m2)", text)
            area = area_match.group(0) if area_match else ""
            bd = re.search(r"(\d+)\s*dormitori", text, flags=re.I)
            bedrooms = bd.group(1) if bd else ""
            bt = re.search(r"(\d+)\s*bañ", text, flags=re.I)
            bathrooms = bt.group(1) if bt else ""
            desc = (li.select_one(".listing__description") or li.select_one(".result__summary") or None)
            desc_text = desc.get_text(strip=True) if desc else ""

            # >>> NUEVO: Extraer URL de la imagen <<<
            imagen_url = ""
            img_tag = li.select_one("img")
            if img_tag:
                imagen_url = img_tag.get("src") or img_tag.get("data-original") or img_tag.get("data-src") or ""
                if imagen_url.startswith("//"):
                    imagen_url = "https:" + imagen_url

            if strict_zone:
                if zona.lower() not in title.lower() and zona.lower() not in desc_text.lower():
                    continue
            listings.append({
                "titulo": title,
                "precio": price_text,
                "m2": area,
                "dormitorios": bedrooms,
                "baños": bathrooms,
                "descripcion": desc_text,
                "link": link or "",
                "fuente": "nestoria",
                "imagen_url": imagen_url  # 👈 NUEVA COLUMNA
            })
        df = pd.DataFrame(listings)
        return df, skipped_usd
    except Exception as e:
        logger.error(f"Error en scrape_nestoria: {e}")
        return pd.DataFrame(), 0

def slugify_zone(zona: str) -> str:
    if not zona:
        return ""
    z = zona.strip().lower()
    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "ü": "u"
    }
    for a, b in replacements.items():
        z = z.replace(a, b)
    z = re.sub(r"\s+", "-", z)
    z = re.sub(r"[^a-z0-9\-]", "", z)
    return z

def scrape_infocasas(zona, dormitorios="0", banos="0", price_min=None, price_max=None, strict_zone=True, max_scrolls=8):
    try:
        LIMA_DISTRICTS = [
            "barranco", "breña", "carabayllo", "chaclacayo", "chorrillos", "cieneguilla", "comas",
            "el agustino", "independencia", "jesus maria", "la molina", "la victoria", "lince",
            "los olivos", "lurigancho", "lurin", "magdalena del mar", "miraflores", "pachacamac",
            "pucusana", "puente piedra", "punta hermosa", "punta negra", "rimac", "san bartolo",
            "san borja", "san isidro", "san juan de lurigancho", "san juan de miraflores", "san luis",
            "san martin de porres", "san miguel", "santa anita", "santa maria del mar", "santa rosa",
            "santiago de surco", "surco", "villa el salvador", "villa maria del triunfo"
        ]
        CALLAO_DISTRICTS = [
            "callao", "bellavista", "la perla", "la punta", "ventanilla", "pedro miguel"
        ]
        zona_slug = slugify_zone(zona)
        zona_norm = normalize_text(zona.strip())
        driver = create_driver(headless=True)
        is_lima_district = zona_norm in LIMA_DISTRICTS
        is_callao_district = zona_norm in CALLAO_DISTRICTS
        candidate_urls = []
        if is_lima_district:
            candidate_urls.append(f"https://www.infocasas.com.pe/alquiler/casas-y-departamentos/lima/{zona_slug}")
            candidate_urls.append(f"https://www.infocasas.com.pe/alquiler/casas-y-departamentos/lima/{zona_slug}/lima")
            candidate_urls.append(f"https://www.infocasas.com.pe/alquiler/casas-y-departamentos/lima/{zona_slug}")
        elif is_callao_district:
            candidate_urls.append(f"https://www.infocasas.com.pe/alquiler/casas-y-departamentos/callao/{zona_slug}")
            candidate_urls.append(f"https://www.infocasas.com.pe/alquiler/casas-y-departamentos/callao/{zona_slug}/callao")
            candidate_urls.append(f"https://www.infocasas.com.pe/alquiler/casas-y-departamentos/callao/{zona_slug}")
        else:
            candidate_urls.append(f"https://www.infocasas.com.pe/alquiler/casas-y-departamentos/{zona_slug}")
        candidate_urls.append(f"https://www.infocasas.com.pe/alquiler/casas-y-departamentos/lima/{zona_slug}")
        candidate_urls.append(f"https://www.infocasas.com.pe/alquiler/casas-y-departamentos/lima/{zona_slug}/lima")
        candidate_urls.append(f"https://www.infocasas.com.pe/alquiler/casas-y-departamentos/{zona_slug}/callao")
        candidate_urls.append(f"https://www.infocasas.com.pe/alquiler/casas-y-departamentos/lima/{zona_slug}")
        candidate_urls.append(f"https://www.infocasas.com.pe/alquiler/casas-y-departamentos/callao/{zona_slug}")
        candidate_urls = list(dict.fromkeys(candidate_urls))
        soup = None
        anchors = []
        used_url = None
        for url in candidate_urls:
            try:
                logger.info(f"🔎 Intentando Infocasas: {url}")
                driver.get(url)
                for _ in range(max_scrolls):
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(0.8)
                try:
                    WebDriverWait(driver, 8).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a.lc-data")))
                    anchors = driver.find_elements(By.CSS_SELECTOR, "a.lc-data")
                except:
                    page = driver.page_source
                    soup = BeautifulSoup(page, "html.parser")
                    possible = []
                    for sel in ["a.lc-data", "li.lc-item", "div.listingCard", "div.listingBoxCard", "article", "div.card"]:
                        found = soup.select(sel)
                        if found and len(found) > 0:
                            possible = found
                            break
                    if possible:
                        anchors = []
                        for el in possible:
                            a = el.select_one("a[href]") or el.select_one("a.lc-data")
                            href = a.get("href") if a else ""
                            anchors.append({"href": href, "html": str(el), "title": (a.get("title") if a and a.get("title") else (el.get_text(" ", strip=True)[:80]))})
                    else:
                        anchors = []
                if anchors and (hasattr(anchors[0], "get_attribute") or isinstance(anchors[0], dict)):
                    results_in_zone = []
                    for item in anchors:
                        try:
                            location = ""
                            if isinstance(item, dict):
                                html = item.get("html", "")
                                soup = BeautifulSoup(html, "html.parser")
                                loc_el = soup.select_one(".lc-location")
                                if loc_el:
                                    location = loc_el.get_text(strip=True)
                            else:
                                try:
                                    loc_el = item.find_element(By.CSS_SELECTOR, ".lc-location")
                                    location = loc_el.text
                                except:
                                    pass
                            loc_norm = normalize_text(location)
                            if strict_zone and zona_norm not in loc_norm:
                                continue
                            results_in_zone.append(item)
                        except Exception as e:
                            continue
                    if results_in_zone:
                        anchors = results_in_zone
                        used_url = url
                        break
            except Exception as e:
                continue
        if not anchors:
            try:
                page = driver.page_source
                soup = BeautifulSoup(page, "html.parser")
                possible = soup.select("a.lc-data") or soup.select("li.lc-item") or soup.select("div.listingCard") or soup.select("div.listingBoxCard") or soup.select("article")
                anchors = []
                for el in possible:
                    a = el.select_one("a[href]") or el.select_one("a.lc-data")
                    href = a.get("href") if a else ""
                    anchors.append({"href": href, "html": str(el), "title": (a.get("title") if a and a.get("title") else (el.get_text(" ", strip=True)[:80]))})
                used_url = "fallback_page_source"
            except Exception:
                anchors = []
        results = []
        if anchors and hasattr(anchors[0], "get_attribute"):
            for a in anchors:
                try:
                    href = a.get_attribute("href") or a.get_attribute("data-href") or ""
                    title = a.get_attribute("title") or ""
                    try:
                        if not title and len(a.find_elements(By.CSS_SELECTOR, "h2.lc-title")):
                            title = a.find_element(By.CSS_SELECTOR, "h2.lc-title").text
                    except:
                        pass
                    location = ""
                    try:
                        loc_el = a.find_element(By.CSS_SELECTOR, ".lc-location")
                        location = loc_el.text
                    except:
                        pass
                    price = ""
                    try:
                        price = a.find_element(By.CSS_SELECTOR, "p.main-price").text
                    except:
                        try:
                            price = a.find_element(By.CSS_SELECTOR, ".main-price").text
                        except:
                            price = ""
                    dorms = baths = m2 = ""
                    try:
                        tags = a.find_elements(By.CSS_SELECTOR, ".lc-typologyTag__item strong")
                        for t in tags:
                            txt = t.text.lower()
                            if "dorm" in txt:
                                m = re.search(r"(\d+)", txt); dorms = m.group(1) if m else t.text
                            elif "bañ" in txt:
                                m = re.search(r"(\d+)", txt); baths = m.group(1) if m else t.text
                            elif "m" in txt:
                                m2 = t.text
                    except:
                        pass
                    desc = ""
                    try:
                        desc = a.find_element(By.CSS_SELECTOR, "p.lc-description").text
                    except:
                        desc = ""

                    # >>> NUEVO: Extraer imagen <<<
                    imagen_url = ""
                    try:
                        img_el = a.find_element(By.CSS_SELECTOR, "img")
                        imagen_url = img_el.get_attribute("src") or img_el.get_attribute("data-src") or ""
                    except:
                        pass

                    if strict_zone and zona_norm not in normalize_text(location):
                        continue
                    results.append({
                        "titulo": title or "",
                        "precio": price or "",
                        "m2": m2 or "",
                        "dormitorios": dorms or "",
                        "baños": baths or "",
                        "descripcion": desc or "",
                        "link": href if href.startswith("http") else ("https://www.infocasas.com.pe/" + href if href.startswith("/") else href),
                        "ubicacion": location,
                        "fuente": "infocasas",
                        "imagen_url": imagen_url  # 👈 NUEVA COLUMNA
                    })
                except Exception:
                    continue
        else:
            for item in anchors:
                try:
                    if isinstance(item, dict):
                        href = item.get("href","") or ""
                        title = item.get("title","") or ""
                        el = BeautifulSoup(item.get("html",""), "html.parser") if item.get("html") else None
                    else:
                        el = item
                        a = el.select_one("a[href]") or el.select_one("a.lc-data")
                        href = a.get("href") if a else ""
                        title = a.get("title") if a and a.get("title") else (el.get_text(" ", strip=True)[:80])
                    location = ""
                    loc_el = el.select_one(".lc-location")
                    if loc_el:
                        location = loc_el.get_text(strip=True)
                    price = ""
                    if el:
                        p = el.select_one("p.main-price") or el.select_one(".main-price") or el.select_one(".content_result_precio")
                        if p:
                            price = p.get_text(" ", strip=True)
                        text = el.get_text(" ", strip=True)
                        m2m = re.search(r"\d{1,4}\s*(m²|m2)", text)
                        bedrooms = re.search(r"(\d+)\s*dormitori", text, flags=re.I) or re.search(r"(\d+)\s*hab", text, flags=re.I)
                        bathrooms = re.search(r"(\d+)\s*bañ", text, flags=re.I)
                        m2 = m2m.group(0) if m2m else ""
                        bedrooms_v = bedrooms.group(1) if bedrooms else ""
                        bathrooms_v = bathrooms.group(1) if bathrooms else ""
                        desc = el.select_one("p.lc-description") or el.select_one(".content_result_specs") or None
                        desc_txt = desc.get_text(" ", strip=True) if desc else text[:200]
                    else:
                        price = ""
                        m2 = ""
                        bedrooms_v = ""
                        bathrooms_v = ""
                        desc_txt = ""

                    # >>> NUEVO: Extraer imagen <<<
                    imagen_url = ""
                    if el:
                        img = el.select_one("img")
                        if img:
                            imagen_url = img.get("src") or img.get("data-src") or ""

                    if strict_zone and zona_norm not in normalize_text(location):
                        continue
                    results.append({
                        "titulo": title or "",
                        "precio": price or "",
                        "m2": m2 or "",
                        "dormitorios": bedrooms_v or "",
                        "baños": bathrooms_v or "",
                        "descripcion": desc_txt or "",
                        "link": href if href.startswith("http") else ("https://www.infocasas.com.pe/" + href if href.startswith("/") else href),
                        "ubicacion": location,
                        "fuente": "infocasas",
                        "imagen_url": imagen_url  # 👈 NUEVA COLUMNA
                    })
                except Exception:
                    continue
        df = pd.DataFrame(results)
        return df
    except Exception as e:
        logger.error(f"Error en scrape_infocasas: {e}")
        return pd.DataFrame()
    finally:
        driver.quit()

# ---------- Adapter Doomos (versión probada y funcional) ----------

def scrape_doomos_general(zona, dormitorios=0, banos=0, price_min=None, price_max=None):
    zona_norm = (zona or "").strip()
    if not zona_norm:
        print("❌ Debes indicar una zona")
        return pd.DataFrame()
    headers = {"User-Agent": COMMON_UA}
    # Detectar loc_name y loc_id
    search_init = f"http://www.doomos.com.pe/search/?clase=1&stipo=16&loc_name={requests.utils.quote(zona_norm)}"
    try:
        r = requests.get(search_init, headers=headers, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print("⚠️ Error al cargar Doomos:", e)
        return pd.DataFrame()
    soup = BeautifulSoup(r.text, "html.parser")
    loc_id_el = soup.select_one("input[name='loc_id']")
    loc_id = loc_id_el.get("value") if loc_id_el else None
    if not loc_id:
        m = re.search(r"loc_id\s*[:=]\s*([-]?\d+)", r.text)
        if m:
            loc_id = m.group(1)
    if not loc_id:
        return pd.DataFrame()
    loc_name_el = soup.select_one("input[name='loc_name']")
    loc_name_final = loc_name_el.get("value") if loc_name_el else zona_norm
    params = {
        "pagina": "1",
        "sort": "primeasc",
        "provincia": "15",
        "clase": "1",
        "stipo": "16",
        "loc_name": loc_name_final,
        "loc_id": loc_id,
        "preciomin": str(price_min) if price_min is not None else "min",
        "preciomax": str(price_max) if price_max is not None else "max"
    }
    if dormitorios:
        params["piezas"] = str(dormitorios)
    if banos:
        params["banos"] = str(banos)
    url_final = "http://www.doomos.com.pe/search/?" + "&".join(f"{k}={requests.utils.quote(str(v))}" for k,v in params.items())
    try:
        r2 = requests.get(url_final, headers=headers, timeout=20)
        r2.raise_for_status()
    except Exception as e:
        print("⚠️ Error cargando anuncios Doomos:", e)
        return pd.DataFrame()
    soup2 = BeautifulSoup(r2.text, "html.parser")
    cards = soup2.select(".content_result")
    results = []
    for card in cards:
        try:
            # Obtener el texto completo del card
            card_text = card.get_text(" ", strip=True)
            # Extraer información del título
            a = card.select_one(".content_result_titulo a")
            titulo = a.get_text(strip=True) if a else ""
            link = a.get("href") if a else ""
            if link and link.startswith("/"):
                link = "http://www.doomos.com.pe" + link
            # Si no tiene título o link, saltar este anuncio
            if not titulo or not link:
                continue
            # Extraer precio - SOLO el valor monetario
            price_el = card.select_one(".content_result_precio")
            price_text = price_el.get_text(" ", strip=True) if price_el else ""
            # Regex para extraer solo el precio (S/ XXXX o $ XXXX)
            price_match = re.search(r"(S/\.?|\$)\s*\d{1,4}(?:\.\d{1,3})?", price_text)
            if price_match:
                price_text = price_match.group(0).strip()
            else:
                # Si no encontramos el formato S/ XXXX, intentamos con el regex más general
                price_match = re.search(r"\d{1,4}(?:\.\d{1,3})?", price_text)
                if price_match:
                    price_text = "S/ " + price_match.group(0)
                else:
                    price_text = ""

            # Buscar dormitorios en el texto completo del card
            bedrooms = 0
            bd = re.search(r"(\d+)\s*(?:hab\.?|habitaci[oó]n|dorm|habitacion)", card_text, flags=re.I)
            if bd:
                bedrooms = int(bd.group(1))
            # Buscar baños en el texto completo del card
            bathrooms = 0
            bt = re.search(r"(\d+)\s*(?:bañ\.?|baños|bano)", card_text, flags=re.I)
            if bt:
                bathrooms = int(bt.group(1))
            # Buscar m2 en el texto completo del card
            m2 = ""
            m2_match = re.search(r"(\d{1,4})\s*(m²|m2|m)", card_text, flags=re.I)
            if m2_match:
                m2 = m2_match.group(0)

            # Filtrar por dormitorios y baños si se especificaron
            if dormitorios and bedrooms != dormitorios:
                continue
            if banos and bathrooms != banos:
                continue

            # >>> NUEVO: Extraer imagen <<<
            imagen_url = ""
            img_tag = card.select_one("img")
            if img_tag:
                imagen_url = img_tag.get("src") or img_tag.get("data-src") or ""
                if imagen_url.startswith("//"):
                    imagen_url = "https:" + imagen_url
                elif imagen_url.startswith("/"):
                    imagen_url = "http://www.doomos.com.pe" + imagen_url

            results.append({
                "titulo": titulo,
                "precio": price_text or "",
                "m2": m2 or "",
                "dormitorios": bedrooms,
                "baños": bathrooms,
                "descripcion": card_text,  # Usar todo el texto como descripción
                "link": link,
                "fuente": "doomos",
                "imagen_url": imagen_url  # 👈 NUEVA COLUMNA
            })
        except Exception as e:
            print(f"Error procesando card: {e}")
            continue
    return pd.DataFrame(results)

def scrape_doomos_brena(zona, dormitorios=0, banos=0):
    headers = {"User-Agent": COMMON_UA}
    zona_norm = normalize_text(zona.strip())
    search_url = f"http://www.doomos.com.pe/search/?clase=1&stipo=16&loc_name={requests.utils.quote(zona)}"
    try:
        r = requests.get(search_url, headers=headers, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"Error fetching Doomos Breña: {e}")
        return pd.DataFrame()
    soup = BeautifulSoup(r.text, "html.parser")
    cards = soup.select(".content_result")
    results = []
    for card in cards:
        try:
            a = card.select_one(".content_result_titulo a") or card.select_one(".content_result_titulo_bold a")
            titulo = a.get_text(strip=True) if a else ""
            link = a.get("href") if a else ""
            if link and link.startswith("/"):
                link = "http://www.doomos.com.pe" + link
            # Si no tiene título o link, saltar este anuncio
            if not titulo or not link:
                continue
            price_el = card.select_one(".content_result_precio")
            price_text_full = price_el.get_text(" ", strip=True) if price_el else ""
            # EXTRAER SOLO EL PRECIO USANDO EXPRESIÓN REGULAR
            price_match = re.search(r"(S/\.?|\$)\s*\d{1,4}(?:\.\d{1,3})?", price_text_full)
            if price_match:
                price_text = price_match.group(0)
            else:
                price_text = ""
            deta_el = card.select_one(".content_result_precio .content_result_deta")
            deta_text = deta_el.get_text(" ", strip=True) if deta_el else ""
            desc_el = card.select_one(".content_result_text .content_result_specs")
            descripcion = desc_el.get_text(" ", strip=True) if desc_el else ""
            resu_el = card.select_one(".content_result_specs_resu")
            resumen = resu_el.get_text(" ", strip=True) if resu_el else ""
            combined_text = " ".join([titulo, descripcion, resumen])
            combined_norm = normalize_text(combined_text)
            if zona_norm not in combined_norm:
                continue
            # Extraer dormitorios
            bd_match = re.search(r"(\d+)\s*hab", deta_text, flags=re.I) or re.search(r"(\d+)\s*dorm", deta_text, flags=re.I)
            bedrooms = int(bd_match.group(1)) if bd_match else 0
            # Extraer baños
            bt_match = re.search(r"(\d+)\s*bañ", deta_text, flags=re.I)
            bathrooms = int(bt_match.group(1)) if bt_match else 0
            # Extraer metros cuadrados
            m2_match = re.search(r"(\d{1,4})\s*(m²|m2|m)", deta_text, flags=re.I)
            m2 = m2_match.group(0) if m2_match else ""

            # Filtrar por dormitorios y baños si se especificaron
            if dormitorios and bedrooms != dormitorios:
                continue
            if banos and bathrooms != banos:
                continue

            # >>> NUEVO: Extraer imagen <<<
            imagen_url = ""
            img_tag = card.select_one("img")
            if img_tag:
                imagen_url = img_tag.get("src") or img_tag.get("data-src") or ""
                if imagen_url.startswith("//"):
                    imagen_url = "https:" + imagen_url
                elif imagen_url.startswith("/"):
                    imagen_url = "http://www.doomos.com.pe" + imagen_url

            results.append({
                "titulo": titulo,
                "precio": price_text,
                "m2": m2,
                "dormitorios": bedrooms,
                "baños": bathrooms,
                "descripcion": descripcion,
                "link": link,
                "fuente": "doomos",
                "imagen_url": imagen_url  # 👈 NUEVA COLUMNA
            })
        except Exception as e:
            print(f"Error procesando card en scrape_doomos_brena: {e}")
            continue
    return pd.DataFrame(results)

# ---------- scrape_properati (nueva fuente) ----------

def scrape_properati(zona, dormitorios="0", banos="0", price_min=None, price_max=None):
    """
    Scraping de Properati.com.pe con transformación inteligente de nombre de zona.
    Soporta: "Cercado de Lima" → "lima-cercado", "San Juan de Lurigancho" → "san-juan-de-lurigancho"
    No permite resultados parciales (ej: solo "Lima" si se pide "Cercado de Lima").
    """
    if not zona or not zona.strip():
        return pd.DataFrame()

    # Normalizar entrada para evitar espacios extra
    zona_input = zona.strip()

    def transform_zona_for_url(zona_original):
        """
        Transforma el nombre de la zona para generar la URL correcta de Properati.
        Reglas:
        - Si contiene " de " y termina en "Lima": invertir orden y eliminar "de" → "lima-cercado"
        - Si contiene " de " pero NO termina en Lima: mantener " de " → "san-juan-de-lurigancho"
        - Si no tiene "de": usar tal cual
        - Si es una sola palabra: usar tal cual
        """
        zona = zona_original.strip()
        if not zona:
            return ""
        # Caso 1: Una sola palabra → devolver tal cual
        if len(zona.split()) == 1:
            return zona.lower().replace(" ", "-").replace("ñ", "n").replace("í", "i").replace("á", "a").replace("é", "e").replace("ó", "o").replace("ú", "u")
        # Caso 2: Contiene " de " y termina en "Lima" (o variaciones)
        if " de " in zona and zona.lower().endswith(" lima"):
            before_de = zona.rsplit(" de ", 1)[0].strip()  # Ej: "Cercado"
            transformed = f"lima-{before_de.lower().replace(' ', '-')}"
            return transformed.replace("ñ", "n").replace("í", "i").replace("á", "a").replace("é", "e").replace("ó", "o").replace("ú", "u")
        # Caso 3: Contiene " de " pero NO termina en Lima → mantener " de "
        if " de " in zona:
            parts = zona.split(" de ")
            transformed = "-".join(part.lower().replace(" ", "-") for part in parts)
            return transformed.replace("ñ", "n").replace("í", "i").replace("á", "a").replace("é", "e").replace("ó", "o").replace("ú", "u")
        # Caso 4: Sin "de", pero múltiples palabras → sustituir espacios por guiones
        return zona.lower().replace(" ", "-").replace("ñ", "n").replace("í", "i").replace("á", "a").replace("é", "e").replace("ó", "o").replace("ú", "u")

    # Generar slug final
    zona_url = transform_zona_for_url(zona_input)
    base_url = f"https://www.properati.com.pe/s/{zona_url}/alquiler?propertyType=apartment%2Chouse"
    if banos != "0" and banos:
        base_url += f"&bathrooms={banos}"
    if dormitorios != "0" and dormitorios:
        base_url += f"&bedrooms={dormitorios}"
    print(f"🌐 Properati URL generada: {base_url}")

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument(f"user-agent={COMMON_UA}")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    try:
        driver.get(base_url)
        # Esperar hasta que cargue contenedor principal o mensaje de "no resultados"
        WebDriverWait(driver, 15).until(
            lambda d: d.find_element(By.CSS_SELECTOR, "div[data-test='listings-serp']") or
                      d.find_element(By.CSS_SELECTOR, ".no-results-message, .empty-state")
        )
        time.sleep(1.5)
        soup = BeautifulSoup(driver.page_source, "html.parser")

        # Verificar si hay mensaje de "no resultados"
        no_results = soup.select_one(".no-results-message, .empty-state")
        if no_results:
            print("🔍 No se encontraron propiedades en Properati para esta búsqueda.")
            return pd.DataFrame()

        # Buscar contenedor principal de resultados
        listings_container = soup.find("div", {"data-test": "listings-serp"})
        if not listings_container:
            print("⚠️ Contenedor de resultados no encontrado en Properati.")
            return pd.DataFrame()

        cards = listings_container.select("article.snippet")
        if not cards:
            print("ℹ️ No se encontraron tarjetas de propiedades en Properati.")
            return pd.DataFrame()

        results = []
        for card in cards:
            try:
                # Enlace completo
                link = card.get("data-url", "")
                if not link:
                    a_tag = card.select_one("a.title")
                    link = a_tag.get("href") if a_tag else ""
                if link and link.startswith("/"):
                    link = "https://www.properati.com.pe" + link

                # Título
                titulo = ""
                a = card.select_one("a.title")
                if a:
                    titulo = a.get("title", "").strip()
                    if not titulo:
                        titulo = a.get_text(strip=True)

                # Precio
                price_el = card.select_one(".price")
                precio = price_el.get_text(strip=True) if price_el else ""

                # Área (m²)
                area_el = card.select_one(".properties__area")
                m2 = area_el.get_text(strip=True) if area_el else ""

                # Dormitorios
                bd_el = card.select_one(".properties__bedrooms")
                dormitorios_txt = bd_el.get_text(strip=True) if bd_el else ""
                dormitorios_val = int(re.search(r"\d+", dormitorios_txt).group()) if re.search(r"\d+", dormitorios_txt) else 0

                # Baños
                bt_el = card.select_one(".properties__bathrooms")
                banos_txt = bt_el.get_text(strip=True) if bt_el else ""
                banos_val = int(re.search(r"\d+", banos_txt).group()) if re.search(r"\d+", banos_txt) else 0

                # Ubicación (descripción)
                loc_el = card.select_one(".location")
                ubicacion = loc_el.get_text(strip=True) if loc_el else ""

                # >>> NUEVO: Extraer imagen <<<
                imagen_url = ""
                img_tag = card.select_one("img")
                if img_tag:
                    imagen_url = img_tag.get("src") or img_tag.get("data-src") or ""
                    if imagen_url.startswith("//"):
                        imagen_url = "https:" + imagen_url

                results.append({
                    "titulo": titulo,
                    "precio": precio,
                    "m2": m2,
                    "dormitorios": dormitorios_val,
                    "baños": banos_val,
                    "descripcion": ubicacion,
                    "link": link,
                    "fuente": "properati",
                    "imagen_url": imagen_url  # 👈 NUEVA COLUMNA
                })
            except Exception as e:
                print("⚠️ Error procesando tarjeta en Properati:", e)
                continue

        df = pd.DataFrame(results)
        return df

    except Exception as e:
        # Silenciamos completamente el stack trace — solo mensaje amigable
        print("🔍 No se encontraron propiedades en Properati para esta búsqueda.")
        return pd.DataFrame()
    finally:
        driver.quit()

# ---------- scrape_urbania (nueva fuente) ----------

def scrape_urbania(zona: str, dormitorios: str = "0", banos: str = "0", price_min: Optional[int] = None, price_max: Optional[int] = None):
    if not zona or not zona.strip():
        return pd.DataFrame()
    zona_input = zona.strip()
    zona_norm_slug = normalize_text(zona_input)
    driver = create_driver(headless=True)
    try:
        base_url = f"https://urbania.pe/buscar/alquiler-de-departamentos-en-{zona_norm_slug}--lima--lima"
        params = []
        if dormitorios and dormitorios != "0":
            params.append(f"bedroomsNumber={dormitorios}")
        if banos and banos != "0":
            params.append(f"bathroomMin={banos}")
        if price_min is not None:
            params.append(f"priceMin={price_min}")
        if price_max is not None:
            params.append(f"priceMax={price_max}")
        params.append("currencyId=6")
        if params:
            separator = "&" if "?" in base_url else "?"
            target_url = base_url + separator + "&".join(params)
        else:
            target_url = base_url
        print(f"🌐 Intentando URL directa: {target_url}")
        driver.get(target_url)
        time.sleep(3)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        cards = soup.select("div[data-qa='posting PROPERTY']")
        if not cards:
            print("⚠️ No se encontraron resultados con la URL directa. Intentando búsqueda interactiva...")
            driver.get("https://urbania.pe/buscar/alquiler")
            time.sleep(2)
            try:
                close_button = WebDriverWait(driver, 8).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-qa='MENU_MOBILE_CLOSE']"))
                )
                close_button.click()
            except:
                pass
            search_input = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[data-qa='input_ubicacion']"))
            )
            search_input.clear()
            search_input.send_keys(zona_input)
            try:
                first_suggestion = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "ul[data-qa='menuList'] li:first-child"))
                )
                print("✅ Sugerencia encontrada. Haciendo clic...")
                first_suggestion.click()
                time.sleep(3)
            except:
                print("⚠️ No se encontraron sugerencias. Forzando búsqueda con ENTER...")
                search_input.send_keys(webdriver.common.keys.Keys.ENTER)
                time.sleep(3)
            current_url = driver.current_url
            if any(p not in current_url for p in params):
                if params:
                    separator = "&" if "?" in current_url else "?"
                    target_url = current_url + separator + "&".join(params)
                print(f"🔗 URL con filtros interactivos: {target_url}")
                driver.get(target_url)
                time.sleep(3)
        scroll_count = 0
        while scroll_count < 5:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            scroll_count += 1
            print(f"🔄 Realizando scroll... {scroll_count}/5")
        soup = BeautifulSoup(driver.page_source, "html.parser")
        cards = soup.select("div[data-qa='posting PROPERTY']")
        if not cards:
            print("🔍 No se encontraron propiedades en Urbania.")
            return pd.DataFrame()
        results = []
        print(f"✅ Se encontraron {len(cards)} propiedades. Extrayendo datos y filtrando por zona...")
        for card in cards:
            try:
                link_tag = card.select_one("h3.postingCard-module__posting-description a")
                link = "https://urbania.pe" + link_tag["href"] if link_tag and link_tag["href"].startswith("/") else (link_tag["href"] if link_tag else "")
                titulo = link_tag.get_text(strip=True) if link_tag else ""
                location_el = card.select_one("div.postingCard-module__location")
                location_text = location_el.get_text(strip=True) if location_el else ""
                if zona_norm_slug not in normalize_text(titulo) and zona_norm_slug not in normalize_text(location_text):
                    continue
                price_el = card.select_one("div.postingPrices-module__price")
                precio = price_el.get_text(strip=True) if price_el else ""
                features = card.select("span.postingMainFeatures-module__posting-main-features-listing")
                m2, dormitorios_val, banos_val = "", 0, 0
                for feat in features:
                    text = feat.get_text(strip=True)
                    if re.search(r"\d+\s*m²|\d+\s*m2", text, re.I):
                        m2 = text
                    elif re.search(r"(\d+)\s*dorm", text, re.I):
                        dormitorios_val = int(re.search(r"(\d+)", text).group(1))
                    elif re.search(r"(\d+)\s*bañ", text, re.I):
                        banos_val = int(re.search(r"(\d+)", text).group(1))
                descripcion = titulo
                if dormitorios_val == 0 or banos_val == 0 or not titulo or not link:
                    continue

                # >>> NUEVO: Extraer URL de la primera imagen <<<
                imagen_url = ""
                img_tag = card.select_one("img")
                if img_tag:
                    imagen_url = img_tag.get("src") or img_tag.get("data-src") or ""
                    if imagen_url.startswith("//"):
                        imagen_url = "https:" + imagen_url

                results.append({
                    "titulo": titulo,
                    "precio": precio,
                    "m2": m2,
                    "dormitorios": dormitorios_val,
                    "baños": banos_val,
                    "descripcion": descripcion,
                    "link": link,
                    "fuente": "urbania",
                    "imagen_url": imagen_url  # 👈 NUEVA COLUMNA
                })
            except Exception as e:
                print(f"⚠️ Error procesando una tarjeta: {e}")
                continue
        df = pd.DataFrame(results)
        print(f"✅ ¡Éxito! Se encontraron {len(df)} propiedades en Urbania.")
        return df
    except Exception as e:
        print(f"❌ Error general en el scraper de Urbania: {e}")
        return pd.DataFrame()
    finally:
        driver.quit()

# ---------- Adaptadores ----------
def _adapter_nestoria(zona, dormitorios, banos, price_min, price_max):
    df, skipped = scrape_nestoria(zona, dormitorios=dormitorios, banos=banos, price_min=price_min, price_max=price_max, max_results=50, strict_zone=True)
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

def _adapter_infocasas(zona, dormitorios, banos, price_min, price_max):
    df = scrape_infocasas(zona, dormitorios=dormitorios, banos=banos, price_min=price_min, price_max=price_max, strict_zone=True)
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

def _adapter_doomos(zona, dormitorios="0", banos="0", price_min=None, price_max=None):
    # Convertir dormitorios y banos a enteros
    try:
        dorm = int(dormitorios) if dormitorios and dormitorios != "0" else 0
    except:
        dorm = 0
    try:
        ban = int(banos) if banos and banos != "0" else 0
    except:
        ban = 0
    # Primero intentar con el código general
    df = scrape_doomos_general(zona, dorm, ban, price_min, price_max)
    if df.empty:
        print("⚠️ No se encontraron anuncios con el código general, probando código especial...")
        df = scrape_doomos_brena(zona, dorm, ban)
    return df

def _adapter_properati(zona, dormitorios, banos, price_min, price_max):
    """Adaptador para usar scrape_properati dentro del sistema combinado."""
    df = scrape_properati(zona, dormitorios=dormitorios, banos=banos)
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

def _adapter_urbania(zona, dormitorios, banos, price_min, price_max):
    df = scrape_urbania(zona, dormitorios=dormitorios, banos=banos, price_min=price_min, price_max=price_max)
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

# Lista de scrapers
SCRAPERS = [
    ("nestoria", _adapter_nestoria),
    ("infocasas", _adapter_infocasas),
    ("urbania", _adapter_urbania),
    ("properati", _adapter_properati),
    ("doomos", _adapter_doomos),
]

# ---------- Filtrado y Combinación ----------

def _extract_int_from_text(s):
    if s is None:
        return None
    s = str(s)
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else None

def _extract_m2(s):
    if s is None:
        return None
    s = str(s)
    m = re.search(r"(\d{1,4})\s*(m²|m2)", s, flags=re.I)
    return int(m.group(1)) if m else None

def _parse_price_soles(s):
    moneda, val = parse_precio_con_moneda(str(s))
    if moneda == "S" and val is not None:
        return val
    return None

def _filter_df_strict(df, dormitorios_req, banos_req, price_min, price_max):
    if df is None or df.empty:
        return pd.DataFrame()
    dfc = df.copy().reset_index(drop=True)
    dfc["_precio_soles"] = dfc["precio"].apply(_parse_price_soles)
    dfc["_m2_num"] = dfc["m2"].apply(_extract_m2)
    dfc["_dorm_num"] = dfc["dormitorios"].apply(_extract_int_from_text)
    dfc["_banos_num"] = dfc["baños"].apply(_extract_int_from_text)
    mask = pd.Series(True, index=dfc.index)
    mask &= dfc["titulo"].astype(str).str.strip().replace({"": False, "None": False}).apply(lambda x: bool(x))
    mask &= dfc["link"].astype(str).str.strip().replace({"": False, "None": False}).apply(lambda x: bool(x))
    mask &= dfc["precio"].astype(str).str.strip().replace({"": False, "None": False}).apply(lambda x: bool(x))
    mask &= dfc["_m2_num"].notnull()
    mask &= dfc["_dorm_num"].notnull()
    mask &= dfc["_banos_num"].notnull()
    try:
        if dormitorios_req is not None and str(dormitorios_req).strip() != "" and str(dormitorios_req) != "0":
            dorm_req_int = int(dormitorios_req)
            mask &= (dfc["_dorm_num"] == dorm_req_int)
    except Exception:
        pass
    try:
        if banos_req is not None and str(banos_req).strip() != "" and str(banos_req) != "0":
            banos_req_int = int(banos_req)
            mask &= (dfc["_banos_num"] == banos_req_int)
    except Exception:
        pass
    if (price_min is not None) or (price_max is not None):
        if price_min is None:
            price_min = -10**12
        if price_max is None:
            price_max = 10**12
        mask &= dfc["_precio_soles"].notnull()
        mask &= (dfc["_precio_soles"] >= int(price_min)) & (dfc["_precio_soles"] <= int(price_max))
    df_filtered = dfc.loc[mask].copy().reset_index(drop=True)
    df_filtered.drop(columns=["_precio_soles","_m2_num","_dorm_num","_banos_num"], errors="ignore", inplace=True)
    return df_filtered

# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
# >>> NUEVA FUNCIÓN: FILTRADO SEMÁNTICO POR PALABRAS CLAVE <<<
def _filter_by_keywords(df, palabras_clave: str):
    """
    Filtra el DataFrame para mantener solo las filas que contienen TODAS las palabras clave
    en el texto combinado de: titulo + descripcion + m2 + dormitorios + baños.
    """
    if df.empty or not palabras_clave.strip():
        return df
    palabras = palabras_clave.lower().split()
    # Crear columna combinada
    df["texto_completo"] = (
        df["titulo"].astype(str) + " " +
        df["descripcion"].astype(str) + " " +
        df["m2"].astype(str) + " " +
        df["dormitorios"].astype(str) + " " +
        df["baños"].astype(str)
    ).str.lower()
    # Aplicar filtro: mantener solo filas que contengan TODAS las palabras
    for palabra in palabras:
        df = df[df["texto_completo"].str.contains(palabra, na=False, case=False)]
    # Eliminar columna auxiliar
    df.drop(columns=["texto_completo"], inplace=True, errors="ignore")
    return df
# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<

def run_scrapers(zona, dormitorios="0", banos="0", price_min=None, price_max=None, palabras_clave=""):
    """
    Ejecuta todos los scrapers y devuelve los resultados combinados
    """
    frames = []
    counts = {}
    logger.info(f"🔎 Buscando en {zona} | dorms={dormitorios} | baños={banos} | precio={price_min}-{price_max} | palabras_clave='{palabras_clave}'")

    for name, func in SCRAPERS:
        try:
            df = func(zona, dormitorios, banos, price_min, price_max)
        except Exception as e:
            logger.error(f"❌ Error en {name}: {e}")
            df = pd.DataFrame()

        if df is None:
            df = pd.DataFrame()

        # Asegurar que todas las columnas requeridas existan
        required_columns = ["titulo","precio","m2","dormitorios","baños","descripcion","link","fuente","imagen_url"]
        for col in required_columns:
            if col not in df.columns:
                df[col] = ""

        total_raw = len(df)
        counts[name] = total_raw
        logger.info(f"Fuente: {name} -> encontrados: {total_raw}")

        df = df.fillna("").astype(object)
        for col in required_columns:
            df[col] = df[col].astype(str).str.strip().replace({None: "", "None": ""})

        # Aplicar filtro estricto
        df_filtered = _filter_df_strict(df, dormitorios, banos, price_min, price_max)

        # Aplicar filtro por palabras clave
        if palabras_clave.strip():
            df_filtered = _filter_by_keywords(df_filtered, palabras_clave)

        if len(df_filtered) > 0:
            df_filtered = df_filtered.copy()
            df_filtered["scraped_at"] = datetime.now().isoformat()
            df_filtered["id"] = [str(uuid.uuid4()) for _ in range(len(df_filtered))]
            frames.append(df_filtered)

    if not frames:
        logger.warning("⚠️ Ninguna fuente devolvió anuncios")
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined = combined.drop_duplicates(subset=["link","titulo"], keep="first").reset_index(drop=True)
    return combined

# Para uso como módulo
if __name__ == "__main__":
    # Ejemplo de uso directo
    resultados = run_scrapers("miraflores", "2", "1", 1000, 2000, "piscina")
    print(f"Se encontraron {len(resultados)} propiedades")
    print(resultados.head())