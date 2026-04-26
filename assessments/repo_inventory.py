#!/usr/bin/env python3
"""Quick inventory of all D-Sorganization repos for assessment."""
import os, subprocess, json
from pathlib import Path

REPOS = [
    'AffineDrift','Bitnet_Launcher','controls','Drake_Models','Games',
    'Gasification_Model','Maxwell-Daemon','MEB_Conversion','MLProjects',
    'Movement-Optimizer','MuJoCo_Models','OpenSim_Models','Pinocchio_Models',
    'Playground','Programmatic-PID','QuatEngine','Repository_Management',
    'runner-dashboard','Tools','Tools_Private','UpstreamDrift','Worksheet-Workshop'
]

def main():
    results = []
    for r in REPOS:
        p = Path(r)
        if not p.exists():
            results.append({'repo': r, 'exists': False})
            continue
        try:
            sha = subprocess.check_output(['git','-C',r,'log','-1','--format=%h'], stderr=subprocess.DEVNULL).decode().strip()
            sha_long = subprocess.check_output(['git','-C',r,'log','-1','--format=%H'], stderr=subprocess.DEVNULL).decode().strip()
            branch = subprocess.check_output(['git','-C',r,'rev-parse','--abbrev-ref','HEAD'], stderr=subprocess.DEVNULL).decode().strip()
            dirty = bool(subprocess.check_output(['git','-C',r,'status','--porcelain'], stderr=subprocess.DEVNULL).decode().strip())
        except Exception as e:
            sha, sha_long, branch, dirty = 'ERR','ERR','ERR',False
        # quick file count
        nfiles = sum(1 for _ in p.rglob('*') if _.is_file() and _.suffix in {'.py','.js','.ts','.rs','.go','.java','.cpp','.c','.cs','.md','.yml','.yaml','.json','.toml','.sh','.ps1'})
        results.append({
            'repo': r, 'exists': True,
            'head_short': sha, 'head_long': sha_long,
            'branch': branch, 'dirty': dirty,
            'src_files': nfiles
        })
    print(json.dumps(results, indent=2))

if __name__ == '__main__':
    main()