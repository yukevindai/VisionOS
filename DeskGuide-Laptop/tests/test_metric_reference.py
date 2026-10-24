"""Numeric reference observations, without importing the comparison runtime."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import numpy as np
from deskguide.metric_hand import HandDepthFusion


class ReferenceTests(unittest.TestCase):
    def test_recorded_metric_sequence_and_depth_loss(self):
        cases=json.loads((Path(__file__).parent/'fixtures/metric_reference.json').read_text())
        solver=HandDepthFusion()
        for row in cases:
            depth=np.zeros((960,960),np.float32)
            confidence=np.zeros_like(depth,np.uint8)
            for x,y,z,c in row['samples']:
                depth[y,x]=z
                confidence[y,x]=c
            f=SimpleNamespace(color=np.zeros((960,960,3),np.uint8),depth=depth,
                              confidence=confidence,K=np.array(row['K']))
            points=np.array(row['points'])
            measured,_,_=solver.resolve(f,points,1000+row['timestamp'])
            self.assertIsNotNone(measured)
            np.testing.assert_allclose(measured.xyz,row['expected'],atol=1e-7)
        f.confidence[:]=0
        measured,estimate,_=solver.resolve(f,points,1000+row['timestamp']+.03)
        self.assertIsNone(measured)
        self.assertIsNotNone(estimate)


if __name__=='__main__': unittest.main()
