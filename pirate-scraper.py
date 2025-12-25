import requests
import argparse
import sqlite3
import re
import sys
import signal
import time
import os
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
from concurrent.futures import ThreadPoolExecutor, as_completed
from colorama import Fore, Style, init

init(autoreset=True)

DB_NAME = "tpb_archive.db"
GB_LIMIT = 20

class ScraperState:
    def __init__(self, start_id, end_id):
        self.keep_running = True
        self.consecutive_failures = 0
        self.start_time = time.time()
        self.total_scraped = 0
        self.initial_id = start_id
        self.end_id = end_id

    def exit_gracefully(self, signum, frame):
        if self.keep_running:
            print(f"\n{Fore.YELLOW}[!] Interruption detected. Finishing batch...")
            self.keep_running = False

def format_time(seconds):
    if seconds < 0: return "..."
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days > 0: parts.append(f"{days}d")
    if hours > 0: parts.append(f"{hours}h")
    if minutes > 0: parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)

def draw_ui(current, start, end, fail_streak, eta_secs):
    bar_len = 20
    total_dist = abs(end - start)
    done_dist = abs(current - start)
    progress = done_dist / total_dist if total_dist > 0 else 0
    
    filled = int(bar_len * progress)
    bar = '█' * filled + '-' * (bar_len - filled)
    sys.stdout.write(f"\r\033[K|{bar}| {progress*100:5.1f}% | ETA: {format_time(eta_secs):<10} | Fail: {fail_streak:<3} | ID: {current}")
    sys.stdout.flush()

