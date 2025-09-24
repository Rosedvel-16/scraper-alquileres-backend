# -*- coding: utf-8 -*-
"""
Scraper completo: Nestoria, Infocasas, Urbania, Properati, Doomos
Filtros opcionales: zona, dormitorios, baños, price_min, price_max, palabras_clave
Salida: DataFrame combinado (mostrado) + CSV (combined_anuncios_filtrados.csv)
"""
import re
import time
import os
import requests
import pandas as pd
from typing import Optional
from bs4 import BeautifulSoup
import logging
import uuid
from datetime import datetime

# Selenium (solo para otros scrapers, no para Nestoria)
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

COMMON_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# -------------------- Helpers --------------------
def create_driver(headless: bool = True):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument("--disable-images")  # opcional: acelera
    options.add_argument("--blink-settings=imagesEnabled=false")  # opcional
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    # NO usamos ChromeDriverManager → asumimos que chromedriver está en PATH
    driver = webdriver.Chrome(options=options)
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        })
    except Exception:
        pass
    return driver

def slugify_zone(zona: str) -> str:
    if not zona:
        return ""
    s = zona.lower().strip()
    # Reemplazar caracteres especiales y tildes
    trans = str.maketrans("áéíóúñü", "aeiounu")
    s = s.translate(trans)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-]", "", s)
    return s

def parse_precio_con_moneda(precio_str):
    if not precio_str:
        return (None, None)
    s = str(precio_str)
    moneda = None
    if "S/" in s or s.strip().startswith("S/"):
        moneda = "S"
    elif "$" in s:
        moneda = "USD"
    nums = re.sub(r"[^\d]", "", s)
    return (moneda, int(nums)) if nums else (moneda, None)

def _extract_m2(s):
    if s is None:
        return None
    m = re.search(r"(\d{1,4})\s*(m²|m2)", str(s), flags=re.I)
    return int(m.group(1)) if m else None

def _parse_price_soles(s):
    moneda, val = parse_precio_con_moneda(str(s))
    return val if moneda == "S" else None

# -------------------- Nestoria (VERSÓN CORREGIDA Y FUNCIONAL CON IMÁGENES) --------------------
EXCEPCIONES = ["miraflores", "tarapoto", "la molina", "magdalena", "lambayeque", "ventanilla", "la victoria"]

def normalize_text(text):
    """Elimina acentos y pasa a minúsculas"""
    import unicodedata
    return unicodedata.normalize('NFKD', text.lower()).encode('ASCII','ignore').decode('utf-8')

def build_zona_slug_nestoria(zona_input: str) -> str:
    if not zona_input or not zona_input.strip():
        return "lima"  # ← ¡ESTO ES LO ÚNICO QUE CAMBIA!
    z = zona_input.strip().lower().replace(" ", "-")
    if z not in [e.lower() for e in EXCEPCIONES]:
        return z
    else:
        return "lima_" + z

def _extract_int_from_text(s):
    """
    Extrae el primer número entero de una cadena de texto.
    Es más robusta y maneja espacios, saltos de línea y caracteres especiales.
    """
    if s is None:
        return None
    # Convertir a string y limpiar espacios en blanco alrededor
    text = str(s).strip()
    # Reemplazar cualquier espacio en blanco (incluyendo &nbsp;, tabulaciones, saltos de línea) por un espacio normal
    text = re.sub(r'\s+', ' ', text)
    # Buscar el primer número entero
    m = re.search(r'(\d+)', text)
    return int(m.group(1)) if m else None

