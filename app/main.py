import os
import threading
import time
import random
import sqlite3
import csv
from flask import Flask, render_template_string, request, jsonify, send_file

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- Free Proxy List Providers (Suggesting some robust free sources for rotation) ---
FREE_PROXY_ENDPOINTS = [
    "https://www.proxy-list.download/api/v1/get?type=https",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://www.proxyscan.io/download?type=https",
]


app = Flask(__name__)
SCRAPER_STATUS = {
    "status": "Idle", 
    "current_category": '',
    "product_links_found": 0,
    "products_scraped": 0,
    "error": '',
    "captcha_detected": False,
    "message": '',
}
SCRAPER_RESULTS = []
DB_PATH = "shein_scraper.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS products (
        product_url TEXT PRIMARY KEY,
        title TEXT,
        price TEXT,
        color TEXT,
        size TEXT,
        description TEXT,
        images TEXT
    )''')
    conn.commit()
    conn.close()

init_db()

def random_human_delay(a=1.5, b=4.5):
    time.sleep(random.uniform(a, b))

def fetch_free_proxies():
    import requests
    proxies = []
    for url in FREE_PROXY_ENDPOINTS:
        try:
            resp = requests.get(url, timeout=5)
            if resp.ok:
                proxies += [line.strip() for line in resp.text.splitlines() if line.strip()]
        except Exception:
            pass
    random.shuffle(proxies)
    return proxies

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_selenium_driver(proxy=None, headless=True):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument(f"user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        f"AppleWebKit/537.36 (KHTML, like Gecko) "
                        f"Chrome/{random.randint(110, 120)}.0.{random.randint(1000,5000)}.100 "
                        f"Safari/537.36")
    if proxy:
        options.add_argument(f'--proxy-server={proxy}')
    driver = webdriver.Chrome(ChromeDriverManager().install(), options=options)
    driver.set_window_size(random.randint(1280, 1920), random.randint(800, 1080))
    return driver

# --- UPDATED XPATHS ---
# Using all XPaths and notes provided by user
XPATHS = {
    "product_link": "placeholder",  # These actual links should be collected from category/PLP page, not from product page
    "pagination_next": "//span[@aria-label='Page Next']",
    "pagination_numbers": "//div[contains(@class, 'sui-pagination__center')]//span[contains(@class, 'sui-pagination__inner')]",
    "product_images": "//div[@class = 'product-intro']//img[@class = 'lazyload crop-image-container__img']",
    "product_title": "//div[@class = 'product-intro']//h1[contains(@class, 'product-intro__head-name')]",
    "product_price_1": "//div[@class = 'product-intro']//div[contains(@class, 'from original')]",
    "product_price_2": "//div[@class = 'product-intro']//p[contains(@class, 'product-intro__ssr-priceDel')]",
    "product_price_3": "//div[@class = 'product-intro']//div[contains(@class, 'from original')]/span",
    "product_color": "//div[@class = 'goods-color__radio-container']//div[contains(@class, 'goods-color__radio_block')]",
    "product_size": "//div[@class = 'product-intro__size']//p[contains(@class, 'product-intro__sizes-item-text--one')]",
    "product_description_btn": "//div[@class = 'product-intro__description']//span[contains(@class, 'head-icon')]",
    "product_description": "//div[@class = 'product-intro__description-table']//div[contains(@class, 'product-intro__description-table-item')]",
}

def is_captcha_page(driver):
    text = driver.page_source.lower()
    suspects = ['captcha', 'bot detection', 'verify', '/captcha/', '/challenge/']
    return any(word in text for word in suspects)

def try_bypass_human(driver):
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        random_human_delay(1, 3)
        driver.execute_script("window.scrollTo(0, 0);")
        random_human_delay(0.5, 2)
    except Exception:
        pass

def wait_for_any(driver, by, selector, timeout=15, visible=False, many=False):
    """Helper function to wait for elements with proper error handling"""
    wait = WebDriverWait(driver, timeout)
    try:
        if many:
            if visible:
                return wait.until(EC.visibility_of_any_elements_located((by, selector)))
            return wait.until(EC.presence_of_all_elements_located((by, selector)))
        else:
            if visible:
                return wait.until(EC.visibility_of_element_located((by, selector)))
            return wait.until(EC.presence_of_element_located((by, selector)))
    except Exception:
        # Return empty list or None if timeout
        return [] if many else None

def parse_products_on_page(driver):
    # Wait for product links to load before attempting to extract them
    elems = wait_for_any(driver, By.XPATH, XPATHS["product_link"], timeout=15, many=True)
    # Note: The user says these are gathered from the listing/category page, not the product page
    links = [elem.get_attribute("href") for elem in elems if elem]
    return list(set(links))

def extract_product_data(driver):
    # -- Helper functions with explicit waits --
    def get(xpath, timeout=7, visible=False):
        elem = wait_for_any(driver, By.XPATH, xpath, timeout=timeout, visible=visible)
        if elem:
            return elem.text
        return ''
    
    def get_list(xpath, attrib=None, timeout=7, visible=False):
        elems = wait_for_any(driver, By.XPATH, xpath, timeout=timeout, visible=visible, many=True)
        arr = []
        for e in elems:
            if attrib:
                arr.append(e.get_attribute(attrib))
            elif e.text:
                arr.append(e.text)
        return arr

    # Click to expand the description if possible, so description is available for scraping
    try:
        desc_btn = wait_for_any(driver, By.XPATH, XPATHS["product_description_btn"], timeout=7, visible=True)
        if desc_btn and desc_btn.is_displayed():
            # Scroll to description button before clicking to ensure it's in view
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", desc_btn)
            random_human_delay(0.3, 0.7)
            desc_btn.click()
            random_human_delay(0.5, 1.2)
    except Exception:
        pass

    # Wait for images to be visible before extracting
    images = get_list(XPATHS["product_images"], attrib="src", timeout=8, visible=True)

    # Multiple price possibilities, use all non-empty
    prices = []
    for k in ("product_price_1", "product_price_2", "product_price_3"):
        p = get(XPATHS[k])
        if p:
            prices.append(p)
    # If all price fields are empty, fallback to empty
    price = "; ".join(prices) if prices else ""

    # Colors
    color_list = get_list(XPATHS["product_color"])
    size_list = get_list(XPATHS["product_size"])

    # --- Product Description extraction as key/value pairs ---
    # Each item should have class "key" for the label and class "val" for value.
    descriptions = []
    try:
        # Wait for description items to be present after clicking the expand button
        desc_items = wait_for_any(driver, By.XPATH, XPATHS["product_description"], timeout=9, many=True)
        for item in desc_items:
            key_els = item.find_elements(By.CLASS_NAME, "key")
            val_els = item.find_elements(By.CLASS_NAME, "val")
            key = key_els[0].text.strip() if key_els else ''
            val = val_els[0].text.strip() if val_els else ''
            if key or val:
                descriptions.append({"key": key, "value": val})
    except Exception:
        pass

    return {
        "title": get(XPATHS["product_title"]),
        "price": price,
        "color": color_list,
        "size": size_list,
        "description": descriptions,  # now a list of key-value dicts
        "images": images,
    }

def save_product_row(product_url, data):
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute(
            '''INSERT OR REPLACE INTO products
            (product_url, title, price, color, size, description, images)
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (
                product_url, 
                data['title'], 
                data['price'], 
                ','.join(data['color']) if isinstance(data['color'], list) else data['color'],
                ','.join(data['size']) if isinstance(data['size'], list) else data['size'],
                str(data['description']),  # Actually JSON list of dicts
                ','.join(data['images']) if isinstance(data['images'], list) else data['images']
            )
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

