# 🏴‍☠️ Pirate Archive Explorer

A high-performance, Flask-based web interface for exploring and managing archived torrent metadata. This project is specifically designed to handle large-scale SQLite databases with a focus on clean UI, fast navigation, and real-time metadata recovery.

## 🚀 Key Features

### 🔍 Smart Data Management
* **502 Error Filtering**: Automatically scrubs "Bad Gateway" and "Cloudflare Error" titles from your view, ensuring only valid scraped data is displayed.
* **Live Metadata Fetching**: Fetches `.nfo` descriptions on-demand from active mirrors to keep the local database lightweight.

### 🎨 Advanced Theming System
* **Material Base Architecture**: Uses a core structural CSS file with swappable color "skins."
* **Built-in Themes**: 
    * **Material Skins**: Teal (Default), Pink, Blue, and Yellow.
    * **Classic Pirate**: A nostalgic beige/parchment theme mimicking the OG site.
    * **Simple HTML**: A raw, unstyled minimalist mode for low-resource environments.
* **Persistent Settings**: Remembers your theme preference across sessions using `localStorage`.

### 🧭 Power-User Navigation
* **Milestone Pagination**: Quick-access links to pages 1, 2, 3, 10, 20, and the Max Page.
* **Jump to Page**: A dedicated input field to teleport to any page in the archive instantly.
* **Tag & Breadcrumb System**: Navigate via a collapsible category tree with breadcrumb trails to track your path.

### 🧲 Action Toolkit
* **Copy Magnet**: One-click URI copying with a visual toast notification.
* **Copy Link**: Quickly grab the direct mirror URL.
* **Launch (🚀)**: Open the mirror page in a new tab for comments and file lists.

---

## 📂 Project Structure

```text
pirate-scraper/
├── app.py                # Flask Backend & SQLite Logic
├── tpb_archive.db        # The SQLite Database
├── static/
│   └── css/
│       ├── material-base.css     # Core Layout & Variables
│       ├── material-teal.css     # Teal Skin
│       ├── material-pink.css     # Pink Skin
│       ├── material-blue.css     # Blue Skin
│       ├── material-yellow.css   # Yellow Skin
│       ├── pirate.css            # Standalone Classic Theme
│       └── simplehtml.css        # Standalone Retro Theme
└── templates/
    └── index.html        # Jinja2 Master Template

```

---

## 🛠️ Setup & Installation

### 1. Requirements

* **Python 3.12+** (Fully compatible with **Python 3.14**)
* **Flask** (Web Framework)
* **Requests & BeautifulSoup4** (For live NFO fetching)

### 2. Installation

```bash
pip install flask requests beautifulsoup4

```

### 3. Database Schema

The app expects a table named `torrents` with the following structure:
| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | INTEGER | The original Torrent ID |
| `title` | TEXT | Torrent Name |
| `category` | TEXT | Path format (e.g., "Video > Movies") |
| `magnet` | TEXT | The magnet URI string |
| `seeders` | INTEGER | Number of seeds |

### 4. Execution

```bash
python app.py

```

Open your browser to `http://127.0.0.1:5001`.

---

## 📖 UI Shortcuts

* **Click Title**: Expand/Collapse the NFO description box.
* **Enter on Jump**: Type a page number and hit `Enter` to navigate.
* **Sidebar Toggle**: Click the chevron in the sidebar to reveal sub-categories.

---

## ⚖️ Disclaimer

This project is for archival research and educational purposes. Always adhere to local regulations regarding the use and distribution of torrent metadata.

```

---

### One final touch?
Since you're working with specific IDs (starting at **ID 3211594** as per your notes), would you like me to add a **"Mark as Dead"** button to the interface that logs dead IDs to a separate text file or database table for your future scrapes?