def scrape_nestoria(zona: str = "", dormitorios: str = "0", banos: str = "0",
                    price_min: Optional[int] = None, price_max: Optional[int] = None,
                    palabras_clave: str = "", max_results_per_zone: int = 200):
    """
    Scraper FINAL para Nestoria. Usa Selenium.
    Extrae la imagen DEL DETALLE de cada anuncio.
    Solo entra al detalle para obtener la imagen, no para extraer más datos.
    """
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
    logger.info(f"URL de Nestoria: {base_url}")
    driver = create_driver(headless=True)
    results = []
    try:
        driver.get(base_url)
        time.sleep(3)
        # Scroll para cargar más resultados
        for _ in range(5):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        # Seleccionar los contenedores de anuncios
        items = soup.select("li.rating__new") or soup.select("ul#main__listing_res > li")
        if not items:
            items = [li for li in soup.find_all("li") if li.select_one(".result__details__price")]
        if not items:
            items = soup.find_all(["li", "div", "article"], class_=lambda x: x and any(cls in x for cls in ["listing", "result", "property", "item"]))
        seen_links = set()
        for i, li in enumerate(items):
            try:
                # Extraer link
                a_tag = li.select_one("a.results__link") or li.select_one("a[href]")
                if not a_tag:
                    continue
                link = a_tag.get("data-href") or a_tag.get("href") or ""
                if link and link.startswith("/"):
                    link = "https://www.nestoria.pe" + link
                if not link or link in seen_links:
                    continue
                # Extraer título
                title_elem = li.select_one(".listing__title__text") or li.select_one(".listing__title") or a_tag
                title = title_elem.get_text(" ", strip=True) if title_elem else a_tag.get_text(" ", strip=True)[:140]
                # Extraer precio
                price_elem = li.select_one(".result__details__price span") or li.select_one(".result__details__price") or li.select_one(".price")
                price_text = price_elem.get_text(" ", strip=True) if price_elem else ""
                # Aplicar filtro de precio aquí mismo
                moneda, precio_val = parse_precio_con_moneda(price_text)
                if price_max is not None and moneda == "S" and precio_val is not None and precio_val > price_max:
                    continue
                if price_min is not None and moneda == "S" and precio_val is not None and precio_val < price_min:
                    continue
                if moneda == "USD" and (price_max is not None or price_min is not None):
                    continue
                # Extraer descripción
                desc_elem = li.select_one(".listing__description") or li.select_one(".result__summary") or None
                desc = desc_elem.get_text(" ", strip=True) if desc_elem else li.get_text(" ", strip=True)[:800]
                # Extraer dormitorios, baños y m2 del texto
                text_content = li.get_text(" ", strip=True).lower()
                dormitorios_text = ""
                dorm_match = re.search(r'(\d+)\s*dormitori', text_content, flags=re.I)
                if dorm_match:
                    dormitorios_text = dorm_match.group(1)
                banos_text = ""
                banos_match = re.search(r'(\d+)\s*bañ', text_content, flags=re.I)
                if banos_match:
                    banos_text = banos_match.group(1)
                m2_text = ""
                m2_match = re.search(r'(\d{1,4})\s*(m²|m2)', text_content, flags=re.I)
                if m2_match:
                    m2_text = m2_match.group(1)
                # AHORA: Entrar al detalle para obtener la imagen principal
                img_url = ""
                try:
                    driver.get(link)
                    time.sleep(1)  # Esperar a que cargue la imagen
                    detail_soup = BeautifulSoup(driver.page_source, "html.parser")
                    # Buscar la imagen principal en el detalle
                    main_img = detail_soup.select_one("img[data-element='main-swiper-slide']")
                    if main_img:
                        img_url = main_img.get("src") or main_img.get("data-src") or ""
                        if img_url and img_url.startswith("//"):
                            img_url = "https:" + img_url
                        img_url = img_url.strip()
                    else:
                        # Fallback: buscar cualquier img dentro de .photos .swiper-slide
                        fallback_img = detail_soup.select_one(".photos .swiper-slide img")
                        if fallback_img:
                            img_url = fallback_img.get("src") or fallback_img.get("data-src") or ""
                            if img_url and img_url.startswith("//"):
                                img_url = "https:" + img_url
                            img_url = img_url.strip()
                except Exception as e:
                    logger.warning(f"Error al obtener imagen de detalle en Nestoria para {link}: {e}")
                    pass
                results.append({
                    "titulo": title,
                    "precio": price_text,
                    "m2": m2_text,
                    "dormitorios": dormitorios_text,
                    "baños": banos_text,
                    "descripcion": desc,
                    "link": link,
                    "imagen_url": img_url
                })
                seen_links.add(link)
            except Exception as e:
                logger.warning(f"Error procesando anuncio en Nestoria: {e}")
                continue
    except Exception as e:
        logger.error(f"Error en Nestoria scraper: {e}")
    finally:
        try:
            driver.quit()
        except:
            pass
    logger.info(f"Procesados {len(results)} anuncios válidos de Nestoria")
    return pd.DataFrame(results)

