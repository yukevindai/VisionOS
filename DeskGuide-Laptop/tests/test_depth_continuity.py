import unittest
import numpy as np
from deskguide.stream import Frame
from deskguide.geometry import fingertip_depth,palm_reference,depth_at


def scene():
    return Frame(1,1,1,np.zeros((48,64,3),np.uint8),np.full((12,16),np.nan,np.float32),
                 np.array([[60.,0,32],[0,60,24],[0,0,1]]),np.eye(4),np.full((12,16),2,np.uint8))


class DepthContinuity(unittest.TestCase):
    def test_rgb_pixel_maps_to_its_depth_cell_without_half_cell_bias(self):
        f=scene()
        f.depth[6,8]=.4
        f.depth[6,7]=.4
        f.depth[5,8]=.4
        # RGB x=35 lies inside low-res cell 8, not cell 9.
        m=fingertip_depth(f,[35,24])
        self.assertIsNotNone(m)
        self.assertAlmostEqual(m.xyz[2],.4,places=5)

    def test_thin_tip_center_supported_cluster(self):
        f=scene()
        f.depth[5:8,7:10]=1.
        f.depth[6,7:10]=.4
        m=fingertip_depth(f,[32,24])
        self.assertIsNotNone(m)
        self.assertAlmostEqual(m.xyz[2],.4,places=5)
        self.assertIsNone(depth_at(f,[32,24]))

    def test_center_low_confidence_is_not_silently_accepted(self):
        f=scene()
        f.depth[:]=.4
        f.confidence[6,8]=0
        self.assertIsNone(fingertip_depth(f,[32,24]))

    def test_no_fingertip_center_no_measurement(self):
        f=scene()
        f.depth[:]=.4
        f.depth[6,8]=np.nan
        self.assertIsNone(fingertip_depth(f,[32,24]))

    def test_palm_reference_does_not_change_missing_tip(self):
        f=scene()
        f.depth[:]=.5
        landmarks=np.tile([32.,24.],(21,1))
        self.assertAlmostEqual(palm_reference(f,landmarks),.5)
        f.depth[6,8]=np.nan
        self.assertIsNone(fingertip_depth(f,[32,24]))


if __name__=='__main__': unittest.main()
