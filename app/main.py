# Advanced Shein Scraper - Initial Implementation
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

# --- Free Proxy List Providers (Suggesting some robust free sources for rotation) ---
FREE_PROXY_ENDPOINTS = [
    # Note: For reliability, these must be parsed to extract current IP:PORT lists at runtime.
    "https://www.proxy-list.download/api/v1/get?type=https",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://www.proxyscan.io/download?type=https",
    # Add more as needed
]
# In production, periodically validate proxies for uptime before using!

# --- Flask App Initialization ---
app = Flask(__name__)
SCRAPER_STATUS = {
    "status": "Idle",  # Possible: Idle, Processing, Attempting Bypass, Blocked, Completed, Error
    "current_category": '',
    "product_links_found": 0,
    "products_scraped": 0,
    "error": '',
    "captcha_detected": False,
    "message": '',
}
SCRAPER_RESULTS = []

DB_PATH = "shein_scraper.db"

# --- SQLite Setup ---
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
        images TEXT  -- comma-separated URLs
    )''')
    conn.commit()
    conn.close()

init_db()

# --- Utility Functions ---
def random_human_delay(a=1.5, b=4.5):
    """Adds a random delay to mimic more human-like behavior."""
    time.sleep(random.uniform(a, b))

def fetch_free_proxies():
    """Fetch free proxies from provided endpoints and combine list."""
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

# --- Selenium Setup ---
def get_selenium_driver(proxy=None, headless=True):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    # Basic stealth measures
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

# --- Scraping Logic ---

XPATHS = {
    "product_link": "placeholder",
    "pagination_next": "//span[@aria-label='Page Next']",
    "pagination_numbers": "//div[contains(@class, 'sui-pagination__center')]//span[contains(@class, 'sui-pagination__inner')]",
    "product_images": "//div[@class = 'product-intro']//img[@class = 'lazyload crop-image-container__img']",
    "product_title": "placeholder",
    "product_price": "placeholder",
    "product_color": "placeholder",
    "product_size": "placeholder",
    "product_description": "placeholder",
}

def is_captcha_page(driver):
    """Detects CAPTCHA or bot challenges. User can refine this detection."""
    text = driver.page_source.lower()
    suspects = ['captcha', 'bot detection', 'verify', '/captcha/', '/challenge/']
    return any(word in text for word in suspects)

def try_bypass_human(driver):
    # Simulate random scroll, waits, mouse movement
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        random_human_delay(1, 3)
        driver.execute_script("window.scrollTo(0, 0);")
        random_human_delay(0.5, 2)
    except Exception:
        pass

def parse_products_on_page(driver):
    # Placeholder, needs accurate XPATHS
    elems = driver.find_elements(By.XPATH, XPATHS["product_link"])
    links = [elem.get_attribute("href") for elem in elems if elem]
    return list(set(links))

def extract_product_data(driver):
    # Replace with real XPATH queries
    def get(xpath):
        try:
            elem = driver.find_element(By.XPATH, xpath)
            return elem.text if elem else ''
        except:
            return ''
    def get_list(xpath):
        try:
            elems = driver.find_elements(By.XPATH, xpath)
            return [e.get_attribute("src") or e.text for e in elems if e]
        except:
            return []
    images = get_list(XPATHS["product_images"])
    return {
        "title": get(XPATHS["product_title"]),
        "price": get(XPATHS["product_price"]),
        "color": get(XPATHS["product_color"]),
        "size": get(XPATHS["product_size"]),
        "description": get(XPATHS["product_description"]),
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
            (product_url, data['title'], data['price'], data['color'],
                data['size'], data['description'], ','.join(data['images']))
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
        # Try next page navigation (placeholder: real implementation must replace below)
        try:
            next_btn = driver.find_element(By.XPATH, XPATHS["pagination_next"])
            if next_btn and next_btn.is_displayed():
                next_btn.click()
            else:
                break
        except Exception:
            break
        page += 1
        random_human_delay()
    return product_links

def scrape_products(product_links, driver):
    SCRAPER_RESULTS.clear()
    count = 0
    for url in product_links:
        driver.get(url)
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
        SCRAPER_RESULTS.append(data)
        save_product_row(url, data)
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
                break  # category done
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

# --- Flask Routes ---

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
                + `<td>${row.color}</td>`
                + `<td>${row.size}</td>`
                + `<td>${row.description}</td>`
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
    # for UI display
    records = [dict(row) for row in rows]
    return jsonify(records)

@app.route('/export')
def export_data():
    # Export DB as CSV and serve as file download
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM products").fetchall()
    conn.close()
    fp = "shein_scraped_data.csv"
    with open(fp, "w", newline="", encoding="utf8") as f:
        w = csv.writer(f)
        w.writerow(['title', 'price', 'color', 'size', 'description', 'images', 'product_url'])
        for row in rows:
            w.writerow([
                row["title"], row["price"], row["color"], row["size"],
                row["description"], row["images"], row["product_url"]
            ])
    return send_file(fp, as_attachment=True)

if __name__ == "__main__":
    app.run(port=8080, debug=True)