# -------------------- Infocasas --------------------
def scrape_infocasas(zona: str = "", dormitorios: str = "0", banos: str = "0",
                     price_min: Optional[int] = None, price_max: Optional[int] = None,
                     palabras_clave: str = "", max_scrolls: int = 8):
    # Mapeo específico para InfoCasas
    ZONA_MAPEO_INFOCASAS = {
        "ancón": "ancon",
        "ate": "ate",
        "barranco": "barranco",
        "breña": "breña",
        "carabayllo": "carabayllo",
        "chaclacayo": "chaclacayo",
        "chorrillos": "chorrillos",
        "cieneguilla": "cieneguilla",
        "comas": "comas",
        "el agustino": "el-agustino",
        "independencia": "independencia",
        "jesús maría": "jesus-maria",
        "la molina": "la-molina",
        "la victoria": "la-victoria",
        "lima": "lima-cercado",
        "lince": "lince",
        "los olivos": "los-olivos",
        "lurigancho": "lurigancho",
        "lurín": "lurin",
        "magdalena del mar": "magdalena-del-mar",
        "miraflores": "miraflores",
        "pachacámac": "pachacamac",
        "pucusana": "pucusana",
        "pueblo libre": "pueblo-libre",
        "puente piedra": "puente-piedra",
        "punta hermosa": "punta-hermosa",
        "punta negra": "punta-negra",
        "rímac": "rimac",
        "san bartolo": "san-bartolo",
        "san borja": "san-borja",
        "san isidro": "san-isidro",
        "san juan de lurigancho": "san-juan-de-lurigancho",
        "san juan de miraflores": "san-juan-de-miraflores",
        "san luis": "san-luis",
        "san martín de porres": "san-martin-de-porres",
        "san miguel": "san-miguel",
        "santa anita": "santa-anita",
        "santa maría del mar": "santa-maria-del-mar",
        "santa rosa": "santa-rosa",
        "santiago de surco": "santiago-de-surco",
        "surquillo": "surquillo",
        "villa el salvador": "villa-el-salvador",
        "villa maría del triunfo": "villa-maria-del-triunfo"
    }
    # Construir URL base según la zona
    if zona and zona.strip():
        zona_lower = zona.strip().lower()
        zone_slug = ZONA_MAPEO_INFOCASAS.get(zona_lower, slugify_zone(zona))
        base = f"https://www.infocasas.com.pe/alquiler/casas-y-departamentos/lima/{zone_slug}"
    else:
        base = "https://www.infocasas.com.pe/alquiler/casas-y-departamentos"
    # Agregar filtros si están especificados
    if dormitorios and dormitorios != "0" and banos and banos != "0" and price_min is not None and price_max is not None:
        base += f"/{dormitorios}-dormitorio/{banos}-bano/desde-{price_min}/hasta-{price_max}?&IDmoneda=6"
    elif dormitorios and dormitorios != "0" and banos and banos != "0":
        base += f"/{dormitorios}-dormitorio/{banos}-bano"
    elif dormitorios and dormitorios != "0":
        base += f"/{dormitorios}-dormitorio"
    elif banos and banos != "0":
        base += f"/{banos}-bano"
    # Agregar parámetros de búsqueda si existen
    if palabras_clave and palabras_clave.strip():
        if "?" in base:
            base += f"&searchstring={requests.utils.quote(palabras_clave.strip())}"
        else:
            base += f"?searchstring={requests.utils.quote(palabras_clave.strip())}"
    logger.info(f"URL de InfoCasas: {base}")  # Mostrar URL usada
    driver = create_driver(headless=True)
    results = []
    try:
        driver.get(base)
        time.sleep(2)  # Esperar a que cargue la página
        # Hacer scroll para cargar más resultados
        for _ in range(max_scrolls):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.6)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        # Buscar los contenedores de anuncios específicos de InfoCasas
        nodes = soup.select("div.listingCard") or soup.select("article")
        for n in nodes:
            try:
                # Verificar que el elemento tiene el atributo href
                a = n.select_one("a[href]")
                if not a:
                    continue
                href = a.get("href") if a else ""
                # Construir URL completa
                if href and href.startswith("/"):
                    href = "https://www.infocasas.com.pe" + href
                # Extraer título
                title_elem = n.select_one("h2.lc-title") or n.select_one(".lc-title") or a
                title = title_elem.get_text(" ", strip=True) if title_elem else n.get_text(" ", strip=True)[:250]
                # Extraer precio
                price = ""
                price_elem = n.select_one(".main-price") or n.select_one(".lc-price p") or n.select_one(".property-price-tag p")
                if price_elem:
                    price = price_elem.get_text(" ", strip=True)
                # Extraer ubicación
                location_elem = n.select_one(".lc-location") or n.select_one("strong")
                location = location_elem.get_text(" ", strip=True) if location_elem else ""
                # Extraer dormitorios, baños y m² de los tags
                dormitorios_text = ""
                banos_text = ""
                m2_text = ""
                # Buscar en los elementos con clase lc-typologyTag__item
                typology_items = n.select(".lc-typologyTag__item strong")
                for item in typology_items:
                    text = item.get_text().strip()
                    if "Dorm" in text:
                        dorm_match = re.search(r'(\d+)', text)
                        if dorm_match:
                            dormitorios_text = dorm_match.group(1)
                    elif "Baños" in text or "Baño" in text:
                        banos_match = re.search(r'(\d+)', text)
                        if banos_match:
                            banos_text = banos_match.group(1)
                    elif "m²" in text:
                        m2_match = re.search(r'(\d+)', text)
                        if m2_match:
                            m2_text = m2_match.group(1)
                # Extraer descripción
                desc_elem = n.select_one(".lc-description") or n.select_one("p")
                desc = desc_elem.get_text(" ", strip=True) if desc_elem else n.get_text(" ", strip=True)[:400]
                # EXTRAER IMAGEN DIRECTAMENTE DEL LISTADO (NO ENTRAR AL DETALLE)
                img_url = ""
                img_tag = n.select_one(".cardImageGallery .gallery-image img")
                if img_tag:
                    img_url = img_tag.get("src") or img_tag.get("data-src") or ""
                    if img_url and img_url.startswith("//"):
                        img_url = "https:" + img_url
                    img_url = img_url.strip()
                results.append({
                    "titulo": title,
                    "precio": price,
                    "m2": m2_text,
                    "dormitorios": dormitorios_text,
                    "baños": banos_text,
                    "descripcion": desc,
                    "link": href or "",
                    "imagen_url": img_url
                })
            except Exception as e:
                logger.warning(f"Error procesando anuncio en InfoCasas: {e}")
                continue
    except Exception as e:
        logger.error(f"Error en InfoCasas scraper: {e}")
        pass
    finally:
        try:
            driver.quit()
        except:
            pass
    return pd.DataFrame(results)