def scrape_category(category_url, driver):
    product_links = set()
    page = 1
    while True:
        SCRAPER_STATUS['message'] = f"Scraping page {page} of category"
        driver.get(category_url)
        # Wait for product links to load before proceeding
        wait_for_any(driver, By.XPATH, XPATHS["product_link"], timeout=20, many=True)
        random_human_delay(2, 4)
        try_bypass_human(driver)
        if is_captcha_page(driver):
            SCRAPER_STATUS.update({
                "status": "Blocked",
                "error": "CAPTCHA encountered - manual intervention required.",
                "captcha_detected": True
            })
            return product_links
        links = parse_products_on_page(driver)
        for link in links:
            product_links.add(link)
        SCRAPER_STATUS['product_links_found'] = len(product_links)
        try:
            # Wait for next pagination button with explicit wait
            next_btn = wait_for_any(driver, By.XPATH, XPATHS["pagination_next"], timeout=10, visible=True)
            if next_btn and next_btn.is_displayed():
                # Scroll to pagination button before clicking
                driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", next_btn)
                random_human_delay(0.3, 0.7)
                next_btn.click()
            else:
                break
        except Exception:
            break
        page += 1
        random_human_delay()
    return product_links

def scrape_products(product_links, driver):
    import json
    SCRAPER_RESULTS.clear()
    count = 0
    for url in product_links:
        driver.get(url)
        # Wait for product title to appear as indicator that page has loaded
        wait_for_any(driver, By.XPATH, XPATHS["product_title"], timeout=15)
        random_human_delay(1, 3)
        try_bypass_human(driver)
        if is_captcha_page(driver):
            SCRAPER_STATUS.update({
                "status": "Blocked",
                "error": f"CAPTCHA on product page {url[:40]}...",
                "captcha_detected": True
            })
            break
        data = extract_product_data(driver)
        data['product_url'] = url
        # Save as JSON in DB for the "description" (list of key/vals)
        data_save = data.copy()
        data_save['description'] = json.dumps(data['description'])
        SCRAPER_RESULTS.append(data)
        save_product_row(url, data_save)
        count += 1
        SCRAPER_STATUS['products_scraped'] = count
    return SCRAPER_RESULTS