def init_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS torrents 
                  (id INTEGER PRIMARY KEY, title TEXT, category TEXT, 
                   size TEXT, seeders INTEGER, magnet TEXT, 
                   status TEXT, scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    cursor.execute('CREATE TABLE IF NOT EXISTS dead_ids (id INTEGER PRIMARY KEY, discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
    conn.commit()
    return conn

def scrape_id(i, template, state):
    if not state.keep_running: return ("STOP", i)
    target_url = template.format(i)
    try:
        r = requests.get(target_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=7, allow_redirects=False)
        
        if not r.text or not r.text.strip():
            return ("BLANK", i, target_url)

        if r.status_code in [301, 302, 404]:
            return ("DEAD", i, target_url)
            
        soup = BeautifulSoup(r.text, 'html.parser')
        title_tag = soup.find('div', id='title') or soup.find('h1')
        title_text = title_tag.get_text(strip=True) if title_tag else ""

        if not title_tag or any(x in title_text for x in ["502", "Bad Gateway", "504", "Cloudflare"]):
            return ("UNKNOWN", i, title_text or "Gateway Error", target_url)

        category = "Unknown"
        type_label = soup.find('dt', string=re.compile(r'Type:', re.I))
        if type_label: category = type_label.find_next('dd').get_text(strip=True)

        page_text = soup.get_text()
        size_match = re.search(r'Size:\s*(.*?Bytes)', page_text, re.I)
        seed_match = re.search(r'Seeders:\s*(\d+)', page_text, re.I)
        magnet = soup.find('a', href=re.compile(r'magnet:\?xt=urn:btih:'))

        return ("LIVE", i, title_text, category, 
                size_match.group(1) if size_match else "N/A", 
                int(seed_match.group(1)) if seed_match else 0, 
                magnet['href'] if magnet else "N/A", target_url)

    except Exception as e:
        return ("ERROR", i, str(e), target_url)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--link", required=True)
    parser.add_argument("--start", type=int, default=81592417)
    parser.add_argument("--end", type=int, default=3211770)
    parser.add_argument("--fail_limit", type=int, default=1000)
    parser.add_argument("--threads", type=int, default=50)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-c", "--clean", action="store_true")
    parser.add_argument("--show_link", action="store_true")
    args = parser.parse_args()

    # Determine direction automatically
    is_desc = args.start > args.end
    
    if args.clean and os.path.exists(DB_NAME): os.remove(DB_NAME)

    state = ScraperState(args.start, args.end)
    signal.signal(signal.SIGINT, state.exit_gracefully)
    
    parsed = urlparse(args.link)
    template = f"{parsed.scheme}://{parsed.netloc}/torrent/{{}}" if "/torrent/" in args.link else f"{parsed.scheme}://{parsed.netloc}/description.php?id={{}}"
    
    conn = init_db()
    cursor = conn.cursor()
    current_id = args.start

    mode_label = "DESCENDING" if is_desc else "ASCENDING"
    print(f"{Fore.CYAN}[*] Range: {args.start} -> {args.end} ({mode_label})")

    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        while state.keep_running:
            # Check loop exit
            if is_desc and current_id < args.end: break
            if not is_desc and current_id > args.end: break

            if os.path.exists(DB_NAME) and os.path.getsize(DB_NAME) / (1024**3) > GB_LIMIT:
                print(f"\n{Fore.RED}[!] DB Limit reached.")
                break

            # Batching logic
            if is_desc:
                next_stop = max(current_id - args.threads, args.end - 1)
                batch_ids = range(current_id, next_stop, -1)
            else:
                next_stop = min(current_id + args.threads, args.end + 1)
                batch_ids = range(current_id, next_stop)

            futures = {executor.submit(scrape_id, tid, template, state): tid for tid in batch_ids}
            batch_results = {}
            for f in as_completed(futures):
                res = f.result()
                batch_results[res[1]] = res

            for tid in sorted(batch_results.keys(), reverse=is_desc):
                res = batch_results[tid]
                if res[0] == "STOP": continue
                url_display = f" | URL: {res[-1]}" if args.show_link else ""

                if res[0] == "BLANK":
                    cursor.execute("INSERT OR REPLACE INTO torrents (id, title, status) VALUES (?,?,?)", (tid, "[Empty]", "BLANK"))
                    state.consecutive_failures += 1
                    sys.stdout.write(f"\r\033[K{Style.DIM}{Fore.WHITE}[BLK]  {tid}{url_display}\n")

                elif res[0] == "DEAD":
                    cursor.execute("INSERT OR IGNORE INTO dead_ids (id) VALUES (?)", (tid,))
                    state.consecutive_failures += 1
                    if args.verbose:
                        sys.stdout.write(f"\r\033[K{Style.DIM}[DEAD] {tid}{url_display}\n")

                elif res[0] == "UNKNOWN":
                    cursor.execute("INSERT OR REPLACE INTO torrents (id, title, status) VALUES (?,?,?)", (tid, res[2], "UNKNOWN"))
                    state.consecutive_failures += 1
                    sys.stdout.write(f"\r\033[K{Fore.YELLOW}[UNK]  {tid} - {res[2][:30]}{url_display}\n")

                elif res[0] == "LIVE":
                    state.consecutive_failures = 0
                    cursor.execute("INSERT OR REPLACE INTO torrents (id, title, category, size, seeders, magnet, status) VALUES (?,?,?,?,?,?,?)", 
                                  (res[1], res[2], res[3], res[4], res[5], res[6], "LIVE"))
                    state.total_scraped += 1
                    if args.verbose:
                        sys.stdout.write(f"\r\033[K{Fore.GREEN}[HIT]  {res[1]} | {res[2][:40]}{url_display}\n")

                if args.fail_limit > 0 and state.consecutive_failures >= args.fail_limit:
                    state.keep_running = False
                    break

            conn.commit()
            current_id = (min(batch_ids) - 1) if is_desc else (max(batch_ids) + 1)
            
            elapsed = time.time() - state.start_time
            done = abs(current_id - args.start)
            remaining = abs(args.end - current_id)
            eta = (elapsed / done) * remaining if done > 0 else 0
            draw_ui(current_id, args.start, args.end, state.consecutive_failures, eta)

    conn.close()
    print(f"\n{Fore.CYAN}[*] Range Complete.")

if __name__ == "__main__":
    main()