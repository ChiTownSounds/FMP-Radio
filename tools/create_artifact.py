import os
import re

txt_path = r"C:\FMP_Ultimate\logs\mismatches.txt"
md_path = r"C:\Users\chito\.gemini\antigravity\brain\ccd9e955-03f8-4925-a27f-7b3036f54fcb\purged_mismatches.md"

def generate():
    with open(txt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    blocks = content.strip().split("\n\n")
    
    table_rows = []
    
    for block in blocks:
        lines = block.split('\n')
        if len(lines) >= 2 and "[SEVERE:" in lines[0]:
            # Extract Severity
            sev_match = re.search(r'\[SEVERE: (\d+)%\]', lines[0])
            severity = sev_match.group(1) + "%" if sev_match else "Unknown"
            
            # Extract Expected
            expected = lines[0].split("Expected: ")[1].strip()
            
            # Extract Actual
            actual = lines[1].split("Found ID3: ")[1].strip()
            
            table_rows.append(f"| {expected} | {actual} | {severity} |")
            
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write("# Purged Mismatched Tracks\n\n")
        f.write("This document contains the full list of the 141 corrupted tracks that were completely deleted from your vault by the Auto-Scrubber.\n\n")
        f.write("> [!TIP]\n")
        f.write("> **How to Fix:** To safely get any of these songs back, queue them up in the dashboard using a **direct YouTube URL** instead of typing the name. This forces the downloader to grab the exact audio you want instead of guessing.\n\n")
        f.write("| Expected Song (What you typed) | Actual Audio (What downloaded) | Match Score (Lower is worse) |\n")
        f.write("|---|---|---|\n")
        f.write("\n".join(table_rows))
        
if __name__ == "__main__":
    generate()