def scraper_job(category_urls, use_proxies=True):
    SCRAPER_STATUS.update({
        "status": "Processing",
        "product_links_found": 0,
        "products_scraped": 0,
        "error": "",
        "captcha_detected": False,
        "message": "",
    })
    proxies = fetch_free_proxies() if use_proxies else []
    for category_url in category_urls:
        SCRAPER_STATUS['current_category'] = category_url
        driver = None
        for proxy in (proxies[:] if proxies else [None]):
            try:
                SCRAPER_STATUS['message'] = f"Using proxy: {proxy or 'None'}"
                driver = get_selenium_driver(proxy)
                product_links = scrape_category(category_url, driver)
                if SCRAPER_STATUS["captcha_detected"]:
                    break
                scrape_products(product_links, driver)
                driver.quit()
                break
            except Exception as e:
                SCRAPER_STATUS['status'] = 'Error'
                SCRAPER_STATUS['error'] = f"Error: {str(e)}"
                if driver:
                    driver.quit()
                continue
        if SCRAPER_STATUS["captcha_detected"]:
            break
    SCRAPER_STATUS['status'] = "Completed" if not SCRAPER_STATUS['captcha_detected'] else "Blocked"
    SCRAPER_STATUS['message'] = "Scraping Finished" if not SCRAPER_STATUS['captcha_detected'] else SCRAPER_STATUS['error']

# --- Flask Routes (unchanged) ---

