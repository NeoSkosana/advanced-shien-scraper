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
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Shein Scraper Dashboard</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <!-- Modernizable CSS for dashboard UI -->
    <style>
        :root {
            --primary: #5145cd;
            --accent: #28c76f;
            --danger: #ea5455;
            --header-bg: #282a36;
            --card-bg: #fff;
            --text-main: #222;
            --gray-bg: #f7f7fb;
        }
        body {
            background: var(--gray-bg);
            font-family: 'Segoe UI', Arial, sans-serif;
            margin: 0;
            color: var(--text-main);
        }
        .header {
            background: var(--primary);
            color: #fff;
            padding: 2rem 2rem 1rem 2rem;
        }
        .header h1 { margin: 0 0 0.1em 0; font-size: 2.1em; }
        .stat-cards {
            display: flex;
            gap: 2em;
            margin: 1.2em 0;
        }
        .card {
            background: var(--card-bg);
            box-shadow: 0 2px 10px #0001;
            border-radius: 10px;
            flex: 1;
            padding: 1em;
            display: flex;
            flex-direction: column;
            align-items: flex-start;
        }
        .card-main {
            font-size: 2em;
            margin-bottom: 0.3em;
        }
        .card-label {
            color: #666;
            letter-spacing: 0.1em;
            font-size: 0.96em;
        }
        .main-container {
            max-width: 1100px;
            margin: auto;
            background: var(--card-bg);
            box-shadow: 0 8px 38px #5145cd10;
            border-radius: 18px;
            padding: 2rem 2.5rem 3rem 2.5rem;
            margin-top: -2.5rem;
        }
        .input-section label {
            font-weight: 600;
            font-size: 1.03em;
        }
        textarea {
            width: 100%;
            min-height: 80px;
            resize: vertical;
            font-size: 1.05em;
            border-radius: 5px;
            border: 1.5px solid #dedede;
            padding: 0.7em 1em;
            font-family: inherit;
            background: #fafaff;
        }
        .actions {
            margin: 1em 0 0 0;
            display: flex;
            gap: 0.9em;
        }
        button, .export-btn {
            background: var(--primary);
            color: #fff;
            border: none;
            border-radius: 6px;
            padding: 0.7em 1.8em;
            font-size: 1.03em;
            cursor: pointer;
            font-weight: 500;
            transition: background 0.2s;
            text-decoration: none;
            display: inline-block;
        }
        button:hover, .export-btn:hover {background: var(--accent);}
        .status-wrap {
            margin: 2em 0 0 0;
        }
        .status-label {
            font-weight: 700;
            font-size: 1.07em;
            margin-right: 0.5em;
        }
        .status-indicator {
            display: inline-block;
            width: 11px; height: 11px;
            border-radius: 50%;
            margin: 0 0.4em 0 0;
            background: var(--gray-bg);
            vertical-align: middle;
        }
        .is-idle {background: #c1c1c1;}
        .is-processing {background: var(--primary);}
        .is-bypass {background: #eab308;}
        .is-blocked {background: var(--danger);}
        .is-completed {background: var(--accent);}
        .is-error {background: var(--danger);}
        .data-grid-wrap {
            margin: 3em 0 1em 0;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            background: #fff;
            border-radius: 8px;
            box-shadow: 0 1px 6px #2221;
        }
        th, td {
            border: 1px solid #eaeaea;
            padding: 0.9em 0.7em;
            text-align: left;
            font-size: 0.99em;
        }
        th {
            background: #fafafa;
            color: #333;
        }
        tbody tr:nth-child(odd) {background: #f8f7fc;}
        .img-list a {
            margin-right: 4px;
            background: #eee;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.87em;
        }
        @media (max-width: 900px) {
            .stat-cards { flex-direction: column; gap: 0.8em; }
            .main-container { padding: 1.3rem; }
        }
        @media (max-width: 600px) {
            .header, .main-container { padding-left: 0.6em; padding-right: 0.6em; }
        }
        .progress-bar-wrap { margin: 0.7em 0; height: 13px; background: #e8ecfa; border-radius: 5px; overflow: hidden;}
        .progress-bar { height: 100%; background: var(--primary);}
    </style>
</head>
<body>
    <div class="header">
        <h1>Shein Product Scraper Dashboard</h1>
        <div class="stat-cards" id="dashboard-cards">
            <!-- Cards updated via JS -->
            <div class="card">
                <span class="card-main" id="scraped-products-count">0</span>
                <div class="card-label">Products Scraped</div>
            </div>
            <div class="card">
                <span class="card-main" id="product-links-count">0</span>
                <div class="card-label">Links Found</div>
            </div>
            <div class="card">
                <span class="card-main" id="current-category">-</span>
                <div class="card-label">Current Category</div>
            </div>
            <div class="card">
                <span class="card-main status-indicator is-idle" id="status-indicator"></span>
                <div class="card-label" id="scraper-status-text">Status</div>
            </div>
        </div>
    </div>
    <div class="main-container">
        <div class="input-section">
            <label for="cat-urls">Input Shein Category URLs:</label><br>
            <textarea id="cat-urls" rows="3" placeholder="Paste one Shein category URL per line"></textarea>
            <div class="actions">
                <button id="scrape-btn">Start Scraping</button>
                <a class="export-btn" href="/export" target="_blank">Export CSV</a>
                <button id="refresh-btn" type="button">Refresh Table</button>
            </div>
        </div>
        <div class="status-wrap">
            <span class="status-label">Progress:</span>
            <span id="progress-prod">0</span>/<span id="progress-total">0</span>
            <div class="progress-bar-wrap">
                <div class="progress-bar" id="progress-bar" style="width:0%;"></div>
            </div>
            <span class="status-label">Last Message:</span>
            <span id="scraper-message">Idle</span>
        </div>
        <div class="data-grid-wrap">
            <h2 style="margin-bottom:0.5em;">Scraped Products</h2>
            <div id="data-view"></div>
        </div>
    </div>
    <footer style="margin:3em 0 1em 0; text-align:center; font-size:0.95em; color:#888;">
        &copy; 2024 Carregar (Pty) Ltd &ndash; Shein Advanced Scraper Dashboard
    </footer>
    <script>
        // Update stat cards and UI from status
        function updateDashboard(status) {
            document.getElementById('scraped-products-count').textContent = status.products_scraped;
            document.getElementById('product-links-count').textContent = status.product_links_found;
            document.getElementById('current-category').textContent = status.current_category || "-";
            document.getElementById('progress-prod').textContent = status.products_scraped;
            document.getElementById('progress-total').textContent = status.product_links_found;

            // update status indicator and text
            var el = document.getElementById('status-indicator');
            el.className = 'card-main status-indicator';
            var statusMap = {
                "Idle": "is-idle",
                "Processing": "is-processing",
                "Attempting Bypass": "is-bypass",
                "Blocked": "is-blocked",
                "Completed": "is-completed",
                "Error": "is-error"
            };
            var state = status.status;
            if (statusMap[state]) el.classList.add(statusMap[state]);
            document.getElementById('scraper-status-text').textContent = state;

            // update progress bar
            let perc = (status.product_links_found > 0)
                ? Math.round(100.0 * status.products_scraped/status.product_links_found)
                : 0;
            document.getElementById('progress-bar').style.width = perc + "%";

            document.getElementById('scraper-message').textContent = status.message || "";
        }

        function fetchStatus() {
            fetch('/status').then(r=>r.json()).then(d=>{
                updateDashboard(d);
                if(d.status == "Processing" || d.status == "Attempting Bypass") setTimeout(fetchStatus, 3000);
            });
        }

        function startScraping(){
            let urls = document.getElementById('cat-urls').value.split('\n');
            fetch('/scrape', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({category_urls: urls})
            }).then(r=>r.json()).then(d=>{
                fetchStatus();
                setTimeout(loadTable, 3000);
            });
        }

        function loadTable() {
            fetch('/data').then(r=>r.json()).then(rows=>{
                let html = '<table><thead><tr><th>Title</th><th>Price</th><th>Colors</th><th>Sizes</th><th>Description</th><th>Images</th><th>Link</th></tr></thead><tbody>';
                for(let row of rows){
                    html += '<tr>'
                        + `<td>${row.title}</td>`
                        + `<td>${row.price}</td>`
                        + `<td>${Array.isArray(row.color) ? row.color.join(", ") : row.color}</td>`
                        + `<td>${Array.isArray(row.size) ? row.size.join(", ") : row.size}</td>`
                        + `<td style="max-width:250px">${Array.isArray(row.description) ? row.description.map(x => x.key + ": " + x.value).join("<br>") : row.description}</td>`
                        + `<td class="img-list">${(row.images || '').split(',').map(u=>"<a href='"+u+"' target='_blank'>img</a>").join(' ')}</td>`
                        + `<td><a href="${row.product_url}" target="_blank">Link</a></td>`
                        + '</tr>';
                }
                html += "</tbody></table>";
                document.getElementById('data-view').innerHTML = html;
            });
        }

        document.getElementById('scrape-btn').onclick = startScraping;
        document.getElementById('refresh-btn').onclick = loadTable;
        window.onload = function() {
            fetchStatus();
            loadTable();
        };
    </script>
</body>
</html>
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