# -------------------- Urbania --------------------
def scrape_urbania(zona: str = "", dormitorios: str = "0", banos: str = "0",
                   price_min: Optional[int] = None, price_max: Optional[int] = None,
                   palabras_clave: str = "", max_pages: int = 6, wait_time: float = 1.5):
    zona = (zona or "").strip()
    # construir keyword combinando filtros (si el usuario solo pone keyword, la usamos)
    kw_parts = []
    if palabras_clave and palabras_clave.strip():
        kw_parts.append(palabras_clave.strip())
    if dormitorios and str(dormitorios) != "0":
        kw_parts.append(f"{dormitorios} dormitorios")
    if banos and str(banos) != "0":
        kw_parts.append(f"{banos} banos")
    keyword_value = " ".join(kw_parts).strip()
    # CAMBIO CLAVE: Siempre usar la zona si está especificada, independientemente de las keywords
    if zona:
        # Mapeo específico para Urbania
        ZONA_MAPEO_URBANIA = {
            "ancón": "ancon",
            "ate": "ate-vitarte",  # Usar ate-vitarte como fallback
            "barranco": "barranco",
            "breña": "brena",
            "carabayllo": "carabayllo",
            "chaclacayo": "chaclacayo",
            "chorrillos": "chorrillos",
            "cieneguilla": "cieneguilla",
            "comas": "comas",
            "el agustino": "el-agustino",
            "independencia": "independencia",
            "jesús maría": "jesus-maria",
            "la molina": "la-molina",
            "la victoria": "la-victoria",
            "lima": "lima-cercado",
            "lince": "lince",
            "los olivos": "los-olivos",
            "lurigancho": "lurigancho",
            "lurín": "lurin",
            "magdalena del mar": "magdalena-del-mar",
            "miraflores": "miraflores",
            "pachacámac": "pachacamac",
            "pucusana": "pucusana",
            "pueblo libre": "pueblo-libre",
            "puente piedra": "puente-piedra",
            "punta hermosa": "punta-hermosa",
            "punta negra": "punta-negra",
            "rímac": "rimac",
            "san bartolo": "san-bartolo",
            "san borja": "san-borja",
            "san isidro": "san-isidro",
            "san juan de lurigancho": "san-juan-de-lurigancho",
            "san juan de miraflores": "san-juan-de-miraflores",
            "san luis": "san-luis",
            "san martín de porres": "san-martin-de-porres",
            "san miguel": "san-miguel",
            "santa anita": "santa-anita",
            "santa maría del mar": "santa-maria-del-mar",
            "santa rosa": "santa-rosa",
            "santiago de surco": "santiago-de-surco",
            "surquillo": "surquillo",
            "villa el salvador": "villa-el-salvador",
            "villa maría del triunfo": "villa-maria-del-triunfo"
        }
        zona_lower = zona.strip().lower()
        zone_slug = ZONA_MAPEO_URBANIA.get(zona_lower, slugify_zone(zona))
        base = f"https://urbania.pe/buscar/alquiler-de-departamentos-en-{zone_slug}--lima--lima"
    else:
        base = "https://urbania.pe/buscar/alquiler-de-departamentos"
    params = []
    if keyword_value:
        params.append(f"keyword={requests.utils.quote(keyword_value)}")
    if price_min is not None:
        params.append(f"priceMin={price_min}")
    if price_max is not None:
        params.append(f"priceMax={price_max}")
    if dormitorios and dormitorios != "0":
        params.append(f"bedroomMin={dormitorios}")
    if banos and banos != "0":
        params.append(f"bathroomMin={banos}")
    if price_min is not None or price_max is not None:
        params.append("currencyId=6")  # Soles
    url = base + ("?" + "&".join(params) if params else "")
    logger.info(f"URL de Urbania: {url}")  # Mostrar URL usada
    driver = create_driver(headless=True)
    results = []
    seen = set()
    try:
        driver.get(url)
        # esperar unos segundos por elementos representativos (no bloquear si timeout)
        try:
            WebDriverWait(driver, 12).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "article, div[data-qa='posting PROPERTY'], div.postingCard"))
            )
        except:
            pass
        page_count = 0
        while page_count < max_pages:
            page_count += 1
            last_h = driver.execute_script("return document.body.scrollHeight")
            for _ in range(8):
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(wait_time)
                new_h = driver.execute_script("return document.body.scrollHeight")
                if new_h == last_h:
                    break
                last_h = new_h
            soup = BeautifulSoup(driver.page_source, "html.parser")
            # intentar varios selectores
            card_selectors = [
                "div[data-qa='posting PROPERTY']",
                "article",
                "div.postingCard-module__posting",
                "div.postingCard",
                "div.posting-card",
                "div[class*='postingCard']",
            ]
            cards = []
            for sel in card_selectors:
                found = soup.select(sel)
                if found and len(found) > 0:
                    cards = found
                    break
            if not cards:
                cards = soup.select("a[href]")[:0]  # vacío
            prev_len = len(results)
            for c in cards:
                try:
                    a_tag = c.select_one("a[href]") or c.select_one("h2 a") or c.select_one("h3 a")
                    link = a_tag.get("href") if a_tag else ""
                    if link and link.startswith("/"):
                        link = "https://urbania.pe" + link
                    if not link:
                        continue
                    if link in seen:
                        continue
                    seen.add(link)
                    title = a_tag.get_text(" ", strip=True) if a_tag and a_tag.get_text(strip=True) else (c.get_text(" ", strip=True)[:140])
                    price_el = c.select_one("div.postingPrices-module__price") or c.select_one(".first-price") or c.select_one(".price")
                    price = price_el.get_text(" ", strip=True) if price_el else ""
                    desc = c.get_text(" ", strip=True)[:400]
                    img = ""
                    img_tag = c.select_one("img")
                    if img_tag:
                        img = img_tag.get("src") or img_tag.get("data-src") or ""
                        if img and img.startswith("//"): img = "https:" + img
                        # Limpiar espacios al final
                        img = img.strip()
                    # EXTRAER DORMITORIOS
                    dormitorios_text = ""
                    dorm_elem = c.select_one(".postingMainFeatures-module__posting-main-features-span:contains('dorm.')")
                    if dorm_elem:
                        dorm_text = dorm_elem.get_text(" ", strip=True)
                        dorm_match = re.search(r'(\d+)', dorm_text)
                        if dorm_match:
                            dormitorios_text = dorm_match.group(1)
                    # EXTRAER BAÑOS
                    banos_text = ""
                    banos_elem = c.select_one(".postingMainFeatures-module__posting-main-features-span:contains('baño')")
                    if banos_elem:
                        banos_text_full = banos_elem.get_text(" ", strip=True)
                        banos_match = re.search(r'(\d+)', banos_text_full)
                        if banos_match:
                            banos_text = banos_match.group(1)
                    # EXTRAER METROS CUADRADOS
                    m2_text = ""
                    m2_elem = c.select_one(".postingMainFeatures-module__posting-main-features-span:contains('m²')")
                    if m2_elem:
                        m2_text_full = m2_elem.get_text(" ", strip=True)
                        m2_match = re.search(r'(\d+)', m2_text_full)
                        if m2_match:
                            m2_text = m2_match.group(1)
                    # AHORA INCLUIMOS LOS VALORES EXTRAÍDOS
                    results.append({
                        "titulo": title,
                        "precio": price,
                        "m2": m2_text,
                        "dormitorios": dormitorios_text,
                        "baños": banos_text,
                        "descripcion": desc,
                        "link": link,
                        "imagen_url": img
                    })
                except Exception as e:
                    logger.warning(f"Error procesando anuncio en Urbania: {e}")
                    continue
            # si no hay nuevos resultados intentar paginar/click "cargar más"
            if len(results) == prev_len:
                clicked = False
                try:
                    # probar varios selectores para "cargar más" / siguiente
                    next_selectors = [
                        "a[rel='next']", "a[aria-label='Siguiente']", "a[data-qa='pagination-next']",
                        "button[data-qa='pagination-next']", "a.pagination__next", "a.next", "button.load-more", "a.load-more"
                    ]
                    for sel in next_selectors:
                        elems = driver.find_elements(By.CSS_SELECTOR, sel)
                        for e in elems:
                            try:
                                if e.is_displayed():
                                    driver.execute_script("arguments[0].scrollIntoView(true);", e)
                                    time.sleep(0.2)
                                    e.click()
                                    time.sleep(wait_time + 0.5)
                                    clicked = True
                                    break
                            except:
                                continue
                        if clicked:
                            break
                except:
                    clicked = False
                if not clicked:
                    # intentar incrementar page= en URL
                    cur = driver.current_url
                    m = re.search(r"([?&]page=)(\d+)", cur)
                    if m:
                        cur_page = int(m.group(2))
                        next_page = cur_page + 1
                        new_url = re.sub(r"([?&]page=)\d+", r"\1{}".format(next_page), cur)
                        try:
                            driver.get(new_url)
                            time.sleep(wait_time + 0.8)
                            clicked = True
                        except:
                            clicked = False
                if not clicked:
                    break
            time.sleep(0.4)
        return pd.DataFrame(results)
    except Exception as e:
        logger.error(f"Error en Urbania scraper: {e}")
        return pd.DataFrame()
    finally:
        try:
            driver.quit()
        except:
            pass

