"""Compare independent metric equations to previously saved baseline outputs."""
import json
from pathlib import Path
import sys
import time
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deskguide.metric_hand import HandDepthFusion
from deskguide.stream import Frame,prepare
sys.path.insert(0,str(Path(__file__).parent))
from compare import inputs


def main():
    reference=json.loads((ROOT/'logs/pipeline-baseline.json').read_text())
    solver=HandDepthFusion()
    rows=[]
    for row in reference['rows']:
        color,depth,K,pose,confidence,ts=inputs(ROOT/'logs/identical-input-hand-visible'/row['sample'])
        f=prepare(Frame(1,ts,ts,color,depth,K,pose,confidence,1),960,90)
        if row['keypoints'] is None:
            continue
        m,estimate,reason=solver.resolve(f,np.array(row['keypoints']),1000+row['timestamp'])
        error=None if m is None or row['hand_xyz'] is None else float(np.linalg.norm(m.xyz-np.array(row['hand_xyz'])))
        rows.append(dict(sample=row['sample'],prototype_xyz=None if m is None else m.xyz.tolist(),
                         baseline_xyz=row['hand_xyz'],error_m=error,reason=reason))
    errors=[r['error_m'] for r in rows if r['error_m'] is not None]
    report=dict(rows=rows,paired=len(errors),error_m_p50_p95=np.percentile(errors,[50,95]).tolist() if errors else None)
    (ROOT/'logs/comparison-depth-equations.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},indent=2))


if __name__=='__main__': main()
