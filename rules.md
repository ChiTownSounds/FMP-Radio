# FMP Ultimate - Agent Guidelines

## 1. Downloader Hierarchy
* **SomeDL** is the absolute primary downloader. 
* `yt-dlp` is strictly used for fallback and playlist extraction. 
* Do NOT attempt to replace SomeDL with `yt-dlp` as the primary engine.

## 2. Database Integrity
* Do not alter the `fmp_data_7718.csv` structure without explicit permission. 
* Always use `csv.DictReader` and dynamic header matching to avoid structural drift.

## 3. Network & Storage
* Do not bypass the exponential backoff logic for Citrus3 FTP connections.
* Treat the `.env` file as the sole source of truth for passwords and API keys. Do not hardcode credentials.