# -------------------- Properati --------------------
def scrape_properati(zona: str = "", dormitorios: str = "0", banos: str = "0",
                     price_min: Optional[int] = None, price_max: Optional[int] = None,
                     palabras_clave: str = ""):
    if zona and zona.strip():
        # Mapeo específico para Properati
        ZONA_MAPEO_PROPERATI = {
            "ancón": "ancon",
            "ate": "ate",
            "barranco": "barranco",
            "breña": "brena",
            "carabayllo": "carabayllo",
            "chaclacayo": "chaclacayo",
            "chorrillos": "chorrillos",
            "cieneguilla": "cieneguilla",
            "comas": "comas",
            "el agustino": "el-agustino",
            "independencia": "independencia",
            "jesús maría": "jesus-maria",
            "la molina": "la-molina",
            "la victoria": "la-victoria",
            "lima": "lima",
            "lince": "lince",
            "los olivos": "los-olivos",
            "lurigancho": "lurigancho",
            "lurín": "lurin",
            "magdalena del mar": "magdalena-del-mar",
            "miraflores": "miraflores",
            "pachacámac": "pachacamac",
            "pucusana": "pucusana",
            "pueblo libre": "pueblo-libre",
            "puente piedra": "puente-piedra",
            "punta hermosa": "punta-hermosa",
            "punta negra": "punta-negra",
            "rímac": "rimac",
            "san bartolo": "san-bartolo",
            "san borja": "san-borja",
            "san isidro": "san-isidro",
            "san juan de lurigancho": "san-juan-de-lurigancho",
            "san juan de miraflores": "san-juan-de-miraflores",
            "san luis": "san-luis",
            "san martín de porres": "san-martin-de-porres",
            "san miguel": "san-miguel",
            "santa anita": "santa-anita",
            "santa maría del mar": "santa-maria-del-mar",
            "santa rosa": "santa-rosa",
            "santiago de surco": "santiago-de-surco",
            "surquillo": "surquillo",
            "villa el salvador": "villa-el-salvador",
            "villa maría del triunfo": "villa-maria-del-triunfo"
        }
        zona_lower = zona.strip().lower()
        zone_slug = ZONA_MAPEO_PROPERATI.get(zona_lower, slugify_zone(zona))
        base = f"https://www.properati.com.pe/s/{zone_slug}/alquiler?propertyType=apartment%2Chouse"
    else:
        base = "https://www.properati.com.pe/s/alquiler?propertyType=apartment%2Chouse"
    # Agregar parámetros de filtros
    params = []
    if dormitorios and dormitorios != "0":
        params.append(f"bedrooms={dormitorios}")
    if banos and banos != "0":
        params.append(f"bathrooms={banos}")
    if price_min is not None:
        params.append(f"minPrice={price_min}")
    if price_max is not None:
        params.append(f"maxPrice={price_max}")
    # Procesar palabras clave: convertir "piscina" → amenities=swimming_pool, "jardin" → amenities=garden
    if palabras_clave and palabras_clave.strip():
        palabras = palabras_clave.lower().split()
        amenities = []
        other_keywords = []
        for p in palabras:
            if p == "piscina":
                amenities.append("swimming_pool")
            elif p == "jardin":
                amenities.append("garden")
            else:
                other_keywords.append(p)
        # Si hay amenities, usarlas como parámetro separado
        if amenities:
            base += "&amenities=" + ",".join(amenities)
        # Si quedan otras palabras clave, agregarlas como keyword
        if other_keywords:
            base += "&keyword=" + requests.utils.quote(" ".join(other_keywords))
    # Construir URL final
    if params:
        base += "&" + "&".join(params)
    logger.info(f"URL de Properati: {base}")  # Mostrar URL usada
    try:
        r = requests.get(base, headers={"User-Agent": COMMON_UA}, timeout=15)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"Error en Properati al hacer la petición: {e}")
        return pd.DataFrame()
    soup = BeautifulSoup(r.text, "html.parser")
    cards = soup.select("article") or soup.select("div.posting-card") or soup.select("a[href]")
    results = []
    for c in cards:
        try:
            a = c.select_one("a[href]") or c.select_one("a.title")
            href = a.get("href") if a else ""
            if href and href.startswith("/"):
                href = "https://www.properati.com.pe" + href
            title = a.get_text(" ", strip=True) if a else c.get_text(" ", strip=True)[:140]
            price = ""
            price_elem = c.select_one(".price")
            if price_elem:
                price = price_elem.get_text(" ", strip=True)
            # EXTRAER DORMITORIOS
            dormitorios_text = ""
            dorm_elem = c.select_one(".properties__bedrooms")
            if dorm_elem:
                dorm_text = dorm_elem.get_text(" ", strip=True)
                dorm_match = re.search(r'(\d+)', dorm_text)
                if dorm_match:
                    dormitorios_text = dorm_match.group(1)
            # EXTRAER BAÑOS
            banos_text = ""
            banos_elem = c.select_one(".properties__bathrooms")
            if banos_elem:
                banos_text_full = banos_elem.get_text(" ", strip=True)
                banos_match = re.search(r'(\d+)', banos_text_full)
                if banos_match:
                    banos_text = banos_match.group(1)
            # EXTRAER METROS CUADRADOS
            m2_text = ""
            m2_elem = c.select_one(".properties__area")
            if m2_elem:
                m2_text_full = m2_elem.get_text(" ", strip=True)
                m2_match = re.search(r'(\d+)', m2_text_full)
                if m2_match:
                    m2_text = m2_match.group(1)
            img = ""
            img_tag = c.select_one("img")
            if img_tag:
                img = img_tag.get("src") or img_tag.get("data-src") or ""
                # Filtrar imágenes no deseadas: solo aceptar las que comienzan con https://img (no con https://images.proppit)
                if img and img.startswith("https://img"):
                    img = img.strip()
                elif img and img.startswith("//"):
                    img_full = "https:" + img
                    if img_full.startswith("https://img"):
                        img = img_full.strip()
                    else:
                        img = ""  # Rechazar otras fuentes
                else:
                    img = ""  # Rechazar si no cumple con el criterio
            # AHORA INCLUIMOS LOS VALORES EXTRAÍDOS
            results.append({
                "titulo": title,
                "precio": price,
                "m2": m2_text,
                "dormitorios": dormitorios_text,
                "baños": banos_text,
                "descripcion": title,
                "link": href or "",
                "imagen_url": img
            })
        except Exception as e:
            logger.warning(f"Error en Properati al procesar un anuncio: {e}")
            continue
    return pd.DataFrame(results)

