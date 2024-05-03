'''
Build Gui.py
'''
import json
import platform
import os  # for checking for dirs
import re
from json.decoder import JSONDecodeError
from subprocess import Popen, PIPE, STDOUT, CalledProcessError

DEST_DIRECTORY = "."

# UPX greatly reduces the filesize.  You can get this utility from https://upx.github.io/
# just place it in a subdirectory named "upx" and this script will find it
UPX_DIR = "upx"
if os.path.isdir(os.path.join(".", UPX_DIR)):
    upx_string = f"--upx-dir={UPX_DIR}"
else:
    upx_string = ""
GO = True

def set_output(name, value):
    with open(os.environ['GITHUB_OUTPUT'], 'a') as fh:
        print(f'{name}={value}', file=fh)

def run_build():
    global GO

    print("Building via Python %s" % platform.python_version())

    PYINST_EXECUTABLE = "pyinstaller"
    args = [
        os.path.join("source", "Gui.spec").replace(os.sep, os.sep * 2),
        upx_string,
        "-y",
        f"--distpath={DEST_DIRECTORY}"
    ]
    errs = []
    strs = []
    print("PyInstaller args: %s" % " ".join(args))
    cmd = [
      PYINST_EXECUTABLE,
      *args
    ]

    ret = {
      "stdout": [],
      "stderr": []
    }

    with Popen(cmd, stdout=PIPE, stderr=STDOUT, bufsize=1, universal_newlines=True) as p:
      for line in p.stdout:
        ret["stdout"].append(line)
        print(line, end='')
      # if p.stderr:
      #   for line in p.stderr:
      #     ret["stderr"].append(line)
      #     print(line, end='')
      # if p.returncode != 0:
      #   raise CalledProcessError(p.returncode, p.args)

    for key in ["stdout","stderr"]:
      if len(ret[key]) > 0:
        for line in ret[key]:
          if "NotCompressibleException" in line.strip():
            print(line)
            errs.append(line.strip())
          if "UPX" in line:
            print(line)
          elif "NotCompressibleException" in line.strip():
            strAdd = re.search(r'api-ms-win-(?:[^-]*)-([^-]*)', line.strip()).group(1)
            strs.append(strAdd)
            errs.append(line.strip())
    if len(errs) > 0:
      print("=" * 10)
      print("| ERRORS |")
      print("=" * 10)
      print("\n".join(errs))
    else:
      GO = False
    if len(strs) > 0:
      with open(os.path.join(".","resources","app","meta","manifests","excluded_dlls.json"), "w+", encoding="utf-8") as dllsManifest:
        dlls = []
        try:
          dlls = json.load(dllsManifest)
        except JSONDecodeError as e:
          dlls = []
        #   raise ValueError("Windows DLLs manifest malformed!")

        newDLLs = dlls

        addDLLs = sorted(list(set(strs)))

        if addDLLs:
            set_output("error_dlls","Failed to compress DLLs!")

        newDLLs += addDLLs
        newDLLs = sorted(list(set(newDLLs)))

        if newDLLs:
            dllsManifest.seek(0)
            dllsManifest.truncate()
            dllsManifest.write(json.dumps(sorted(newDLLs), indent=2))

        print("Old DLLs")
        print(f"{json.dumps(sorted(dlls))}")
        print("New DLLs")
        print(f"{json.dumps(sorted(newDLLs))}")
        print("Add DLLs")
        print(f"{json.dumps(sorted(addDLLs))}")
    print("")


if __name__ == "__main__":
    while GO:
        run_build()
        GO = False
