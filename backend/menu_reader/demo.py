"""Generate clearly SIMULATED menu artwork for the offline saved-photo demonstration."""
import argparse
from pathlib import Path
from .fixtures import create


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=Path('.runtime/menu-demo/images'))
    args=parser.parse_args()
    create(args.out)
    print('SIMULATED menu artwork ready:',(args.out/'printed_menu.png').resolve())