# -------------------- Doomos --------------------
def scrape_doomos(zona: str = "", dormitorios: str = "0", banos: str = "0",
                  price_min: Optional[int] = None, price_max: Optional[int] = None,
                  palabras_clave: str = ""):
    driver = create_driver(headless=True)
    results = []
    try:
        # Mapeo ACTUALIZADO de zonas a sus IDs específicos para Doomos
        ZONA_IDS_CORRECTOS = {
            "ancón": "-336912",
            "ate": "-337679",
            "breña": "65645345",
            "carabayllo": "-339907",
            "chaclacayo": "-341190",
            "chorrillos": "-342811",
            "cieneguilla": "-343329",
            "comas": "-343903",
            "el agustino": "-345552",
            "jesús maría": "348294",
            "la molina": "-351740",
            "la victoria": "-352442",
            "lima": "45343445",  # Cercado de Lima
            "lince": "-352696",
            "los olivos": "191126",
            "lurigancho": "-353648",
            "lurín": "-353652",
            "magdalena del mar": "326245",
            "miraflores": "-354864",
            "pachacámac": "-356636",
            "pucusana": "-359672",
            "pueblo libre": "-359690",
            "puente piedra": "-359759",
            "punta hermosa": "-360186",
            "punta negra": "-360189",
            "rímac": "-361308",
            "san bartolo": "-362154",
            "san borja": "-362170",
            "san isidro": "-362425",
            "san luis": "-362738",
            "san miguel": "-362804",
            "santiago de surco": "-364705",
            "surquillo": "-364723"
        }
        # Construir URL base CORRECTA para Doomos
        base_url = "http://www.doomos.com.pe/search/"
        # Parámetros base
        params = {
            "clase": "1",           # Departamentos
            "stipo": "16",          # Alquiler
            "pagina": "1",
            "sort": "primeasc"
        }
        # Si NO se especifica zona, usar LIMA por defecto con el ID CORRECTO
        if not zona or not zona.strip():
            params["loc_name"] = "Lima (Región de Lima)"
            params["loc_id"] = "-352647"  # ← ¡¡¡ESTA ES LA LÍNEA CORREGIDA!!!
        else:
            zona_lower = zona.strip().lower()
            loc_id = ZONA_IDS_CORRECTOS.get(zona_lower, "")
            zona_formateada = f"{zona.strip()} (Región de Lima)"
            params["loc_name"] = zona_formateada
            if loc_id:
                params["loc_id"] = loc_id
        # Agregar filtros opcionales
        if dormitorios and dormitorios != "0":
            params["piezas"] = dormitorios
        if banos and banos != "0":
            params["banos"] = banos
        if price_min is not None:
            params["preciomin"] = str(price_min)
        if price_max is not None:
            params["preciomax"] = str(price_max)
        if palabras_clave and palabras_clave.strip():
            params["keyword"] = palabras_clave.strip()
        # Construir URL completa
        url = base_url + "?" + "&".join(f"{k}={requests.utils.quote(str(v))}" for k,v in params.items())
        logger.info(f"URL de Doomos: {url}")
        driver.get(url)
        time.sleep(3)
        # Scroll para cargar más resultados
        for _ in range(3):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        cards = soup.select(".content_result")
        if not cards:
            logger.warning("No se encontraron cards en Doomos")
            return pd.DataFrame()
        logger.info(f"Se encontraron {len(cards)} cards en Doomos")
        for card in cards:
            try:
                # Extraer link y título
                a_tag = card.select_one(".content_result_titulo a")
                if not a_tag:
                    continue
                title = a_tag.get_text(" ", strip=True)
                href = a_tag.get("href") or ""
                # Construir URL completa si es relativa
                if href and href.startswith("/"):
                    href = "http://www.doomos.com.pe" + href
                # Extraer precio
                price_elem = card.select_one(".content_result_precio")
                price = price_elem.get_text(" ", strip=True) if price_elem else ""
                # Extraer descripción
                desc_elem = card.select_one(".content_result_descripcion")
                desc = desc_elem.get_text(" ", strip=True) if desc_elem else card.get_text(" ", strip=True)[:400]
                # Extraer dormitorios, baños, m2 del texto
                dormitorios_text = ""
                banos_text = ""
                m2_text = ""
                text_content = card.get_text(" ", strip=True).lower()
                dorm_match = re.search(r'(\d+)\s*dormitorio', text_content)
                if dorm_match:
                    dormitorios_text = dorm_match.group(1)
                banos_match = re.search(r'(\d+)\s*baño', text_content)
                if banos_match:
                    banos_text = banos_match.group(1)
                m2_match = re.search(r'(\d+)\s*m2', text_content)
                if m2_match:
                    m2_text = m2_match.group(1)
                # EXTRAER IMAGEN DIRECTAMENTE DEL LISTADO (NO ENTRAR AL DETALLE)
                img_url = ""
                img_tag = card.select_one("img.content_result_image")
                if img_tag:
                    img_url = img_tag.get("src") or img_tag.get("data-src") or ""
                    if img_url and img_url.startswith("//"):
                        img_url = "https:" + img_url
                    img_url = img_url.strip()
                results.append({
                    "titulo": title,
                    "precio": price,
                    "m2": m2_text,
                    "dormitorios": dormitorios_text,
                    "baños": banos_text,
                    "descripcion": desc,
                    "link": href,
                    "imagen_url": img_url
                })
            except Exception as e:
                logger.warning(f"Error procesando card en Doomos: {e}")
                continue
    except Exception as e:
        logger.error(f"Error en Doomos scraper: {e}")
    finally:
        try:
            driver.quit()
        except:
            pass
    return pd.DataFrame(results)