MINIMAL_TEMPLATE = """
<!DOCTYPE html>
<html><head>
<title>Advanced Shein Scraper</title>
<style>
body { font-family: Arial; margin:2em }
table { border-collapse: collapse; margin-top:1em }
th, td { border: 1px solid #ccc; padding:6px 12px }
input, textarea { width: 400px; }
</style>
<script>
function checkStatus() {
    fetch('/status').then(r=>r.json()).then(d=>{
        document.getElementById('scraper-status').innerText = JSON.stringify(d, null,2);
        if(d.status == "Processing" || d.status == "Attempting Bypass") setTimeout(checkStatus, 3000);
        if(d.status=="Completed") getDataView();
    });
}
function startScraping(){
    let urls = document.getElementById('cat-urls').value.split('\\n');
    fetch('/scrape', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({category_urls: urls})
    }).then(r=>r.json()).then(d=>{
        checkStatus();
    });
}
function getDataView() {
    fetch('/data').then(r=>r.json()).then(rows=>{
        let html = '<table><tr><th>Title</th><th>Price</th><th>Colors</th><th>Sizes</th><th>Desc</th><th>Images</th><th>URL</th></tr>';
        for(let row of rows){
            html += '<tr>'
                + `<td>${row.title}</td>`
                + `<td>${row.price}</td>`
                + `<td>${Array.isArray(row.color) ? row.color.join(", ") : row.color}</td>`
                + `<td>${Array.isArray(row.size) ? row.size.join(", ") : row.size}</td>`
                + `<td>${Array.isArray(row.description) ? row.description.map(x => x.key + ": " + x.value).join("<br>") : row.description}</td>`
                + `<td>${(row.images || '').split(',').map(u=>"<a href='"+u+"' target='_blank'>img</a>").join(' ')}</td>`
                + `<td><a href="${row.product_url}" target="_blank">Link</a></td>`
                + '</tr>';
        }
        html += "</table>";
        document.getElementById('data-view').innerHTML = html;
    });
}
</script>
</head>
<body>
<h2>Advanced Shein Scraper</h2>
<div>
    <b>Category URLs:</b><br>
    <textarea id="cat-urls" rows=5 placeholder="Paste one Shein category URL per line"></textarea><br>
    <button onclick="startScraping()">Start Scraping</button>
</div>
<div style="margin-top:1.2em">
    <b>Status:</b>
    <pre id="scraper-status">Idle</pre>
</div>
<div>
    <button onclick="getDataView()">View Data</button>
    <a href="/export" target="_blank">Download CSV</a>
</div>
<div id="data-view"></div>
<hr>
<b>Suggested Free Proxy Services Used:</b>
<ul>
<li>https://www.proxy-list.download/</li>
<li>https://github.com/TheSpeedX/PROXY-List</li>
<li>https://www.proxyscan.io/</li>
</ul>
</body></html>
"""

@app.route('/')
def index():
    return render_template_string(MINIMAL_TEMPLATE)

@app.route('/status')
def status():
    return jsonify(SCRAPER_STATUS)

@app.route('/scrape', methods=['POST'])
def scrape():
    category_urls = request.json.get('category_urls', [])
    thr = threading.Thread(target=scraper_job, args=(category_urls,))
    thr.daemon = True
    thr.start()
    return jsonify({"ok": True, "msg": "Scraping launched"})

@app.route('/data')
def data_view():
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM products").fetchall()
    conn.close()
    import json
    records = []
    for row in rows:
        rec = dict(row)
        try:
            # Display decoded list for description
            rec["description"] = json.loads(rec["description"])
        except Exception:
            pass
        records.append(rec)
    return jsonify(records)

@app.route('/export')
def export_data():
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM products").fetchall()
    conn.close()
    fp = "shein_scraped_data.csv"
    import json
    with open(fp, "w", newline="", encoding="utf8") as f:
        w = csv.writer(f)
        w.writerow(['title', 'price', 'color', 'size', 'description', 'images', 'product_url'])
        for row in rows:
            # For description, flatten JSON into key:value; for color/size/images, use as string
            try:
                description = json.loads(row["description"])
                description_str = "; ".join([f"{d['key']}: {d['value']}" for d in description])
            except Exception:
                description_str = row["description"]
            w.writerow([
                row["title"], row["price"], row["color"], row["size"],
                description_str, row["images"], row["product_url"]
            ])
    return send_file(fp, as_attachment=True)

if __name__ == "__main__":
    app.run(port=8080, debug=True)
