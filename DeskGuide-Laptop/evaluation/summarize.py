import argparse
import json
from pathlib import Path
import numpy as np


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--prototype',default='logs/pipeline-prototype-matched.json')
    p.add_argument('--baseline',default='logs/pipeline-baseline.json')
    p.add_argument('--out',default='logs/pipeline-comparison-summary.json')
    a=p.parse_args()
    baseline=json.loads(Path(a.baseline).read_text())['rows']
    prototype=json.loads(Path(a.prototype).read_text())['rows']
    rows=[]
    for left,right in zip(baseline,prototype):
        row=dict(sample=left['sample'])
        for key in ['hand_xyz','object_xyz','aim_xyz','object_box']:
            row[key+'_error']=None if left[key] is None or right[key] is None else float(np.linalg.norm(np.array(left[key])-right[key]))
        rows.append(row)
    stats={}
    for key in ['hand_xyz','object_xyz','aim_xyz','object_box']:
        values=[r[key+'_error'] for r in rows if r[key+'_error'] is not None]
        stats[key]=dict(paired=len(values),error_p50_p95=np.percentile(values,[50,95]).tolist() if values else None)
    Path(a.out).write_text(json.dumps(dict(stats=stats,rows=rows),indent=2))
    print(json.dumps(stats,indent=2))


if __name__=='__main__': main()
