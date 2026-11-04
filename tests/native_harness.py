"""Run isolated production C functions with a host C compiler.

Requires Python 3 and cc/gcc/clang, or cl in a Visual Studio developer shell.
The camera SDK is not needed; hardware interfaces are supplied by each harness.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
# Braces, plus the literals and comments whose braces must not be counted
TOKEN = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|//[^\n]*|/\*.*?\*/|[{}]', re.S)


def function(source, signature):
    # A definition reaches its "{" before any ";", which skips prototypes
    definition = re.search(re.escape(signature) + r"[^;{]*\{", source)
    depth, end = 1, definition.end()
    while depth:
        token = TOKEN.search(source, end)
        depth += (token.group() == "{") - (token.group() == "}")
        end = token.end()
    return source[definition.start():end]


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
