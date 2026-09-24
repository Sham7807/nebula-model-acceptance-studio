#!/usr/bin/env python3
"""Build a self-contained macOS app, preserving the web source tree unchanged."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / 'desktop'
DIST = DESKTOP / 'dist'
APP = DIST / '小小宇宙无敌.app'
VERSION = json.loads((DESKTOP / 'version.json').read_text())


def run(*args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def ignored(directory, names):
    return [n for n in names if n in {'.git', '.venv', '__pycache__', '.pytest_cache', '.DS_Store', 'node_modules', 'reports', 'history', 'beam', '.env'}
            or n.endswith(('.pyc', '.sqlite3', '.sqlite3-wal', '.sqlite3-shm'))]


def build(dmg=True):
    uv = shutil.which('uv') or str(Path.home() / '.local/bin/uv')
    if not (DESKTOP / '.build-venv/bin/python').exists():
        run(uv, 'venv', '--python', '3.12', DESKTOP / '.build-venv')
        run(uv, 'pip', 'sync', '--python', DESKTOP / '.build-venv/bin/python', DESKTOP / 'requirements.lock')
    py = DESKTOP / '.build-venv/bin/python'
    runtime = Path(subprocess.check_output([str(py), '-c', 'import sys;print(sys.base_prefix)'], text=True).strip())
    if not (runtime / 'bin/python3.12').exists():
        raise SystemExit('A uv managed standalone Python 3.12 runtime is required.')
    run(sys.executable, ROOT / 'scripts/prepare_kvv.py', '--check')
    run('swift', 'build', '--package-path', DESKTOP, '-c', 'release')
    binary_dir = subprocess.check_output(['swift', 'build', '--package-path', str(DESKTOP), '-c', 'release', '--show-bin-path'], text=True).strip()
    DIST.mkdir(exist_ok=True)
    if APP.exists():
        shutil.rmtree(APP)
    contents = APP / 'Contents'
    resources = contents / 'Resources'
    (contents / 'MacOS').mkdir(parents=True)
    resources.mkdir()
    shutil.copy2(Path(binary_dir) / 'NebulaDesktop', contents / 'MacOS/NebulaDesktop')
    shutil.copytree(runtime, resources / 'Python', symlinks=True, ignore=ignored)
    site = Path(subprocess.check_output([str(py), '-c', 'import sysconfig;print(sysconfig.get_path("purelib"))'], text=True).strip())
    shutil.copytree(site, resources / 'Python/lib/python3.12/site-packages', dirs_exist_ok=True, symlinks=True, ignore=ignored)
    # uv's venv bootstrap is not part of a standalone interpreter installation.
    for name in ['_virtualenv.pth', '_virtualenv.py']:
        (resources / 'Python/lib/python3.12/site-packages' / name).unlink(missing_ok=True)
    workbench = resources / 'Workbench'
    web = workbench / 'multimodal-workbench'
    web.mkdir(parents=True)
    for source in (ROOT / 'multimodal-workbench').iterdir():
        if source.is_file() and source.suffix in {'.html','.css','.js','.png','.svg','.jpg','.webp','.woff2'}:
            shutil.copy2(source, web / source.name)
        elif source.is_dir() and source.name in {'assets', 'fixtures'}:
            shutil.copytree(source, web / source.name, ignore=ignored)
    integrations = workbench / 'integrations'
    integrations.mkdir()
    for source in (ROOT / 'integrations').iterdir():
        if source.is_file() and (source.suffix in {'.py','.json','.css','.html'} or source.name.startswith('LICENSE')) and not source.name.startswith('test_'):
            shutil.copy2(source, integrations / source.name)
    shutil.copytree(ROOT / 'integrations/Kimi-Vendor-Verifier', integrations / 'Kimi-Vendor-Verifier', ignore=ignored)
    extensions = integrations / 'Kimi-Vendor-Verifier/tests/k3_features'
    extensions.mkdir(exist_ok=True)
    shutil.copy2(DESKTOP / 'engine/kvv_extensions/test_workbench_capabilities.py', extensions)
    with (extensions / 'conftest.py').open('a') as f:
        f.write('\n\ndef pytest_configure(config):\n    config.addinivalue_line("markers", "k3_extension: Workbench K3 capability probe")\n')
    shutil.copytree(DESKTOP / 'engine', resources / 'engine', ignore=ignored)
    for file in (DESKTOP / 'Resources').iterdir():
        if file.is_file(): shutil.copy2(file, resources / file.name)
    for name in ['LICENSE', 'NOTICE', 'THIRD_PARTY.md']:
        if (ROOT / name).exists(): shutil.copy2(ROOT / name, resources / name)
    info = {'CFBundleName':'小小宇宙无敌', 'CFBundleDisplayName':'小小宇宙无敌',
            'CFBundleIdentifier':'com.nebula.workbench', 'CFBundleExecutable':'NebulaDesktop',
            'CFBundlePackageType':'APPL', 'CFBundleShortVersionString':VERSION['version'], 'CFBundleVersion':VERSION['build'],
            'LSMinimumSystemVersion':'14.0', 'LSApplicationCategoryType':'public.app-category.developer-tools',
            'CFBundleDevelopmentRegion':'zh_CN', 'CFBundleLocalizations':['zh-Hans','en'],
            'NSHighResolutionCapable':True, 'CFBundleIconFile':'AppIcon',
            'NSHumanReadableCopyright':'Nebula Model Acceptance Studio',
            'NSAppTransportSecurity':{'NSAllowsLocalNetworking':True, 'NSAllowsArbitraryLoadsInWebContent':True}}
    with (contents / 'Info.plist').open('wb') as f: plistlib.dump(info, f)
    iconset = DIST / 'AppIcon.iconset'
    run('swift', DESKTOP / 'scripts/make_icon.swift', iconset)
    run('iconutil', '-c', 'icns', iconset, '-o', resources / 'AppIcon.icns')
    # Remove extended attributes copied from local caches before signing.
    run('xattr', '-cr', APP)
    run(resources / 'Python/bin/python3.12', '-I', '-c', 'import httpx,openai,jsonschema,PIL,pytest; print("Bundled runtime: imports OK")')
    # Every nested executable/library must be signed before sealing the app.
    signed = set()
    magic = {b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}
    for file in resources.rglob('*'):
        if file.is_symlink():
            if not str(file.resolve()).startswith(str(APP.resolve()) + os.sep):
                raise SystemExit('Bundle has an external symlink: ' + str(file.relative_to(APP)))
        elif file.is_file():
            with file.open('rb') as f: header = f.read(4)
            if header in magic and file not in signed:
                run('codesign', '--force', '--sign', '-', file, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                signed.add(file)
    run('codesign', '--force', '--sign', '-', APP)
    run('codesign', '--verify', '--deep', '--strict', APP)
    if dmg:
        staging = DIST / 'DMG'
        if staging.exists(): shutil.rmtree(staging)
        staging.mkdir(); shutil.copytree(APP, staging / APP.name, symlinks=True)
        (staging / 'Applications').symlink_to('/Applications')
        shutil.copy2(DESKTOP / '安装说明.txt', staging)
        image = DIST / f"Nebula-Studio-{VERSION['version']}-Apple-Silicon.dmg"
        image.unlink(missing_ok=True)
        run('hdiutil', 'create', '-volname', '小小宇宙无敌', '-srcfolder', staging, '-ov', '-format', 'UDZO', image)
        shutil.rmtree(staging)
        print('DMG:', image)
    print('APP:', APP)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--no-dmg', action='store_true')
    build(dmg=not parser.parse_args().no_dmg)
