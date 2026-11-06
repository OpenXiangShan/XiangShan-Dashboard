#!/usr/bin/env python3
"""Prepare or apply GCC16 SPEC06 history recovery; preparation never writes SQLite."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commit_ipc_trend.spec06_history import apply, prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'apply'])
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--git-repo', type=Path)
    parser.add_argument('--weekly-source', type=Path)
    parser.add_argument('--branch', default='origin/kunminghu-v3')
    parser.add_argument('--report-root', type=Path)
    parser.add_argument('--jobs', type=int, default=8)
    args = parser.parse_args()
    if args.action=='prepare':
        if not args.profile or not args.git_repo or not args.weekly_source or not args.report_root:
            parser.error('prepare requires --profile, --git-repo, --weekly-source, and --report-root')
        shas = subprocess.run(['git','-c',f'safe.directory={args.git_repo}',
                              '-C',str(args.git_repo),'rev-list','--first-parent',args.branch],
                              check=True,capture_output=True,text=True).stdout.splitlines()
        result=prepare(args.db,args.profile,set(shas),args.report_root,args.output,
                       args.weekly_source,args.jobs)
        print(json.dumps({'prepared_runs':len(result['runs']),'corrections':len(result['corrections'])}))
    else:
        print(json.dumps(apply(args.db,args.output),ensure_ascii=False))


if __name__=='__main__':
    main()
