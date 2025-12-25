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

# Initialize Colorama
init(autoreset=True)

# --- Configuration ---
DB_NAME = "tpb_archive.db"
GB_LIMIT = 20

class ScraperState:
    def __init__(self, start_id, target_id):
        self.keep_running = True
        self.consecutive_failures = 0
        self.start_time = time.time()
        self.total_scraped = 0
        self.initial_id = start_id
        self.target_id = target_id

    def exit_gracefully(self, signum, frame):
        if self.keep_running:
            print(f"\n{Fore.YELLOW}[!] Interruption detected. Finishing current batch...")
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

def draw_ui(current, start, total, fail_streak, eta_secs):
    bar_len = 20
    # Handle progress calculation for both ascending and descending
    total_to_do = abs(total - start)
    done = abs(current - start)
    progress = done / total_to_do if total_to_do > 0 else 0
    
    filled = int(bar_len * progress)
    bar = '█' * filled + '-' * (bar_len - filled)
    
    sys.stdout.write(f"\r\033[K|{bar}| {progress*100:5.1f}% | ETA: {format_time(eta_secs):<10} | Fail: {fail_streak:<3} | {current}")
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

def get_resume_id(default_start, desc=False):
    if not os.path.exists(DB_NAME): return default_start
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    if desc:
        # Find the lowest ID we have and go down
        cursor.execute("SELECT MIN(id) FROM (SELECT id FROM torrents UNION SELECT id FROM dead_ids)")
        res = cursor.fetchone()[0]
        conn.close()
        return min(res - 1, default_start) if res else default_start
    else:
        # Find the highest ID we have and go up
        cursor.execute("SELECT MAX(id) FROM (SELECT id FROM torrents UNION SELECT id FROM dead_ids)")
        res = cursor.fetchone()[0]
        conn.close()
        return max(res + 1, default_start) if res else default_start

def scrape_id(i, template, state):
    if not state.keep_running: return ("STOP", i)
    try:
        r = requests.get(template.format(i), headers={'User-Agent': 'Mozilla/5.0'}, timeout=7, allow_redirects=False)
        
        if r.status_code in [301, 302, 404]:
            return ("DEAD", i)
            
        soup = BeautifulSoup(r.text, 'html.parser')
        title_tag = soup.find('div', id='title') or soup.find('h1')
        title_text = title_tag.get_text(strip=True) if title_tag else ""

        if not title_tag or any(x in title_text for x in ["502", "Bad Gateway", "504", "Cloudflare"]):
            sys.stdout.write(f"\r\033[K{Fore.YELLOW}[UNK]  {i} - Potential Gateway Error\n")
            return ("UNKNOWN", i, title_text or "Gateway Error")

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
                magnet['href'] if magnet else "N/A")

    except Exception:
        return ("DEAD", i)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--link", required=True)
    parser.add_argument("--max", type=int, default=81589633)
    parser.add_argument("--fail_limit", type=int, default=1000)
    parser.add_argument("--threads", type=int, default=50)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-c", "--clean", action="store_true")
    parser.add_argument("-d", "--desc", action="store_true", help="Scrape downwards from start ID")
    args = parser.parse_args()

    parsed = urlparse(args.link)
    if args.clean and os.path.exists(DB_NAME): os.remove(DB_NAME)

    # Determine Start ID
    try:
        if "/torrent/" in parsed.path:
            start_id = int(parsed.path.rstrip('/').split('/')[-1])
        else:
            query = parse_qs(parsed.query)
            start_id = int(query.get('id', [3211594])[0])
    except:
        start_id = 3211594

    if not args.clean:
        start_id = get_resume_id(start_id, args.desc)

    target_id = 1 if args.desc else args.max
    state = ScraperState(start_id, target_id)
    signal.signal(signal.SIGINT, state.exit_gracefully)
    
    template = f"{parsed.scheme}://{parsed.netloc}/torrent/{{}}" if "/torrent/" in args.link else f"{parsed.scheme}://{parsed.netloc}/description.php?id={{}}"
    
    conn = init_db()
    cursor = conn.cursor()
    current_id = start_id

    direction_str = "DESCENDING" if args.desc else "ASCENDING"
    print(f"{Fore.CYAN}[*] Mode: {direction_str} | Start: {current_id} | Target: {target_id}")

    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        while state.keep_running:
            # Check loop exit condition
            if args.desc and current_id < target_id: break
            if not args.desc and current_id > target_id: break

            if os.path.exists(DB_NAME) and os.path.getsize(DB_NAME) / (1024**3) > GB_LIMIT:
                print(f"\n{Fore.RED}[!] Database exceeds {GB_LIMIT}GB.")
                break

            # Generate batch range based on direction
            if args.desc:
                # e.g., current is 100, threads 10 -> range(100, 89, -1) -> 100, 99...90
                end_range = max(current_id - args.threads, target_id - 1)
                batch_ids = range(current_id, end_range, -1)
            else:
                end_range = min(current_id + args.threads, target_id + 1)
                batch_ids = range(current_id, end_range)

            futures = {executor.submit(scrape_id, tid, template, state): tid for tid in batch_ids}
            batch_results = {}
            for f in as_completed(futures):
                res = f.result()
                batch_results[res[1]] = res

            # Process results in order
            for tid in sorted(batch_results.keys(), reverse=args.desc):
                res = batch_results[tid]
                if res[0] == "STOP": continue

                if res[0] in ["DEAD", "UNKNOWN"]:
                    if res[0] == "DEAD":
                        cursor.execute("INSERT OR IGNORE INTO dead_ids (id) VALUES (?)", (tid,))
                        state.consecutive_failures += 1
                    else:
                        # Mark Unknown as requested
                        cursor.execute("INSERT OR REPLACE INTO torrents (id, title, status) VALUES (?,?,?)", (tid, res[2], "UNKNOWN"))
                    
                    if args.verbose:
                        color = Fore.YELLOW if res[0] == "UNKNOWN" else Fore.WHITE
                        sys.stdout.write(f"\r\033[K{Style.DIM}{color}[{res[0][:3]}] {tid}\n")
                
                elif res[0] == "LIVE":
                    state.consecutive_failures = 0
                    cursor.execute("INSERT OR REPLACE INTO torrents (id, title, category, size, seeders, magnet, status) VALUES (?,?,?,?,?,?,?)", 
                                  (res[1], res[2], res[3], res[4], res[5], res[6], "LIVE"))
                    state.total_scraped += 1
                    if args.verbose:
                        sys.stdout.write(f"\r\033[K{Fore.GREEN}[HIT]  {res[1]} | {res[2][:45]}\n")

                if args.fail_limit > 0 and state.consecutive_failures >= args.fail_limit:
                    state.keep_running = False
                    break

            conn.commit()
            
            # Update current_id and progress
            current_id = (min(batch_ids) - 1) if args.desc else (max(batch_ids) + 1)
            elapsed = time.time() - state.start_time
            done = abs(current_id - state.initial_id)
            remaining = abs(target_id - current_id)
            eta = (elapsed / done) * remaining if done > 0 else 0
            
            draw_ui(current_id, state.initial_id, target_id, state.consecutive_failures, eta)

    conn.close()
    print(f"\n{Fore.CYAN}[*] Done. Total hits: {state.total_scraped}")

if __name__ == "__main__":
    main()