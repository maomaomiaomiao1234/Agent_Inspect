"""A repeatable 10,000-tool import benchmark; synthetic data only."""

import copy
import tempfile
import time

from agent_trace_review.demo import demo_bundles
from agent_trace_review.service import ingest
from agent_trace_review.storage import Store
from agent_trace_review.util import canonical

native = demo_bundles()[0]["export"]
message = native["messages"][1]
sample = message["parts"][0]
parts = []
for index in range(10000):
    part = copy.deepcopy(sample)
    part["id"], part["callID"] = f"prt_large_{index}", f"call_large_{index}"
    part["state"]["input"]["filePath"] = f"/demo/auth-service/file_{index}.py"
    part["state"]["output"] = "x" * 1000
    parts.append(part)
message["parts"] = parts
native["messages"] = [native["messages"][0], message]
content = canonical(native).encode()
with tempfile.TemporaryDirectory(prefix="agent-review-benchmark-") as temporary:
    store = Store(temporary)
    start = time.perf_counter()
    run, _, _ = ingest(store, content)
    seconds = time.perf_counter() - start
    start = time.perf_counter()
    restored = store.get_run(run.id)
    read_seconds = time.perf_counter() - start
    assert len(restored.events) == 10001
    print(
        canonical(
            {
                "tools": 10000,
                "bytes": len(content),
                "import_seconds": round(seconds, 3),
                "read_seconds": round(read_seconds, 3),
            }
        )
    )
