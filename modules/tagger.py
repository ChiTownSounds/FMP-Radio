import os
import re
import json
import logging
import subprocess
from typing import Tuple, Dict
from mutagen.mp3 import MP3
import librosa

class AutoMaster:
    def __init__(self):
        # Target vocal frequencies: 1kHz to 3kHz range
        self.vocal_min_freq = 1000
        self.vocal_max_freq = 3000

    def _determine_energy_category(self, year: str, bpm: float) -> str:
        """Determines the era and energy pooling category based on year and BPM."""
        try:
            year_int = int(str(year)[:4])
            if year_int < 1970:
                era = "Classics"
            elif 1970 <= year_int <= 1989:
                era = "Old School 70s80s"
            elif 1990 <= year_int <= 2009:
                era = "Throwbacks 90s2000s"
            else:
                era = "New School 2010+"
        except Exception:
            era = "Unsorted_Review"

        if bpm < 86:
            energy = "Smooth"
        elif 86 <= bpm <= 105:
            energy = "Mid-Tempo"
        else:
            energy = "Upbeat"

        return f"{era} ({energy})"

    def _verify_quality(self, file_path: str) -> bool:
        """Analyzes audio channel and sample-rate baselines before vaulting."""
        try:
            cmd = [
                'ffprobe', '-v', 'error', '-select_streams', 'a:0',
                '-show_entries', 'stream=channels,sample_rate', '-of', 'json', file_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(result.stdout)
            stream = data.get('streams', [{}])[0]

            channels = int(stream.get('channels', 0))
            sample_rate = int(stream.get('sample_rate', 0))

            if channels < 2 or sample_rate < 44100:
                logging.error(f"Hard Reject: Quality threshold failed (Channels: {channels}, Sample Rate: {sample_rate}Hz)")
                if os.path.exists(file_path):
                    os.remove(file_path)
                return False
            return True
        except Exception as e:
            logging.error(f"Quality verification execution crash: {e}")
            if os.path.exists(file_path):
                os.remove(file_path)
            return False

    def _analyze_audio_properties(self, file_path: str) -> Dict:
        """Extracts Cue points via FFmpeg silence detection."""
        analysis = {'bpm': 98, 'intro_sec': 0.0, 'cue_in': 0.0, 'cue_out': 0.0}
        silence_cmd = [
            'ffmpeg', '-i', file_path, '-af', 'silencedetect=noise=-48dB:duration=2.0', 
            '-f', 'null', '-'
        ]
        try:
            result = subprocess.run(silence_cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore', timeout=12)
            output = result.stderr
            
            start_matches = re.findall(r'silence_start:\s+([-\d.]+)', output)
            end_matches = re.findall(r'silence_end:\s+([-\d.]+)', output)
            
            # Check if there is an authentic front silence block starting at the very beginning (<= 0.5s)
            if start_matches and end_matches:
                first_start = float(start_matches[0])
                first_end = float(end_matches[0])
                if first_start <= 0.5:
                    analysis['cue_in'] = first_end
            
            if start_matches:
                analysis['cue_out'] = float(start_matches[-1])
                
        except subprocess.TimeoutExpired:
            print("[-] WARNING: FFmpeg processing timed out on file paths. Skipping structural properties.")
            return {}
        except Exception as e:
            logging.error(f"FFmpeg silence analysis routine failed: {e}")
        
        analysis['intro_sec'] = analysis['cue_in']
        return analysis

    def process_file(self, file_path: str, original_bitrate: str = "320k") -> Tuple[str, Dict]:
        """
        Main entry point for the AutoMaster module.
        Harvests embedded ID3 tags directly from the physical file to catch SomeDL data.
        """
        if not self._verify_quality(file_path):
            return "", {}

        clean_name = os.path.basename(file_path).replace('.mp3', '')
        
        if " - " in clean_name:
            parts = clean_name.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()
        else:
            artist = "Unknown Artist"
            title = clean_name.strip()

        metrics = self._analyze_audio_properties(file_path)
        if not metrics:
            return file_path, {}

        # Harvest embedded metadata
        true_year = "Unknown"
        lyrics_text = "Not Found"
        true_bpm = None
        embedded_url = ""
        
        # Embedded Cue Point Placeholders
        embedded_intro = None
        embedded_punch = None
        embedded_outro = None
        
        try:
            audio = MP3(file_path)
            if audio and audio.tags:
                # 1. Year Extraction (TDRC or TYER)
                tag_year = ""
                if 'TDRC' in audio.tags:
                    tag_year = str(audio.tags['TDRC'].text[0])
                elif 'TYER' in audio.tags:
                    tag_year = str(audio.tags['TYER'].text[0])
                
                if tag_year:
                    year_match = re.search(r'(\d{4})', tag_year)
                    if year_match:
                        true_year = year_match.group(1)
                
                # 2. Lyrics Extraction (USLT or SYLT)
                uslt_frames = audio.tags.getall('USLT')
                if uslt_frames:
                    lyrics_text = str(uslt_frames[0].text)
                else:
                    found_uslt = False
                    for key in audio.tags.keys():
                        if key.startswith('USLT'):
                            lyrics_text = str(audio.tags[key].text)
                            found_uslt = True
                            break
                    if not found_uslt:
                        sylt_frames = audio.tags.getall('SYLT')
                        if sylt_frames:
                            lyrics_text = str(sylt_frames[0].text)
                
                # 3. BPM Extraction (TBPM)
                if 'TBPM' in audio.tags:
                    try:
                        true_bpm = float(str(audio.tags['TBPM'].text[0]))
                    except Exception:
                        pass

                # 4. Embedded URL Extraction
                if 'WXXX' in audio.tags:
                    embedded_url = str(audio.tags['WXXX'].url)
                else:
                    for key in audio.tags.keys():
                        if key.startswith('COMM'):
                            comment_text = str(audio.tags[key].text[0])
                            if "http" in comment_text:
                                embedded_url = comment_text
                                break
                                
                # 5. Cue points extraction from user-defined TXXX text frames
                for tag in audio.tags.getall('TXXX'):
                    desc = tag.desc.upper()
                    if desc == 'INTRO_DURATION':
                        try: embedded_intro = int(tag.text[0])
                        except: pass
                    elif desc == 'PUNCH_MS':
                        try: embedded_punch = int(tag.text[0])
                        except: pass
                    elif desc == 'OUTRO_DURATION':
                        try: embedded_outro = int(tag.text[0])
                        except: pass
        except Exception as e:
            logging.error(f"Failed to extract embedded ID3 metadata: {e}")

        # Waveform beat tracking fallback
        if not true_bpm:
            try:
                import librosa
                y, sr = librosa.load(file_path, sr=22050, offset=30.0, duration=60.0)
                tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
                if hasattr(tempo, '__len__'):
                    true_bpm = float(tempo[0])
                else:
                    true_bpm = float(tempo)
            except Exception as e:
                logging.error(f"librosa BPM calculation failed: {e}")
                true_bpm = 98.0
        
        bpm_int = int(round(true_bpm))
        
        # Calculate Energy Category
        energy_category = self._determine_energy_category(true_year, true_bpm)

        # Explicit Variable Initialization
        intro_duration = 0
        outro_duration = 0
        punch_ms = 2000

        # Convert silence metrics to integer milliseconds using precise rounding
        cue_in_ms = int(round(float(metrics.get('cue_in', 0)) * 1000))
        cue_out_ms = int(round(float(metrics.get('cue_out', 0)) * 1000))

        # Read absolute total track length in milliseconds via mutagen.mp3
        total_duration_ms = 0
        try:
            audio = MP3(file_path)
            total_duration_ms = int(round(float(audio.info.length) * 1000))
        except Exception as e:
            logging.error(f"Failed to read track length via mutagen: {e}")

        # Immediate start means zero: if cue_in_ms is 0, intro_duration strictly stays 0.
        if cue_in_ms > 0:
            intro_duration = cue_in_ms
        else:
            intro_duration = 0

        # Compute outro_duration as trailing silence length at the end of the file
        # (Total Duration minus Cue Out point)
        if cue_out_ms > 0 and total_duration_ms > cue_out_ms:
            outro_duration = total_duration_ms - cue_out_ms
            punch_ms = 2000
        else:
            outro_duration = 0
            punch_ms = 2000

        # OVERRIDE with embedded cue points if they exist!
        if embedded_intro is not None:
            intro_duration = embedded_intro
        if embedded_punch is not None:
            punch_ms = embedded_punch
        if embedded_outro is not None:
            outro_duration = embedded_outro

        # Embed the final precision cue points & BPM back into the MP3 tags
        try:
            from mutagen.id3 import TXXX, TBPM
            audio = MP3(file_path)
            if audio.tags is None:
                audio.add_tags()
            audio.tags.add(TXXX(encoding=3, desc='INTRO_DURATION', text=[str(intro_duration)]))
            audio.tags.add(TXXX(encoding=3, desc='PUNCH_MS', text=[str(punch_ms)]))
            audio.tags.add(TXXX(encoding=3, desc='OUTRO_DURATION', text=[str(outro_duration)]))
            audio.tags.add(TBPM(encoding=3, text=[str(bpm_int)]))
            audio.save()
        except Exception as e:
            logging.error(f"Failed to write cue points to MP3 tags: {e}")

        metadata_updates = {
            'bitrate': original_bitrate,
            'lyrics': lyrics_text,
            'art_ratio': '1.0',
            'release_year': true_year,
            'bpm': bpm_int,
            'intro_sec': metrics.get('intro_sec', float(intro_duration) / 1000.0),
            'cue_in': cue_in_ms,
            'cue_out': cue_out_ms,
            'intro_duration': intro_duration,
            'outro_duration': outro_duration,
            'punch_ms': punch_ms,
            'energy_category': energy_category
        }
        
        # Only overwrite the URL if SomeDL successfully embedded one.
        # This protects the yt-dlp URL captured earlier by Gatekeeper.
        if embedded_url and embedded_url.strip():
            metadata_updates['url'] = embedded_url.strip()

        return file_path, metadata_updates