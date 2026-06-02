import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PYLIBS_DIR = os.path.join(SCRIPT_DIR, "pylibs")
PYPI = "https://pypi.tuna.tsinghua.edu.cn"

PACKAGES = [
    ("beautifulsoup4", "bs4"),
    ("html5lib", "html5lib"),
    ("tabulate", "tabulate"),
    ("decorator", "decorator"),
    ("six", "six"),
    ("soupsieve", "soupsieve"),
    ("webencodings", "webencodings"),
    ("xlrd", "xlrd"),
    ("jsonpath", "jsonpath"),
    ("py-mini-racer", "py_mini_racer"),
    ("apscheduler", "apscheduler"),
    ("tushare", "tushare"),
    ("stockstats", "stockstats"),
    ("pandas-ta", "pandas_ta"),
    ("akshare", "akshare"),
    ("tdxpy", "tdxpy"),
    ("mootdx", "mootdx"),
    ("loguru", "loguru"),
    ("pyyaml", "yaml"),
    ("python-dotenv", "dotenv"),
    ("requests", "requests"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("pydantic", "pydantic"),
]

def check(import_name):
    original_path = sys.path.copy()
    if PYLIBS_DIR not in sys.path:
        sys.path.insert(0, PYLIBS_DIR)
    try:
        __import__(import_name)
        return True
    except Exception:
        return False
    finally:
        sys.path = original_path

def get_release_urls(pkg_name):
    url = f"{PYPI}/pypi/{pkg_name}/json"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    version = data["info"]["version"]
    return data.get("releases", {}).get(version, [])

def pick_wheel(urls):
    py3_none = [u for u in urls if "py3-none-any.whl" in u.get("filename", "")]
    if py3_none:
        return py3_none[0]
    cp312_win = [u for u in urls if "cp312" in u.get("filename", "") and "win_amd64" in u.get("filename", "") and ".whl" in u.get("filename", "")]
    if cp312_win:
        return cp312_win[0]
    py3_win = [u for u in urls if "py3" in u.get("filename", "") and "win_amd64" in u.get("filename", "") and ".whl" in u.get("filename", "")]
    if py3_win:
        return py3_win[0]
    cp312 = [u for u in urls if "cp312" in u.get("filename", "") and ".whl" in u.get("filename", "")]
    if cp312:
        return cp312[0]
    any_whl = [u for u in urls if ".whl" in u.get("filename", "")]
    if any_whl:
        return any_whl[0]
    return None

def pick_sdist(urls):
    tar_gz = [u for u in urls if u.get("filename", "").endswith(".tar.gz")]
    if tar_gz:
        return tar_gz[0]
    zip_sdist = [u for u in urls if u.get("filename", "").endswith(".zip") and ".whl" not in u.get("filename", "")]
    if zip_sdist:
        return zip_sdist[0]
    return None

def copy_tree(src, dst):
    if not os.path.exists(dst):
        os.makedirs(dst)
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            copy_tree(s, d)
        else:
            shutil.copy2(s, d)

def install_from_wheel(wheel_url, wheel_filename):
    tmp_dir = tempfile.mkdtemp(prefix="fund_whl_")
    whl_path = os.path.join(tmp_dir, wheel_filename)
    extract_dir = os.path.join(tmp_dir, "extracted")

    try:
        urllib.request.urlretrieve(wheel_url, whl_path)
        size = os.path.getsize(whl_path)
        print(f"    Downloaded {size:,} bytes", flush=True)

        with zipfile.ZipFile(whl_path, 'r') as zf:
            zf.extractall(extract_dir)

        os.makedirs(PYLIBS_DIR, exist_ok=True)
        for item in os.listdir(extract_dir):
            src = os.path.join(extract_dir, item)
            dst = os.path.join(PYLIBS_DIR, item)
            if os.path.isdir(src):
                copy_tree(src, dst)
            else:
                shutil.copy2(src, dst)
        return True
    except Exception as e:
        print(f"    Wheel install error: {e}", flush=True)
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def install_from_sdist(sdist_url, sdist_filename):
    tmp_dir = tempfile.mkdtemp(prefix="fund_sdist_")
    sdist_path = os.path.join(tmp_dir, sdist_filename)

    try:
        urllib.request.urlretrieve(sdist_url, sdist_path)
        size = os.path.getsize(sdist_path)
        print(f"    Downloaded sdist {size:,} bytes", flush=True)

        if sdist_filename.endswith(".tar.gz"):
            with tarfile.open(sdist_path, 'r:gz') as tf:
                tf.extractall(tmp_dir)
        elif sdist_filename.endswith(".zip"):
            with zipfile.ZipFile(sdist_path, 'r') as zf:
                zf.extractall(tmp_dir)

        pkg_dirs = [d for d in os.listdir(tmp_dir)
                    if os.path.isdir(os.path.join(tmp_dir, d)) and d != "__pycache__"]
        if not pkg_dirs:
            print(f"    No extracted directory found", flush=True)
            return False

        pkg_dir = os.path.join(tmp_dir, pkg_dirs[0])

        setup_py = os.path.join(pkg_dir, "setup.py")
        setup_cfg = os.path.join(pkg_dir, "setup.cfg")
        pyproject = os.path.join(pkg_dir, "pyproject.toml")

        if os.path.exists(setup_py) or os.path.exists(setup_cfg) or os.path.exists(pyproject):
            print(f"    Building from source...", flush=True)
            result = subprocess.run(
                [sys.executable, "setup.py", "install", "--prefix", PYLIBS_DIR],
                cwd=pkg_dir,
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                print(f"    setup.py install failed, trying direct copy...", flush=True)
                for item in os.listdir(pkg_dir):
                    src = os.path.join(pkg_dir, item)
                    if os.path.isdir(src) and not item.startswith(('.', 'test', 'doc', 'example')):
                        dst = os.path.join(PYLIBS_DIR, item)
                        copy_tree(src, dst)
                    elif item.endswith('.py') and not item.startswith(('setup', 'test_')):
                        dst = os.path.join(PYLIBS_DIR, item)
                        shutil.copy2(src, dst)
                return True
            return True
        else:
            for item in os.listdir(pkg_dir):
                src = os.path.join(pkg_dir, item)
                if os.path.isdir(src) and not item.startswith(('.', 'test', 'doc')):
                    dst = os.path.join(PYLIBS_DIR, item)
                    copy_tree(src, dst)
                elif item.endswith('.py') and not item.startswith(('setup', 'test_')):
                    dst = os.path.join(PYLIBS_DIR, item)
                    shutil.copy2(src, dst)
            return True
    except Exception as e:
        print(f"    Sdist install error: {e}", flush=True)
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def install_package(pkg_name, import_name):
    if check(import_name):
        print(f"  [SKIP] {pkg_name} (already installed)", flush=True)
        return True

    print(f"  [INSTALL] {pkg_name} ...", flush=True)

    try:
        urls = get_release_urls(pkg_name)
    except Exception as e:
        print(f"    PyPI API error: {e}", flush=True)
        return False

    if not urls:
        print(f"    No releases found on {PYPI}", flush=True)
        return False

    wheel_info = pick_wheel(urls)
    if wheel_info:
        print(f"    Downloading {wheel_info['filename']} ...", flush=True)
        if install_from_wheel(wheel_info["url"], wheel_info["filename"]):
            if check(import_name):
                print(f"    [OK] {pkg_name}", flush=True)
                return True
            else:
                print(f"    Wheel installed but import failed, trying sdist...", flush=True)

    sdist_info = pick_sdist(urls)
    if sdist_info:
        print(f"    Downloading {sdist_info['filename']} ...", flush=True)
        if install_from_sdist(sdist_info["url"], sdist_info["filename"]):
            if check(import_name):
                print(f"    [OK] {pkg_name} (from sdist)", flush=True)
                return True

    print(f"    [FAILED] {pkg_name}", flush=True)
    return False

def main():
    print("=" * 55, flush=True)
    print("  Fund-Assessment 依赖安装 (清华源 → pylibs)", flush=True)
    print("=" * 55, flush=True)
    print(f"  目标目录: {PYLIBS_DIR}", flush=True)
    print(f"  PyPI 镜像: {PYPI}", flush=True)
    print()

    os.makedirs(PYLIBS_DIR, exist_ok=True)

    for pkg_name, import_name in PACKAGES:
        install_package(pkg_name, import_name)

    print("\n" + "=" * 55, flush=True)
    print("  依赖检查结果", flush=True)
    print("=" * 55, flush=True)
    all_ok = True
    for pkg_name, import_name in PACKAGES:
        status = "INSTALLED" if check(import_name) else "MISSING"
        if status == "MISSING":
            all_ok = False
        print(f"    {pkg_name}: {status}", flush=True)

    if all_ok:
        print("\n  All dependencies installed successfully!", flush=True)
    else:
        print("\n  Some dependencies are missing. Check errors above.", flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
