"""Compile the disposable probe against a checksum-verified deployed API JAR.

Uses existing Java 21 and packaged dependencies; no Maven/network resolution.
Build inputs are retained beside the output for inspection, never auto-deleted.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import os
import subprocess
import zipfile


def main():
    parser=argparse.ArgumentParser()
    for name in ['javac','platform-jar','output']: parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--platform-sha256',required=True)
    parser.add_argument('--probe',choices=['OutputStoreRecoveryProbe','OutputReconciliationProbe'],default='OutputStoreRecoveryProbe')
    args=parser.parse_args()
    assert sha256(args.platform_jar.read_bytes()).hexdigest()==args.platform_sha256
    assert not args.output.exists()
    folder=args.output.with_suffix('.build')
    folder.mkdir(exist_ok=False)
    with zipfile.ZipFile(args.platform_jar) as archive:
        for member in archive.namelist():
            if member.startswith('BOOT-INF/lib/') and member.endswith('.jar'):
                target=folder/'lib'/Path(member).name
            elif member.startswith('BOOT-INF/classes/com/iflytek/astron/console/hub/platform/') and member.endswith('.class'):
                target=folder/'classes'/member.removeprefix('BOOT-INF/classes/')
            else: continue
            assert target.resolve().is_relative_to(folder.resolve())
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(member))
    source=Path(__file__).with_name(args.probe+'.java')
    subprocess.run([str(args.javac),'--release','21','-proc:none','-encoding','UTF-8','-cp',
        str(folder/'classes')+os.pathsep+str(folder/'lib/*'),'-d',str(folder/'classes'),str(source)],check=True)
    with zipfile.ZipFile(args.output,'w',zipfile.ZIP_DEFLATED) as archive:
        for file in sorted((folder/'classes').glob(args.probe+'*.class')):
            info=zipfile.ZipInfo(file.name,date_time=(2020,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,file.read_bytes())
    result={'platform_sha256':args.platform_sha256,'source_sha256':sha256(source.read_bytes()).hexdigest(),
        'probe_sha256':sha256(args.output.read_bytes()).hexdigest(),'bytes':args.output.stat().st_size}
    args.output.with_suffix('.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__': main()
