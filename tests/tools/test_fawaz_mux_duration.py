"""Regression coverage for audio mux duration bounds; no media/providers required."""
from pathlib import Path
import sys,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from tools.video.video_compose import VideoCompose
class MuxDurationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.video=Path(self.temp.name)/'v.mp4';self.video.write_bytes(b'original')
  self.audio=Path(self.temp.name)/'a.wav';self.audio.write_bytes(b'audio')
 def test_mux_is_bounded_to_original_duration(self):
  calls=[]
  def fake(cmd,**kwargs):
   calls.append((cmd,kwargs))
   if cmd[0]=='ffprobe':return SimpleNamespace(stdout='3.000000\n')
   Path(cmd[-1]).write_bytes(b'muxed')
   return SimpleNamespace(stdout='')
  tool=VideoCompose()
  with patch.object(tool,'run_command',side_effect=fake):
   result=tool._mux_external_audio(self.video,self.audio)
  self.assertTrue(result.success,result.error)
  cmd,kw=calls[1]
  self.assertEqual(cmd[cmd.index('-t')+1],'3.000000')
  self.assertIn('-nostdin',cmd);self.assertEqual(kw['timeout'],60)
  self.assertEqual(self.video.read_bytes(),b'muxed')
 def test_invalid_duration_preserves_original(self):
  for duration in ['nan','inf','0','-2','not-a-duration']:
   with self.subTest(duration=duration):
    tool=VideoCompose()
    with patch.object(tool,'run_command',return_value=SimpleNamespace(stdout=duration)) as run:
     result=tool._mux_external_audio(self.video,self.audio)
    self.assertFalse(result.success)
    self.assertEqual(run.call_count,1)
    self.assertEqual(self.video.read_bytes(),b'original')
 def test_encoder_failure_preserves_original(self):
  tool=VideoCompose()
  with patch.object(tool,'run_command',side_effect=[SimpleNamespace(stdout='3'),RuntimeError('encoder failed')]):
   result=tool._mux_external_audio(self.video,self.audio)
  self.assertFalse(result.success)
  self.assertEqual(self.video.read_bytes(),b'original')
if __name__=='__main__':unittest.main()
