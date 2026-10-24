"""Offline checks only: selection, appearance, current depth and fail-closed loss."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import time
import tempfile
from unittest.mock import patch
import cv2
import numpy as np
from deskguide.stream import Frame
from deskguide.selected_target import SelectedCanTracker
from deskguide.app import Window
from deskguide.engine import Engine


class SelectedCanTests(unittest.TestCase):
    def scene(self,shift=0,depth=.55):
        rng=np.random.default_rng(92)
        color=np.full((240,320,3),25,np.uint8)
        texture=rng.integers(40,240,(100,50,3),np.uint8)
        color[65:165,110+shift:160+shift]=texture
        return Frame(1,1.,1.,color,np.full((240,320),depth,np.float32),
                     np.array([[300.,0,160],[0,300,120],[0,0,1]]),np.eye(4),
                     np.ones((240,320),np.uint8)*2,1)

    def tracker(self):
        cfg=json.loads((Path(__file__).parents[1]/'config.json').read_text())
        t=SelectedCanTracker(cfg)
        self.assertTrue(t.select(self.scene(),[110,65,160,165]))
        return t

    def test_can_tracks_with_fresh_depth_and_updates_position(self):
        t=self.tracker()
        finger=SimpleNamespace(boxes=[])
        for i in range(4):
            f=self.scene(shift=i)
            t.update(f,cv2.cvtColor(f.color,cv2.COLOR_BGR2GRAY),finger,1+i*.03)
        self.assertIsNotNone(t.measurement)
        self.assertEqual(t.state,'TRACKED')
        self.assertGreater(t.box[0],111)
        contact=t.contact(np.array([0.,0.,.4]),f.K)
        self.assertTrue(np.isfinite(contact).all())
        f=self.scene(shift=4,depth=float('nan'))
        t.update(f,cv2.cvtColor(f.color,cv2.COLOR_BGR2GRAY),finger,1.13)
        self.assertIsNone(t.measurement)

    def test_hand_cover_loss_and_epoch_cannot_authorize_geometry(self):
        t=self.tracker()
        f=self.scene()
        gray=cv2.cvtColor(f.color,cv2.COLOR_BGR2GRAY)
        t.update(f,gray,SimpleNamespace(boxes=[np.array([90,40,180,190])]),1.)
        self.assertEqual(t.state,'OCCLUDED')
        self.assertIsNone(t.measurement)
        gray[:]=0
        t.update(f,gray,SimpleNamespace(boxes=[]),1.1)
        self.assertIsNone(t.measurement)
        f.epoch=2
        t.update(f,gray,SimpleNamespace(boxes=[]),1.2)
        self.assertFalse(t.selected)

    def test_selection_coordinates_account_for_preview_padding(self):
        frame=self.scene()
        mapping=(frame,40,20,640,480)
        p=Window.image_point(SimpleNamespace(x=260,y=150),mapping)
        np.testing.assert_allclose(p,[110,65])
        self.assertIsNone(Window.image_point(SimpleNamespace(x=10,y=10),mapping))


if __name__=='__main__': unittest.main()
