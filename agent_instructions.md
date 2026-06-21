# FMP Ultimate - AI Agent Guidelines & Systems Overview

Welcome! This document provides a systems-level overview of the FMP Ultimate downloader and tagging engine to ensure any AI assistant has full context before answering questions or writing code.

---

## 1. System Ecosystem & Architecture
FMP Ultimate is a background downloader service. It operates alongside FMP Broadcaster to handle track downloads and tag normalization.

### Active Components:
* **FMP Ultimate (Downloader):** Flask application running on port **`5000`**. It exposes endpoints like `/add` to receive download requests (from FMP Broadcaster on port 8000).
* **FMP Broadcaster (Backend & UI):** FastAPI application running on port **`8000`**. Manages the SQLite database `fmp_radio.db` and schedules playout.
* **Storage Mounts:** 
  * Windows Local: `G:\My Drive\FMP MUSIC\BASE\MUSIC` (Google Drive Desktop Sync).
  * Remote Linux VM: `/home/ubuntu/music`.
  * Citrus3 FTP: Remote playout file server (`hello.citrus3.com`).

---

## 2. Core Project Files
* [app.py](file:///C:/FMP_Ultimate/app.py): Flask backend router. Processes download items sequentially from `url_queue`. Runs the background iHeart poll watcher to capture live broadcast additions.
* [modules/storage.py](file:///C:/FMP_Ultimate/modules/storage.py): Vault Manager. Manages physical file locations, duplicates check, Citrus3 uploads via Rclone, and CSV database appends.
* [modules/tagger.py](file:///C:/FMP_Ultimate/modules/tagger.py) (`AutoMaster`): Quality check verification, loudness normalization, audio tag writing, and lyrics scraping.
* [configs/fmp_data_7718.csv](file:///C:/FMP_Ultimate/configs/fmp_data_7718.csv): Master track catalog (Source of Truth).
* [tools/git_pre_commit_guard.py](file:///C:/FMP_Ultimate/tools/git_pre_commit_guard.py): Pre-commit hook that automatically formats, validates, and sorts the CSV before git commits.

---

## 3. Downloader Hierarchy
* **SomeDL** is the absolute primary downloader.
* `yt-dlp` is strictly used for fallback and playlist extraction.
* Do NOT attempt to replace SomeDL with `yt-dlp` as the primary engine.

---

## 4. File Naming & Folder Protocols (CRITICAL)
* **No Version Tags in Filenames:** Filenames on disk must not contain version tags like `(Clean)`, `[Explicit]`, or `- Radio Edit`. These tags are parsed and stored as metadata properties in the DB/CSV.
* **Strict Version Folders:** Except for `InspirationalChurch`, files must sit inside version folders: `Clean/`, `Explicit/`, or `Radio Edit/` under their respective era directory (e.g. `Throwbacks 90s2000s/Clean/Boyz II Men - On Bended Knee.mp3`).
* **Case Sensitivity:** Linux paths are case-sensitive. Always use capitalized `Clean`, `Explicit`, and `Radio Edit` in both the filesystem and database paths.
* **Inspirational Church Folder Omission:** For the target `InspirationalChurch` (church songs), version subfolders (`Clean/`, `Explicit/`, or `Radio Edit/`) are **strictly omitted** from both local G: drive, remote FTP, and database CSV/SQLite file paths. All tracks route directly under the parent folder `Shows/InspirationalChurch/` (e.g., `Shows/InspirationalChurch/Marvin Sapp - The Best In Me.mp3`). Church songs never have explicit counterparts.
* **Dynamic Target Routing Overrides:** Downloads approved via FMP Ultimate or triggered from FMP Broadcaster's Live Mix console can pass a custom target folder (e.g. `Shows/InspirationalChurch` or `Shows/Live Show/Clean`) that overrides default poller era-based sorting.
