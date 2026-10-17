"""Verify pre-downloaded wheels against official PyPI and build a hash-locked bundle.

Run only on the internet-connected preparation machine. This does not install wheels.
"""
import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.request import urlopen
import zipfile


def build(source, destination):
    if destination.exists():
        raise ValueError('출력 폴더가 이미 있습니다. 덮어쓰지 않고 중단합니다.')
    entries = []
    for path in sorted(source.glob('*.whl')):
        with zipfile.ZipFile(path) as archive:
            meta = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
            if len(meta) != 1 or archive.testzip():
                raise ValueError(f'잘못된 wheel: {path.name}')
            metadata = BytesParser().parsebytes(archive.read(meta[0]))
        name, version = metadata['Name'], metadata['Version']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or not re.fullmatch(r'[A-Za-z0-9_.+!-]+', version):
            raise ValueError('잘못된 패키지 이름/버전')
        with path.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        with urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=30) as response:
            release = json.load(response)
        match = next((f for f in release['urls'] if f['filename'] == path.name), None)
        if not match or match['digests']['sha256'] != sha or match['size'] != path.stat().st_size:
            raise ValueError(f'공식 PyPI 해시 불일치: {path.name}')
        if match.get('yanked'):
            raise ValueError(f'배포 철회된 wheel: {path.name}')
        entries.append(dict(name=name, version=version, filename=path.name, size=path.stat().st_size,
                            sha256=sha, source=match['url']))
    if not entries:
        raise ValueError('wheel이 없습니다.')
    destination.mkdir(parents=True)
    (destination / 'wheels').mkdir()
    for entry in entries:
        shutil.copy2(source / entry['filename'], destination / 'wheels' / entry['filename'])
    lock = '\n'.join(f"{e['name']}=={e['version']} --hash=sha256:{e['sha256']}" for e in entries) + '\n'
    (destination / 'requirements.lock').write_text(lock)
    manifest = dict(schema=1, purpose='XRD matplotlib repair; not a complete Edge installation',
                    target=dict(system='Linux', machine='aarch64', implementation='CPython', python='3.12', glibc_min='2.28'),
                    requirements_sha256=hashlib.sha256(lock.encode()).hexdigest(), wheels=entries)
    (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(f'{len(entries)} official wheels, {sum(e["size"] for e in entries):,} bytes: {destination}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    build(args.source, args.destination)
