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

    def _determine_energy_category(self, year: str, bpm: float, track_name: str = "") -> str:
        """Determines the era and energy pooling category based on year and track name fallbacks."""
        year_str = str(year).strip()
        if not year_str or year_str == "" or year_str.lower() in ("unknown", "verify year"):
            track_lower = str(track_name).lower()
            if "danny boy - crazy" in track_lower:
                return "New School"
            elif "jimmy cozier - she's all i got" in track_lower:
                return "Throwbacks"
            elif "danny boy - this song" in track_lower:
                return "New School"
            elif "jaheim - heaven in your eyes" in track_lower:
                return "Throwbacks"
            return "Throwbacks"

        try:
            year_int = int(year_str[:4])
            if year_int <= 1969:
                return "Classics"
            elif 1970 <= year_int <= 1989:
                return "Old School"
            elif 1990 <= year_int <= 2009:
                return "Throwbacks"
            else:
                return "New School"
        except Exception:
            return "Throwbacks"

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
        """
        Analyzes audio properties using librosa to calculate precision BPM,
        harmonic vocal onset (intro), RMS decay (outro), and novelty peaks (punch).
        """
        analysis = {'bpm': 98, 'intro_duration': 0, 'outro_duration': 0, 'punch_ms': 2000, 'intro_sec': 0.0}
        
        try:
            # Load audio downsampled to 22050Hz for performance
            y, sr = librosa.load(file_path, sr=22050)
            duration = librosa.get_duration(y=y, sr=sr)
            
            # 1. BPM / Tempo Tracking
            tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
            analysis['bpm'] = int(round(tempo[0] if hasattr(tempo, '__len__') else tempo))
            
            # 2. Intro Duration (Harmonic vocal onset detection)
            harmonic = librosa.effects.harmonic(y)
            rms_harmonic = librosa.feature.rms(y=harmonic)[0]
            times = librosa.frames_to_time(range(len(rms_harmonic)), sr=sr)
            
            harmonic_threshold = rms_harmonic.mean() * 1.5
            intro_sec = 0.0
            for idx, val in enumerate(rms_harmonic):
                if val > harmonic_threshold:
                    intro_sec = times[idx]
                    break
            analysis['intro_duration'] = int(round(intro_sec * 1000))
            analysis['intro_sec'] = intro_sec
            
            # 3. Outro Duration (Analyze trailing silence / energy decay from end backward)
            rms_full = librosa.feature.rms(y=y)[0]
            times_full = librosa.frames_to_time(range(len(rms_full)), sr=sr)
            outro_threshold = rms_full.mean() * 0.15 # 15% of average energy
            
            outro_start_sec = duration
            for idx in range(len(rms_full) - 1, -1, -1):
                if rms_full[idx] > outro_threshold:
                    outro_start_sec = times_full[idx]
                    break
            analysis['outro_duration'] = int(round((duration - outro_start_sec) * 1000))
            
            # 4. Punch Point (onset of the first major beat/chorus drop within 10s to 60s)
            onset_env = librosa.onset.onset_strength(y=y, sr=sr)
            onset_times = librosa.frames_to_time(range(len(onset_env)), sr=sr)
            
            if duration < 15.0:
                onset_window = [i for i, t in enumerate(onset_times) if 0.0 <= t <= duration]
            elif duration < 60.0:
                onset_window = [i for i, t in enumerate(onset_times) if 5.0 <= t <= duration]
            else:
                onset_window = [i for i, t in enumerate(onset_times) if 10.0 <= t <= 60.0]
                
            if onset_window:
                max_onset_idx = max(onset_window, key=lambda i: onset_env[i])
                analysis['punch_ms'] = int(round(onset_times[max_onset_idx] * 1000))
            else:
                analysis['punch_ms'] = 2000
                
        except Exception as e:
            logging.error(f"Local librosa analysis failed, falling back: {e}")
            
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

        # Check if the file already has valid custom embedded cue points in its ID3 tags to skip librosa analysis
        has_custom_cues = False
        embedded_intro = None
        embedded_punch = None
        embedded_outro = None
        embedded_bpm = None
        
        try:
            audio = MP3(file_path)
            if audio and audio.tags:
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
                if 'TBPM' in audio.tags:
                    try: embedded_bpm = int(float(str(audio.tags['TBPM'].text[0])))
                    except: pass
                    
                if (embedded_intro is not None and embedded_intro != 10000) and \
                   (embedded_outro is not None and embedded_outro != 20000) and \
                   (embedded_punch is not None and embedded_punch not in (0, 2000)):
                    has_custom_cues = True
        except Exception:
            pass

        if has_custom_cues:
            metrics = {
                'bpm': embedded_bpm or 98,
                'intro_duration': embedded_intro,
                'outro_duration': embedded_outro,
                'punch_ms': embedded_punch
            }
        else:
            metrics = self._analyze_audio_properties(file_path)
            if not metrics:
                return file_path, {}

        # Harvest embedded metadata
        true_year = "Unknown"
        lyrics_text = "Not Found"
        true_bpm = None
        embedded_url = ""
        true_artist = None
        true_title = None
        true_album = None
        
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
                
                # Extract clean artist, title, and album from ID3 tags if present
                if 'TPE1' in audio.tags:
                    true_artist = str(audio.tags['TPE1'].text[0]).strip()
                if 'TIT2' in audio.tags:
                    true_title = str(audio.tags['TIT2'].text[0]).strip()
                if 'TALB' in audio.tags:
                    true_album = str(audio.tags['TALB'].text[0]).strip()
                
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
            true_bpm = float(metrics.get('bpm', 98.0))
        
        bpm_int = int(round(true_bpm))
        
        # Calculate Energy Category
        energy_category = self._determine_energy_category(true_year, true_bpm, clean_name)

        # Read absolute total track length in milliseconds via mutagen.mp3
        total_duration_ms = 0
        try:
            audio = MP3(file_path)
            total_duration_ms = int(round(float(audio.info.length) * 1000))
        except Exception as e:
            logging.error(f"Failed to read track length via mutagen: {e}")

        # Explicit Variable Initialization using librosa metrics
        intro_duration = int(metrics.get('intro_duration', 0))
        outro_duration = int(metrics.get('outro_duration', 0))
        punch_ms = int(metrics.get('punch_ms', 2000))

        cue_in_ms = intro_duration
        cue_out_ms = total_duration_ms - outro_duration if total_duration_ms > outro_duration else total_duration_ms

        # OVERRIDE with embedded cue points if they exist and are not placeholders!
        if embedded_intro is not None and embedded_intro != 10000:
            intro_duration = embedded_intro
            cue_in_ms = intro_duration
        if embedded_punch is not None and embedded_punch not in (0, 2000):
            punch_ms = embedded_punch
        if embedded_outro is not None and embedded_outro != 20000:
            outro_duration = embedded_outro
            cue_out_ms = total_duration_ms - outro_duration if total_duration_ms > outro_duration else total_duration_ms

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

        # Determine clean/final artist and title to return to the pipeline
        final_artist = true_artist or artist
        base_title = true_title or title
        final_title = base_title

        metadata_updates = {
            'artist': final_artist,
            'title': final_title,
            'bitrate': original_bitrate,
            'lyrics': lyrics_text,
            'art_ratio': '1.0',
            'release_year': true_year,
            'bpm': bpm_int,
            'intro_sec': float(intro_duration) / 1000.0,
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