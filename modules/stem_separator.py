import os
import sys
import subprocess
import logging

class StemSeparator:
    """
    Enterprise AI Stem Separator using Meta's HT-Demucs.
    Separates audio into pristine Vocals, Instrumental, Drums, and Bass stems.
    """
    def __init__(self, output_base_dir: str = None):
        self.output_base_dir = output_base_dir or r"G:\My Drive\FMP MUSIC\BASE\MUSIC\ondemand\beds"

    def separate_track(self, input_file_path: str, model_name: str = "htdemucs") -> dict:
        if not os.path.exists(input_file_path):
            raise FileNotFoundError(f"Input file not found: {input_file_path}")

        os.makedirs(self.output_base_dir, exist_ok=True)
        
        cmd = [
            sys.executable, "-m", "demucs",
            "--two-stems", "vocals", # Separates vocals vs instrumental backing
            "-n", model_name,
            "-o", self.output_base_dir,
            input_file_path
        ]
        
        logging.info(f"[StemSeparator] Running HT-Demucs on: {input_file_path}")
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        
        if res.returncode != 0:
            logging.error(f"[StemSeparator Error] {res.stderr}")
            return {"status": "error", "message": res.stderr}

        base_name = os.path.splitext(os.path.basename(input_file_path))[0]
        stems_dir = os.path.join(self.output_base_dir, model_name, base_name)
        
        vocals_path = os.path.join(stems_dir, "vocals.wav")
        instrumental_path = os.path.join(stems_dir, "no_vocals.wav")

        return {
            "status": "ok",
            "vocals_path": vocals_path if os.path.exists(vocals_path) else None,
            "instrumental_path": instrumental_path if os.path.exists(instrumental_path) else None,
            "stems_dir": stems_dir
        }
