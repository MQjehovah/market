"""沙箱子进程执行器：从 stdin 读取 JSON 参数，加载 implementation/tool.py 并调用 run()。

契约：
    tool.py 必须提供 run(params: dict) -> dict 或 main(params: dict) -> dict。
    参数通过 stdin（UTF-8 JSON）传入，结果通过 stdout（UTF-8 JSON）返回。
"""

import importlib.util
import json
import sys


def _fail(message: str) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
    sys.exit(2)


def main() -> None:
    if len(sys.argv) < 2:
        _fail("缺少模块目录参数")
    module_dir = sys.argv[1]
    sys.path.insert(0, module_dir)

    spec = importlib.util.spec_from_file_location(
        "marketplace_tool", module_dir + "/implementation/tool.py"
    )
    if spec is None or spec.loader is None:
        _fail("无法加载 implementation/tool.py")
    try:
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
    except Exception as exc:  # noqa: BLE001
        _fail(f"工具模块加载失败：{exc}")

    try:
        params = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        _fail(f"参数 JSON 解析失败：{exc}")

    fn = getattr(tool, "run", None) or getattr(tool, "main", None)
    if not callable(fn):
        _fail("工具实现必须提供 run(params: dict) -> dict 或 main(params: dict) -> dict")

    try:
        result = fn(params)
    except Exception as exc:  # noqa: BLE001
        import traceback

        _fail(f"工具执行异常：{exc}\n{traceback.format_exc(limit=8)}")

    if not isinstance(result, dict):
        _fail("工具返回值必须是 dict")
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