# -------------------- Filtrado y Unificación --------------------
SCRAPERS = [
    ("nestoria", scrape_nestoria),
    ("infocasas", scrape_infocasas),
    ("urbania", scrape_urbania),
    ("properati", scrape_properati),
    ("doomos", scrape_doomos),
]

def _filter_df_strict(df, dormitorios_req, banos_req, price_min, price_max):
    if df is None or df.empty:
        return pd.DataFrame()
    dfc = df.copy().reset_index(drop=True)
    dfc["_precio_soles"] = dfc["precio"].apply(_parse_price_soles)
    dfc["_dorm_num"] = dfc["dormitorios"].apply(_extract_int_from_text)
    dfc["_banos_num"] = dfc["baños"].apply(_extract_int_from_text)
    mask = pd.Series(True, index=dfc.index)
    # only require dorm/banos if user requested them
    try:
        if dormitorios_req is not None and str(dormitorios_req).strip() != "" and str(dormitorios_req) != "0":
            dorm_req_int = int(dormitorios_req)
            mask &= (dfc["_dorm_num"].notnull()) & (dfc["_dorm_num"] == dorm_req_int)
    except:
        pass
    try:
        if banos_req is not None and str(banos_req).strip() != "" and str(banos_req) != "0":
            banos_req_int = int(banos_req)
            mask &= (dfc["_banos_num"].notnull()) & (dfc["_banos_num"] == banos_req_int)
    except:
        pass
    if (price_min is not None) or (price_max is not None):
        if price_min is None:
            price_min = -10**12
        if price_max is None:
            price_max = 10**12
        mask &= dfc["_precio_soles"].notnull()
        mask &= (dfc["_precio_soles"] >= int(price_min)) & (dfc["_precio_soles"] <= int(price_max))
    df_filtered = dfc.loc[mask].copy().reset_index(drop=True)
    df_filtered.drop(columns=["_precio_soles","_dorm_num","_banos_num"], errors="ignore", inplace=True)
    return df_filtered

