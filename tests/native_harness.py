"""Run isolated production C functions with a host C compiler.

Requires Python 3 and cc/gcc/clang, or cl in a Visual Studio developer shell.
The camera SDK is not needed; hardware interfaces are supplied by each harness.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def run(code, sources=()):
    compiler = next((shutil.which(name) for name in ("cc", "gcc", "clang", "cl")
                     if shutil.which(name)), None)
    if not compiler:
        raise SystemExit("A host C compiler is required (cc/gcc/clang or a Visual Studio developer shell).")
    with tempfile.TemporaryDirectory(prefix="dataq-test-") as directory:
        directory = Path(directory)
        harness = directory / "harness.c"
        harness.write_text(code, encoding="utf-8")
        executable = directory / "harness.exe"
        inputs = [str(harness), *(str(APP / name) for name in sources)]
        if Path(compiler).stem.lower() == "cl":
            command = [compiler, "/nologo", "/D_CRT_SECURE_NO_WARNINGS", "/I" + str(APP),
                       *inputs, "/Fe:" + str(executable)]
        else:
            command = [compiler, "-std=gnu11", "-I", str(APP), *inputs, "-lm", "-o", str(executable)]
        subprocess.run(command, cwd=directory, check=True)
        subprocess.run([str(executable)], cwd=directory, check=True)
