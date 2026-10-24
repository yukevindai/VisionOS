import json
from pathlib import Path
import unittest
from deskguide.guidance_core import evaluate
from deskguide.engine import Engine
from deskguide.models import ROOT


class PortabilityTests(unittest.TestCase):
    def test_hazard_and_old_task_evidence_stop(self):
        self.assertEqual(evaluate((0,0,1),(0,0,.9),'VISIBLE',.05,hazard_active=True).reason,'hazard_stop')
        self.assertEqual(evaluate((0,0,1),(0,0,.9),'VISIBLE',.05,task_generation=2,evidence_generation=1).reason,'task_generation_mismatch')

    def test_measured_xyz_policy(self):
        result=evaluate((.1,-.2,1.3),(0,0,1),'VISIBLE',.05)
        self.assertAlmostEqual(result.distance_m,.14**.5)
        for actual,expected in zip(result.delta_m,(.1,-.2,.3)):
            self.assertAlmostEqual(actual,expected)

    def test_predictions_and_estimates_cannot_guide(self):
        for state in ['PREDICTED','OCCLUDED','VERIFYING_MOVE','UNCERTAIN']:
            self.assertIsNone(evaluate((0,0,1),(0,0,.9),state,.05).distance_m)
        self.assertIsNone(evaluate((0,0,1),None,'VISIBLE',.05).distance_m)

    def test_freshness_completion_and_invalid_geometry(self):
        for age in [.21,float('nan'),-1]:
            self.assertIsNone(evaluate((0,0,1),(0,0,.9),'VISIBLE',age).distance_m)
        self.assertIsNone(evaluate((0,0,float('nan')),(0,0,.9),'VISIBLE',.05).distance_m)
        self.assertEqual(evaluate((0,0,1),(0,0,.9),'VISIBLE',.05,completed=True).reason,'completed')

    def test_engine_accepts_non_phone_source_without_constructing_phone(self):
        from unittest.mock import patch
        source=object()
        with patch('deskguide.engine.Phone',side_effect=AssertionError('transport coupled')):
            engine=Engine(json.loads((ROOT/'config.json').read_text()),object(),source=source)
        self.assertIs(engine.phone,source)


if __name__=='__main__': unittest.main()
