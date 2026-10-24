"""Small offline regression checks for the live-pipeline repairs."""
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import cv2
import numpy as np
from deskguide.stream import read_frame
from deskguide.audio import Audio
from deskguide.flow import align_detections
from deskguide.tracking import hand_covers
from test_io_audio import Reader,packet


class LivePipelineTests(unittest.TestCase):
    def test_decode_time_is_included_in_age(self):
        original=cv2.imdecode
        clock=[10.]
        def decode(*args):
            clock[0]+=.012
            return original(*args)
        with patch('deskguide.stream.cv2.imdecode',side_effect=decode),patch('deskguide.stream.time.monotonic',side_effect=lambda:clock[0]):
            f=read_frame(Reader(packet()))
        self.assertAlmostEqual(clock[0]-f.received,.012)

    def test_expired_active_audio_stops_in_next_callback(self):
        a=Audio()
        a.update([.1,0,0],.3)
        out=np.zeros((480,2),np.float32)
        a.callback(out,len(out),None,False)
        self.assertGreater(abs(out).sum(),0)
        a.update()
        a.callback(out,len(out),None,False)
        self.assertEqual(abs(out).sum(),0)

    def test_delayed_boxes_require_current_visual_support(self):
        source=np.zeros((100,100),np.uint8)
        self.assertEqual(align_detections(source,source.copy(),[(np.array([10,10,70,70]),.9)]),[])

    def test_empty_hand_bbox_space_is_not_occlusion(self):
        finger=SimpleNamespace(boxes=[np.array([0,0,100,100])],
                               hand_regions=[np.array([[0,0],[90,0],[0,90]])])
        self.assertFalse(hand_covers(np.array([80,80,90,90]),finger))
        self.assertTrue(hand_covers(np.array([20,20,40,40]),finger))


if __name__=='__main__': unittest.main()
