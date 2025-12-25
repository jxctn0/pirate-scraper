import requests
import argparse
import sqlite3
import re
import sys
import signal
import time
import os
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from colorama import Fore, Style, init

# Initialize Colorama
init(autoreset=True)

# --- Configuration ---
DB_NAME = "tpb_archive.db"
GB_LIMIT = 20

class ScraperState:
    def __init__(self, start_id):
        self.keep_running = True
        self.consecutive_failures = 0
        self.start_time = time.time()
        self.total_scraped = 0
        self.initial_id = start_id

    def exit_gracefully(self, signum, frame):
        self.keep_running = False

def format_time(seconds):
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days > 0: parts.append(f"{days}d")
    if hours > 0: parts.append(f"{hours}h")
    if minutes > 0: parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)

def draw_ui(current, start, total, fail_streak, eta_secs):
    bar_len = 20
    progress = (current - start) / (total - start) if total > start else 0
    filled = int(bar_len * progress)
    bar = '█' * filled + '-' * (bar_len - filled)
    
    # Progress Bar UI
    sys.stdout.write(f"\r\033[K|{bar}| {progress*100:5.1f}% | ETA: {format_time(eta_secs):<10} | Fail: {fail_streak:<3} | {current}/{total}")
    sys.stdout.flush()

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS torrents 
                      (id INTEGER PRIMARY KEY, title TEXT, category TEXT, size TEXT, seeders INTEGER, magnet TEXT, scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    cursor.execute('CREATE TABLE IF NOT EXISTS dead_ids (id INTEGER PRIMARY KEY, discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
    conn.commit()
    return conn

def get_resume_id(default_start):
    if not os.path.exists(DB_NAME): return default_start
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(id) FROM (SELECT id FROM torrents UNION SELECT id FROM dead_ids)")
    res = cursor.fetchone()[0]
    conn.close()
    return max(res + 1, default_start) if res else default_start

def scrape_id(i, template, state):
    if not state.keep_running: return "STOP"
    try:
        r = requests.get(template.format(i), headers={'User-Agent': 'Mozilla/5.0'}, timeout=7, allow_redirects=False)
        
        if r.status_code in [301, 302, 404] or "not found" in r.text.lower():
            return ("DEAD", i)
            
        soup = BeautifulSoup(r.text, 'html.parser')
        title_tag = soup.find('div', id='title') or soup.find('h1')
        if not title_tag: return ("DEAD", i)
        
        category = "Unknown"
        type_label = soup.find('dt', string=re.compile(r'Type:', re.I))
        if type_label: category = type_label.find_next('dd').get_text(strip=True)

        size = re.search(r'Size:\s*(.*?Bytes)', soup.get_text(), re.I)
        seed = re.search(r'Seeders:\s*(\d+)', soup.get_text(), re.I)
        magnet = soup.find('a', href=re.compile(r'magnet:\?xt=urn:btih:'))

        return (i, title_tag.get_text(strip=True), category, size.group(1) if size else "N/A", int(seed.group(1)) if seed else 0, magnet['href'] if magnet else "N/A")
    except Exception as e:
        return ("ERROR", i, str(e))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--link", required=True)
    parser.add_argument("--max", type=int, default=81589633)
    parser.add_argument("--fail_limit", type=int, default=500)
    parser.add_argument("--threads", type=int, default=150)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    start_id = get_resume_id(3211594)
    state = ScraperState(start_id)
    signal.signal(signal.SIGINT, state.exit_gracefully)

    parsed = urlparse(args.link)
    template = f"{parsed.scheme}://{parsed.netloc}/torrent/{{}}" if "/torrent/" in args.link else f"{parsed.scheme}://{parsed.netloc}/description.php?id={{}}"
    
    conn = init_db()
    cursor = conn.cursor()
    current_id = start_id

    print(f"[*] Resuming at ID: {current_id} | Target: {args.max}")

    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        while state.keep_running and current_id <= args.max:
            if os.path.exists(DB_NAME) and os.path.getsize(DB_NAME) / (1024**3) > GB_LIMIT:
                print(f"\n{Fore.RED}[!] STOPPING: Database exceeds {GB_LIMIT}GB.")
                break

            batch_ids = range(current_id, min(current_id + args.threads, args.max + 1))
            futures = {executor.submit(scrape_id, tid, template, state): tid for tid in batch_ids}
            
            results = [f.result() for f in as_completed(futures)]
            # Logic-based sort to prevent TypeError between int and tuple
            results.sort(key=lambda x: x[1] if (isinstance(x, tuple) and x[0] in ["DEAD", "ERROR"]) else x[0])

            for res in results:
                if not state.keep_running: break

                if isinstance(res, tuple) and res[0] == "DEAD":
                    state.consecutive_failures += 1
                    cursor.execute("INSERT OR IGNORE INTO dead_ids (id) VALUES (?)", (res[1],))
                    if args.verbose:
                        sys.stdout.write(f"\r\033[K{Style.DIM}{Fore.WHITE}[DEAD] {res[1]}\n")

                elif isinstance(res, tuple) and res[0] == "ERROR":
                    # Network errors don't count towards the fail streak
                    if args.verbose:
                        sys.stdout.write(f"\r\033[K{Fore.RED}[ERR]  {res[1]} - {res[2][:30]}\n")

                else:
                    state.consecutive_failures = 0
                    cursor.execute("INSERT OR REPLACE INTO torrents (id, title, category, size, seeders, magnet) VALUES (?,?,?,?,?,?)", res)
                    state.total_scraped += 1
                    if args.verbose:
                        sys.stdout.write(f"\r\033[K{Fore.GREEN}[HIT]  {res[0]} | {res[2]} | {res[1][:45]}\n")

                if args.fail_limit > 0 and state.consecutive_failures >= args.fail_limit:
                    print(f"\n{Fore.YELLOW}[!] Limit of {args.fail_limit} consecutive dead IDs reached.")
                    state.keep_running = False
                    break

            conn.commit()
            
            # Update Progress Bar
            elapsed = time.time() - state.start_time
            done = current_id - state.initial_id + 1
            eta = (elapsed / done) * (args.max - current_id) if done > 1 else 0
            draw_ui(current_id, state.initial_id, args.max, state.consecutive_failures, eta)
            
            current_id += args.threads

    conn.close()
    print(f"\n[*] Run Complete. Data saved to {DB_NAME}.")

if __name__ == "__main__":
    main()