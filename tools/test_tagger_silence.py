import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.tagger import AutoMaster

class TestTaggerSilence(unittest.TestCase):
    def setUp(self):
        self.am = AutoMaster()

    @patch('subprocess.run')
    def test_normal_front_and_end_silence(self, mock_run):
        # Case 1: Normal song with front dead air (0s to 1.5s) and trailing silence (starting at 180.5s)
        ffmpeg_output = """
[silencedetect @ 0x123] silence_start: 0
[silencedetect @ 0x123] silence_end: 1.5 | silence_duration: 1.5
[silencedetect @ 0x123] silence_start: 180.5
        """
        mock_result = MagicMock()
        mock_result.stderr = ffmpeg_output
        mock_run.return_value = mock_result

        analysis = self.am._analyze_audio_properties("dummy_path.mp3")
        
        self.assertEqual(analysis['cue_in'], 1.5)
        self.assertEqual(analysis['cue_out'], 180.5)
        self.assertEqual(analysis['intro_sec'], 1.5)

    @patch('subprocess.run')
    def test_no_front_silence_with_trailing_silence(self, mock_run):
        # Case 2: Immediate start (Otis Redding) - no front silence, only trailing silence
        ffmpeg_output = """
[silencedetect @ 0x123] silence_start: 180.5
        """
        mock_result = MagicMock()
        mock_result.stderr = ffmpeg_output
        mock_run.return_value = mock_result

        analysis = self.am._analyze_audio_properties("dummy_path.mp3")
        
        self.assertEqual(analysis['cue_in'], 0.0)
        self.assertEqual(analysis['cue_out'], 180.5)
        self.assertEqual(analysis['intro_sec'], 0.0)

    @patch('subprocess.run')
    def test_no_front_silence_but_mid_and_trailing_silence(self, mock_run):
        # Case 3: Immediate start, quiet drop in middle (60s to 62s), and trailing silence at end
        ffmpeg_output = """
[silencedetect @ 0x123] silence_start: 60.0
[silencedetect @ 0x123] silence_end: 62.0 | silence_duration: 2.0
[silencedetect @ 0x123] silence_start: 180.5
        """
        mock_result = MagicMock()
        mock_result.stderr = ffmpeg_output
        mock_run.return_value = mock_result

        analysis = self.am._analyze_audio_properties("dummy_path.mp3")
        
        self.assertEqual(analysis['cue_in'], 0.0)  # Should remain 0 since first silence starts at 60s
        self.assertEqual(analysis['cue_out'], 180.5)
        self.assertEqual(analysis['intro_sec'], 0.0)

    @patch('subprocess.run')
    def test_completely_silent_song(self, mock_run):
        # Case 4: Completely silent track starting at 0 and never ending
        ffmpeg_output = """
[silencedetect @ 0x123] silence_start: 0
        """
        mock_result = MagicMock()
        mock_result.stderr = ffmpeg_output
        mock_run.return_value = mock_result

        analysis = self.am._analyze_audio_properties("dummy_path.mp3")
        
        self.assertEqual(analysis['cue_in'], 0.0)
        self.assertEqual(analysis['cue_out'], 0.0)
        self.assertEqual(analysis['intro_sec'], 0.0)

    @patch('subprocess.run')
    def test_ffmpeg_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=12)

        analysis = self.am._analyze_audio_properties("dummy_path.mp3")
        self.assertEqual(analysis, {})

if __name__ == '__main__':
    unittest.main()

