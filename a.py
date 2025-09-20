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

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

COMMON_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# -------------------- Helpers --------------------
def create_driver(headless: bool = True):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument(f"user-agent={COMMON_UA}")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
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

def _extract_int_from_text(s):
    if s is None:
        return None
    m = re.search(r"(\d+)", str(s))
    return int(m.group(1)) if m else None

def _extract_m2(s):
    if s is None:
        return None
    m = re.search(r"(\d{1,4})\s*(m²|m2)", str(s), flags=re.I)
    return int(m.group(1)) if m else None

def _parse_price_soles(s):
    moneda, val = parse_precio_con_moneda(str(s))
    return val if moneda == "S" else None

# -------------------- Nestoria --------------------
# mapa básico español->inglés para keywords usados por Nestoria
NESTORIA_KEYWORDS_MAP = {
    "piscina": "pool",
    "piscinas": "pool",
    "jardin": "garden",
    "jardín": "garden",
    "gimnasio": "gym",
    "gym": "gym",
    "comercial": ("keywords_property_type", "commercial"),
    "condominio": "condo",
    "ascensor": "lift",
    "ascensores": "lift",
    "balcon": "balcony",
    "balcón": "balcony",
    "cancha deportiva": "sport_facilities",
    "bodega": "storage_room",
    "terraza": "terrace",
    "mascotas": "pets"
}

# lista de distritos a iterar cuando zona no fue especificada y user puso keywords
NESTORIA_DISTRICTS = [
    "comas","miraflores","san_isidro","barranco","san_miguel","surco","santiago-de-surco",
    "jesus-maria","la-molina","san-borja","pueblo-libre","rimac","la-victoria",
    "magdalena-del-mar","los-olivos","san-juan-de-lurigancho","san-juan-de-miraflores",
    "callao","ventanilla","chorrillos","puente-piedra","lince","san-luis"
]

def _parse_nestoria_listings_from_soup(soup, seen_links:set):
    # selectors robustos
    items = []
    for sel in ["ul#main_listing_res > li", "li.item", "div.listing", "div.property", "article", "div.ad", "div.result"]:
        found = soup.select(sel)
        if found:
            items.extend(found)
    # fallback: any li with price
    if not items:
        for li in soup.find_all("li"):
            if li.select_one(".result__details__price") or li.select_one(".price"):
                items.append(li)
    results = []
    for li in items:
        try:
            a = li.select_one("a[href]") or li.select_one("a")
            title = a.get_text(" ", strip=True) if a else li.get_text(" ", strip=True)[:140]
            link = a.get("href") if a else ""
            if link and link.startswith("/"):
                link = "https://www.nestoria.pe" + link
            if not link:
                # try data-href
                link = a.get("data-href") if a and a.get("data-href") else ""
                if link and link.startswith("/"):
                    link = "https://www.nestoria.pe" + link
            if link in seen_links:
                continue
            price = ""
            p = li.select_one(".result__details__price") or li.select_one(".price")
            if p:
                price = p.get_text(" ", strip=True)
            desc = li.get_text(" ", strip=True)[:800]
            img = ""
            img_tag = li.select_one("img")
            if img_tag:
                img = img_tag.get("src") or img_tag.get("data-src") or img_tag.get("data-original") or ""
                if img and img.startswith("//"):
                    img = "https:" + img
            results.append({
                "titulo": title,
                "precio": price,
                "m2": "",
                "dormitorios": "",
                "baños": "",
                "descripcion": desc,
                "link": link or "",
                "imagen_url": img
            })
            if link:
                seen_links.add(link)
        except Exception:
            continue
    return results

