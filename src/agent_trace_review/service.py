import json

from .analysis import analyze
from .generic import import_generic
from .opencode import MAX_IMPORT, import_data
from .storage import Store


def ingest(store: Store, content: bytes, profile=None, **kwargs):
    if profile is not None:
        from .profiles import load_profile

        profile = load_profile(profile)
    if len(content) > MAX_IMPORT:
        raise ValueError("文件超过 100 MB，请先按会话或任务拆分。")
    try:
        payload = json.loads(content)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError("无法读取 JSON 轨迹。") from exc
    if not isinstance(payload, dict):
        raise ValueError("导出文件必须是 JSON 对象。")
    if "trace_version" in payload or str(payload.get("bundle_version", "")).startswith("generic/"):
        run, created = import_generic(store, payload, **kwargs)
    else:
        run, created = import_data(store, content, **kwargs)
    try:
        evaluation = store.get_evaluation(run.id)
    except KeyError:
        evaluation = analyze(run)
        store.save_evaluation(evaluation)
    if profile is not None:
        from .profiles import evaluate_profile

        evaluation = evaluate_profile(store, run, profile)
    return run, evaluation, created