def _filter_by_keywords(df, palabras_clave: str):
    if df is None or df.empty or not palabras_clave or not palabras_clave.strip():
        return df
    palabras = palabras_clave.lower().split()
    dfc = df.copy()
    dfc["texto_completo"] = (
        dfc["titulo"].astype(str) + " " +
        dfc.get("descripcion", pd.Series([""]*len(dfc))).astype(str) + " " +
        dfc.get("m2", pd.Series([""]*len(dfc))).astype(str) + " " +
        dfc.get("dormitorios", pd.Series([""]*len(dfc))).astype(str) + " " +
        dfc.get("baños", pd.Series([""]*len(dfc))).astype(str)
    ).str.lower()
    for p in palabras:
        dfc = dfc[dfc["texto_completo"].str.contains(re.escape(p), na=False, case=False)]
    dfc.drop(columns=["texto_completo"], errors="ignore", inplace=True)
    return dfc

def run_scrapers(zona: str = "", dormitorios: str = "0", banos: str = "0",
                 price_min: Optional[int] = None, price_max: Optional[int] = None,
                 palabras_clave: str = ""):
    """
    Ejecuta todos los scrapers y devuelve los resultados combinados.
    Esta función es el punto de entrada para main.py.
    """
    frames = []
    counts_raw = {}
    counts_after = {}
    logger.info(f"🔎 Buscando: zona='{zona}' | dorms={dormitorios} | baños={banos} | pmin={price_min} | pmax={price_max} | keywords='{palabras_clave}'")
    for name, func in SCRAPERS:
        logger.info(f"-> Ejecutando scraper: {name}")
        try:
            df = func(zona=zona, dormitorios=dormitorios, banos=banos, price_min=price_min, price_max=price_max, palabras_clave=palabras_clave)
        except TypeError:
            # backward compatibility
            try:
                df = func(zona, dormitorios, banos, price_min, price_max)
            except Exception as e:
                logger.error(f" ❌ Error ejecutando {name} (fallback): {e}")
                df = pd.DataFrame()
        except Exception as e:
            logger.error(f" ❌ Error CRÍTICO ejecutando {name}: {e}")
            df = pd.DataFrame() # <- Esto es lo que causa que solo se vea Properati
        if df is None or not isinstance(df, pd.DataFrame):
            df = pd.DataFrame(columns=["titulo","precio","m2","dormitorios","baños","descripcion","link","imagen_url"])
        # ensure columns present
        required_columns = ["titulo","precio","m2","dormitorios","baños","descripcion","link","imagen_url"]
        for col in required_columns:
            if col not in df.columns:
                df[col] = ""
        total_raw = len(df)
        counts_raw[name] = total_raw
        logger.info(f"   encontrados (raw): {total_raw}")
        # normalize
        df = df.fillna("").astype(object)
        for col in required_columns:
            df[col] = df[col].astype(str).str.strip().replace({None: "", "None": ""})
        # strict filters (price/dorm/banos)
        df_filtered = _filter_df_strict(df, dormitorios, banos, price_min, price_max)
        logger.info(f"   después filtrado estricto: {len(df_filtered)}")
        # keywords: apply post-scrape ONLY for sources that didn't use keyword in URL
        # EXCLUDE properati because it uses 'amenities' and text may not contain the keyword
        if palabras_clave and palabras_clave.strip() and name not in ("urbania", "doomos", "properati"):
            prev = len(df_filtered)
            df_filtered = _filter_by_keywords(df_filtered, palabras_clave)
            logger.info(f"   después filtrar por keywords: {len(df_filtered)} (eliminados {prev - len(df_filtered)})")
        counts_after[name] = len(df_filtered)
        if len(df_filtered) > 0:
            df_filtered = df_filtered.copy()
            df_filtered["fuente"] = name
            # Añadir campos requeridos por main.py
            df_filtered["scraped_at"] = datetime.now().isoformat()
            df_filtered["id"] = [str(uuid.uuid4()) for _ in range(len(df_filtered))]
            frames.append(df_filtered)
    if not frames:
        logger.warning("⚠️ Ninguna fuente devolvió anuncios tras filtrar. Conteo raw:", counts_raw)
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True, sort=False)
    # Eliminar filas donde el link empieza con "#" o está vacío
    combined = combined[~combined["link"].str.startswith("#")].reset_index(drop=True)
    combined = combined[combined["link"] != ""].reset_index(drop=True)
    combined = combined.drop_duplicates(subset=["link","titulo"], keep="first").reset_index(drop=True)
    logger.info(f"✅ Total final de propiedades combinadas: {len(combined)}")
    return combined

# Para uso como módulo (compatible con main.py)
if __name__ == "__main__":
    print("CONFIG: todos los filtros son opcionales. Dejar vacío para 'no filtrar' en ese campo.")
    zona = input("👉 Zona (ej: comas) - vacío para todas: ").strip()
    dormitorios = input("👉 Dormitorios (0 si no filtrar): ").strip() or "0"
    banos = input("👉 Baños (0 si no filtrar): ").strip() or "0"
    pmin = input("👉 Precio mínimo (solo números, 0 si no filtrar): ").strip() or "0"
    pmax = input("👉 Precio máximo (solo números, 0 si no filtrar): ").strip() or "0"
    palabras_clave = input("👉 Palabras clave (opcional, ej 'piscina mascotas jardin'): ").strip()
    pmin_val = int(pmin) if pmin and pmin != "0" else None
    pmax_val = int(pmax) if pmax and pmax != "0" else None
    combined = run_scrapers(zona=zona, dormitorios=dormitorios, banos=banos,
                            price_min=pmin_val, price_max=pmax_val,
                            palabras_clave=palabras_clave)
    print("Proceso finalizado. Resultados (tras filtrar):", len(combined))