def scrape_nestoria(zona: str = "", dormitorios: str = "0", banos: str = "0",
                    price_min: Optional[int] = None, price_max: Optional[int] = None,
                    palabras_clave: str = "", max_results_per_zone: int = 200):
    """
    - Si zona especificada -> usa /{zona}/inmuebles/alquiler
    - Si zona vacía y hay palabras_clave -> itera por NESTORIA_DISTRICTS y recoge resultados por distrito con keywords_features=...
    - Si zona vacía y no hay keywords -> usa /inmuebles/alquiler (búsqueda general).
    """
    seen_links = set()
    aggregated = []

    # construir valor de keyword para Nestoria (mapear al inglés si posible)
    kw_param = None
    if palabras_clave and palabras_clave.strip():
        kw_key = palabras_clave.strip().lower()
        if kw_key in NESTORIA_KEYWORDS_MAP:
            mapped = NESTORIA_KEYWORDS_MAP[kw_key]
            if isinstance(mapped, tuple):
                kw_param = (mapped[0], mapped[1])
            else:
                kw_param = ("keywords_features", mapped)
        else:
            # fallback: pasar tal cual (escape)
            kw_param = ("keywords_features", requests.utils.quote(palabras_clave.strip()))

    headers = {"User-Agent": COMMON_UA}

    def try_scrape_zone(zone_slug):
        base = f"https://www.nestoria.pe/{zone_slug}/inmuebles/alquiler"
        params = []
        if kw_param:
            # kw_param puede ser tuple (param,val)
            if isinstance(kw_param, tuple) and isinstance(kw_param[0], str):
                params.append(f"{kw_param[0]}={kw_param[1]}")
        if dormitorios and dormitorios != "0":
            params.append(f"bedrooms={dormitorios}")
        if banos and banos != "0":
            params.append(f"bathrooms={banos}")
        if price_min is not None:
            params.append(f"price_min={price_min}")
        if price_max is not None:
            params.append(f"price_max={price_max}")
        url = base + ("?" + "&".join(params) if params else "")
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, "html.parser")
            found = _parse_nestoria_listings_from_soup(soup, seen_links)
            return found
        except Exception:
            return []

    # 1) Si zona dada -> solo esa zona
    if zona and zona.strip():
        zone_slug = slugify_zone(zona)
        results = try_scrape_zone(zone_slug)
        aggregated.extend(results[:max_results_per_zone])
    else:
        # zona no dada
        if kw_param:
            # iterar distritos principales y agregar resultados
            for d in NESTORIA_DISTRICTS:
                slug = slugify_zone(d.replace("_"," "))
                res = try_scrape_zone(slug)
                if res:
                    aggregated.extend(res[:max_results_per_zone])
                # breve pausa para no saturar
                time.sleep(0.5)
        else:
            # ni zona ni keyword -> intentar búsqueda general (sin distrito)
            url = "https://www.nestoria.pe/inmuebles/alquiler"
            params = []
            if dormitorios and dormitorios != "0":
                params.append(f"bedrooms={dormitorios}")
            if banos and banos != "0":
                params.append(f"bathrooms={banos}")
            if price_min is not None:
                params.append(f"price_min={price_min}")
            if price_max is not None:
                params.append(f"price_max={price_max}")
            if params:
                url += "?" + "&".join(params)
            try:
                r = requests.get(url, headers=headers, timeout=15)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "html.parser")
                    aggregated.extend(_parse_nestoria_listings_from_soup(soup, seen_links))
            except Exception:
                pass

    df = pd.DataFrame(aggregated)
    # devolver DataFrame (posible vacío)
    return df

