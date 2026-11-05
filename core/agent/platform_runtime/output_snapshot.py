"""Trusted sandbox-side snapshot helper, delivered via the official command SDK.

The SDK downloads as execd's privileged identity and does not offer no-follow,
nonprivileged atomic snapshots. This helper fills only that missing boundary:
a UID-1000 reader sends bounded bytes through a pipe to a root-owned snapshot.
The SDK subsequently streams only that immutable, root-private snapshot.
No user path is ever opened by the privileged parent.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys

READER = r"""
import json, os, stat, sys
LIMIT=32*1024*1024
count=0
total=0
visited=0
out=sys.stdout.buffer
def fail():
    raise ValueError("output_snapshot_rejected")
def walk(fd, prefix, depth):
    global count,total,visited
    if depth>8: fail()
    for entry in os.scandir(fd):
        name=entry.name
        visited+=1
        if visited>256 or not name or len(name)>255 or any(ord(c)<32 or ord(c)==127 for c in name) or "\\" in name: fail()
        path=prefix+"/"+name
        if len(path)>512: fail()
        info=os.stat(name,dir_fd=fd,follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            try: walk(child,path,depth+1)
            finally: os.close(child)
            continue
        file=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        try:
            before=os.fstat(file)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>LIMIT: fail()
            count+=1;total+=before.st_size
            if count>32 or total>128*1024*1024: fail()
            out.write((json.dumps({"path":path,"size":before.st_size},ensure_ascii=True)+"\n").encode())
            remaining=before.st_size
            while remaining:
                data=os.read(file,min(65536,remaining))
                if not data: fail()
                out.write(data);remaining-=len(data)
            after=os.fstat(file)
            if after.st_nlink!=1 or os.read(file,1) or (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns): fail()
        finally: os.close(file)
    out.flush()
root=os.open("/",os.O_RDONLY|os.O_DIRECTORY)
try:
    for component in ["workspace","outputs"]:
        child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root)
        os.close(root);root=child
    walk(root,"/workspace/outputs",0)
finally: os.close(root)
"""


def main():
    token = sys.argv[1]
    if not re.fullmatch(r"[a-f0-9]{32}", token) or os.getuid() != 0:
        raise ValueError("invalid_snapshot")
    parent = "/opt/open-agent/outputs"
    os.makedirs(parent, mode=0o700, exist_ok=True)
    info = os.lstat(parent)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError("invalid_snapshot_parent")
    destination = parent + "/" + token
    os.mkdir(destination, mode=0o700)
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", READER],
        user=1000,
        group=1000,
        extra_groups=[],
        env={"PATH": "/usr/bin:/bin"},
        cwd="/",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    files = []
    total = 0
    try:
        while True:
            header = process.stdout.readline(4097)
            if not header:
                break
            if len(header) > 4096 or not header.endswith(b"\n"):
                raise ValueError()
            info = json.loads(header)
            size, path = info["size"], info["path"]
            if (
                type(size) is not int
                or not 0 <= size <= 32 * 1024 * 1024
                or not isinstance(path, str)
                or not path.startswith("/workspace/outputs/")
                or len(path) > 512
                or any(ord(c) < 32 or ord(c) == 127 for c in path)
                or "\\" in path
                or any(p in {"", ".", ".."} for p in path[1:].split("/"))
            ):
                raise ValueError()
            total += size
            if len(files) >= 32 or total > 128 * 1024 * 1024:
                raise ValueError()
            target = destination + "/" + str(len(files))
            digest = hashlib.sha256()
            with open(target, "xb") as output:
                remaining = size
                while remaining:
                    data = process.stdout.read(min(65536, remaining))
                    if not data:
                        raise ValueError()
                    output.write(data)
                    digest.update(data)
                    remaining -= len(data)
            os.chmod(target, 0o400)
            files.append(
                {
                    "path": path,
                    "size": size,
                    "sha256": digest.hexdigest(),
                    "snapshot": target,
                }
            )
        if process.wait(timeout=2) != 0:
            raise ValueError()
        if not files:
            os.rmdir(destination)
        print(json.dumps({"files": files}, ensure_ascii=True))
    except BaseException:
        process.kill()
        process.wait(timeout=2)
        shutil.rmtree(destination)
        raise ValueError("output_snapshot_rejected") from None
    finally:
        process.stdout.close()


if __name__ == "__main__":
    main()
