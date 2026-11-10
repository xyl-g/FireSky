"""Test-only legacy engine: location methods must never arrive here."""
import json
import os
import sys

for line in sys.stdin:
    request = json.loads(line)
    with open(os.environ["FIRESKY_PROBE_PYTHON_RECORD"], "a") as stream:
        stream.write(json.dumps(request["method"])+"\n")
    value = {"protocol":1} if request["method"]=="hello" else {"legacy":True}
    print(json.dumps(dict(id=request["id"],type="result",result=value)),flush=True)
