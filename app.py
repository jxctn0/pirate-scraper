import math
import sqlite3
import os
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, url_for, jsonify

app = Flask(__name__)
DB_NAME = "tpb_archive.db"
MIRROR_BASE = "https://pirate-proxy.thepiratebay.rocks/torrent/"

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def build_category_tree(cursor):
    db_cats = cursor.execute("SELECT DISTINCT category FROM torrents WHERE category IS NOT NULL ORDER BY category").fetchall()
    tree = {}
    for row in db_cats:
        cat_name = row['category']
        parent = cat_name.split(" > ")[0] if " > " in cat_name else cat_name
        if parent not in tree: tree[parent] = []
        tree[parent].append(cat_name)
    return tree

@app.route('/get_details/<int:torrent_id>')
def get_details(torrent_id):
    try:
        url = f"{MIRROR_BASE}{torrent_id}"
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        soup = BeautifulSoup(r.text, 'html.parser')
        desc = soup.find('div', class_='nfo').get_text(separator='\n') if soup.find('div', class_='nfo') else "No info available."
        return jsonify({"description": desc})
    except:
        return jsonify({"error": "Failed to connect to mirror."})

@app.route('/')
@app.route('/category/<path:cat_name>')
def index(cat_name=None):
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    
    conn = get_db_connection()
    cursor = conn.cursor()

    # Filter out 502 Bad Gateway title errors
    query_parts = [
        "WHERE (title LIKE ? OR id LIKE ?)",
        "AND title NOT LIKE '%Bad Gateway%'",
        "AND title NOT LIKE '%Error code 502%'"
    ]
    params = [f'%{search}%', f'%{search}%']

    if cat_name:
        query_parts.append("AND (category = ? OR category LIKE ?)")
        params.extend([cat_name, f"{cat_name} > %"])

    filter_sql = " ".join(query_parts)
    total_count = cursor.execute(f"SELECT COUNT(*) FROM torrents {filter_sql}", params).fetchone()[0]
    
    per_page = 100
    total_pages = max(1, math.ceil(total_count / per_page))
    page = max(1, min(page, total_pages))
    
    nav_pages = sorted(list(set([1, 2, 3, 10, 20, total_pages, page, page-1, page+1])))
    nav_pages = [p for p in nav_pages if 1 <= p <= total_pages]

    offset = (page - 1) * per_page
    torrents = cursor.execute(f"SELECT * FROM torrents {filter_sql} ORDER BY id ASC LIMIT ? OFFSET ?", params + [per_page, offset]).fetchall()
    
    category_tree = build_category_tree(cursor)
    
    breadcrumbs = []
    if cat_name:
        parts = cat_name.split(" > ")
        accumulated = ""
        for p in parts:
            accumulated = f"{accumulated} > {p}" if accumulated else p
            breadcrumbs.append({'name': p, 'path': accumulated})

    conn.close()
    return render_template('index.html', torrents=torrents, category_tree=category_tree, 
                           page=page, total_pages=total_pages, nav_pages=nav_pages,
                           total_count=total_count, current_cat=cat_name, 
                           search_query=search, breadcrumbs=breadcrumbs)

if __name__ == '__main__':
    app.run(debug=True, port=5001)