# -------------------- Infocasas --------------------
def scrape_infocasas(zona: str = "", dormitorios: str = "0", banos: str = "0",
                     price_min: Optional[int] = None, price_max: Optional[int] = None,
                     palabras_clave: str = "", max_scrolls: int = 8):
    base = "https://www.infocasas.com.pe/alquiler/casas-y-departamentos"
    if palabras_clave and palabras_clave.strip():
        base += f"?&searchstring={requests.utils.quote(palabras_clave.strip())}"

    driver = create_driver(headless=True)
    results = []
    try:
        driver.get(base)
        # scroll
        for _ in range(max_scrolls):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.6)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        nodes = soup.select("a.lc-data") or soup.select("li.lc-item") or soup.select("div.listingCard") or soup.select("article")
        for n in nodes:
            try:
                a = n.select_one("a[href]") or n
                href = a.get("href") if a and a.get("href") else ""
                if href and href.startswith("/"):
                    href = "https://www.infocasas.com.pe" + href
                title = (a.get("title") if a and a.get("title") else n.get_text(" ", strip=True))[:250]
                price = ""
                p = n.select_one("p.main-price") or n.select_one(".main-price")
                if p:
                    price = p.get_text(" ", strip=True)
                img = ""
                img_tag = n.select_one("img")
                if img_tag:
                    img = img_tag.get("src") or img_tag.get("data-src") or ""
                    if img and img.startswith("//"):
                        img = "https:" + img
                desc = n.get_text(" ", strip=True)[:400]
                results.append({
                    "titulo": title, "precio": price, "m2": "",
                    "dormitorios": "", "baños": "", "descripcion": desc,
                    "link": href or "", "imagen_url": img
                })
            except Exception:
                continue
    except Exception:
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

    if zona and not keyword_value:
        zone_slug = slugify_zone(zona)
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

    url = base + ("?" + "&".join(params) if params else "")
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
                    results.append({
                        "titulo": title, "precio": price, "m2": "",
                        "dormitorios": "", "baños": "",
                        "descripcion": desc, "link": link, "imagen_url": img
                    })
                except Exception:
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
    except Exception:
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
        zone_slug = slugify_zone(zona)
        base = f"https://www.properati.com.pe/s/{zone_slug}/alquiler?propertyType=apartment%2Chouse"
    else:
        base = "https://www.properati.com.pe/s/alquiler?propertyType=apartment%2Chouse"
    if palabras_clave and palabras_clave.strip():
        base += f"&keyword={requests.utils.quote(palabras_clave.strip())}"
    try:
        r = requests.get(base, headers={"User-Agent": COMMON_UA}, timeout=15)
        r.raise_for_status()
    except:
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
            price = c.get_text(" ", strip=True)[:80]
            img = c.select_one("img").get("src") if c.select_one("img") else ""
            results.append({
                "titulo": title, "precio": price, "m2": "", "dormitorios": "", "baños": "",
                "descripcion": title, "link": href or "", "imagen_url": img
            })
        except:
            continue
    return pd.DataFrame(results)

# -------------------- Doomos --------------------
def scrape_doomos(zona: str = "", dormitorios: str = "0", banos: str = "0",
                  price_min: Optional[int] = None, price_max: Optional[int] = None,
                  palabras_clave: str = ""):
    base = "http://www.doomos.com.pe/search/"
    params = {"clase": "1", "stipo": "16", "pagina": "1", "sort": "primeasc", "provincia": "15"}
    if palabras_clave and palabras_clave.strip():
        params["key"] = palabras_clave.strip()
    if zona and zona.strip():
        params["loc_name"] = zona.strip()
    # precios
    params["preciomin"] = str(price_min) if price_min is not None else "min"
    params["preciomax"] = str(price_max) if price_max is not None else "max"
    url = base + "?" + "&".join(f"{k}={requests.utils.quote(str(v))}" for k,v in params.items())
    try:
        r = requests.get(url, headers={"User-Agent": COMMON_UA}, timeout=15)
        r.raise_for_status()
    except:
        return pd.DataFrame()
    soup = BeautifulSoup(r.text, "html.parser")
    cards = soup.select(".content_result") or soup.select(".result") or soup.select("article")
    results = []
    for card in cards:
        try:
            a = card.select_one(".content_result_titulo a") or card.select_one("a[href]")
            title = a.get_text(" ", strip=True) if a else card.get_text(" ", strip=True)[:140]
            href = a.get("href") if a else ""
            if href and href.startswith("/"):
                href = "http://www.doomos.com.pe" + href
            price = card.select_one(".content_result_precio").get_text(" ", strip=True) if card.select_one(".content_result_precio") else ""
            img = card.select_one("img").get("src") if card.select_one("img") else ""
            results.append({
                "titulo": title, "precio": price, "m2": "", "dormitorios": "", "baños": "",
                "descripcion": title, "link": href or "", "imagen_url": img
            })
        except:
            continue
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

def run_and_combine_all(zona: str = "", dormitorios: str = "0", banos: str = "0",
                        price_min: Optional[int] = None, price_max: Optional[int] = None,
                        palabras_clave: str = ""):
    frames = []
    counts_raw = {}
    counts_after = {}
    print(f"\n🔎 Buscando: zona='{zona}' | dorms={dormitorios} | baños={banos} | pmin={price_min} | pmax={price_max} | keywords='{palabras_clave}'\n")
    for name, func in SCRAPERS:
        print(f"-> Ejecutando scraper: {name}")
        try:
            df = func(zona=zona, dormitorios=dormitorios, banos=banos, price_min=price_min, price_max=price_max, palabras_clave=palabras_clave)
        except TypeError:
            # backward compatibility: call with fewer args
            try:
                df = func(zona, dormitorios, banos, price_min, price_max)
            except Exception as e:
                print(f" ❌ Error ejecutando {name} (fallback):", e)
                df = pd.DataFrame()
        except Exception as e:
            print(f" ❌ Error ejecutando {name}:", e)
            df = pd.DataFrame()
        if df is None or not isinstance(df, pd.DataFrame):
            df = pd.DataFrame(columns=["titulo","precio","m2","dormitorios","baños","descripcion","link","imagen_url"])
        # ensure columns present
        for col in ["titulo","precio","m2","dormitorios","baños","descripcion","link","imagen_url"]:
            if col not in df.columns:
                df[col] = ""
        total_raw = len(df)
        counts_raw[name] = total_raw
        print(f"   encontrados (raw): {total_raw}")
        # normalize
        df = df.fillna("").astype(object)
        for col in ["titulo","precio","m2","dormitorios","baños","descripcion","link","imagen_url"]:
            df[col] = df[col].astype(str).str.strip().replace({None: "", "None": ""})
        # strict filters (price/dorm/banos)
        df_filtered = _filter_df_strict(df, dormitorios, banos, price_min, price_max)
        print(f"   después filtrado estricto: {len(df_filtered)}")
        # keywords: apply post-scrape for sources that didn't use keyword in URL (infocasas/nestoria/others)
        if palabras_clave and palabras_clave.strip() and name not in ("urbania", "doomos"):
            prev = len(df_filtered)
            df_filtered = _filter_by_keywords(df_filtered, palabras_clave)
            print(f"   después filtrar por keywords: {len(df_filtered)} (eliminados {prev - len(df_filtered)})")
        counts_after[name] = len(df_filtered)
        if len(df_filtered) > 0:
            df_filtered = df_filtered.copy()
            df_filtered["fuente"] = name
            frames.append(df_filtered)
    if not frames:
        print("\n⚠️ Ninguna fuente devolvió anuncios tras filtrar. Conteo raw:", counts_raw)
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined = combined.drop_duplicates(subset=["link","titulo"], keep="first").reset_index(drop=True)
    # mostrar (dinámico si IPython)
    try:
        from IPython.display import display
        display_cols = ["fuente","titulo","precio","m2","dormitorios","baños","link","imagen_url"]
        display(combined[display_cols])
    except Exception:
        display_cols = ["fuente","titulo","precio","m2","dormitorios","baños","link","imagen_url"]
        print(combined[display_cols].to_string(index=False))
    # guardar CSV
    out_dir = "/mnt/data" if os.path.exists("/mnt/data") else os.getcwd()
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "combined_anuncios_filtrados.csv")
    combined.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print("\nCSV guardado en:", csv_path)
    return combined

# -------------------- CLI --------------------
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

    combined = run_and_combine_all(zona=zona, dormitorios=dormitorios, banos=banos,
                                   price_min=pmin_val, price_max=pmax_val,
                                   palabras_clave=palabras_clave)
    print("\nProceso finalizado. Resultados (tras filtrar):", len(combined))
