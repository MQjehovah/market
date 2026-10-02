"""工作流触发器 + code 沙箱单测。"""
from datetime import datetime

import pytest

from app.services.sandbox import run_code
from app.services.workflow_triggers import cron_due


@pytest.mark.asyncio
async def test_run_code_python_main():
    res = await run_code("python", "def main(a):\n    return {'doubled': a * 2}\n", {"a": 4})
    assert res["result"] == {"doubled": 8}


@pytest.mark.asyncio
async def test_run_code_result_var_and_failure():
    res = await run_code("python", "result = 1 + 2\n", {})
    assert res["result"] == 3
    with pytest.raises(Exception):
        await run_code("python", "raise ValueError('boom')\n", {})
    with pytest.raises(Exception):
        await run_code("node", "console.log(1)", {})


def test_cron_due():
    now = datetime(2026, 1, 1, 8, 30)
    assert cron_due("30 8 * * *", now) is True
    assert cron_due("*/15 8 * * *", now) is True
    assert cron_due("0 8 * * *", now) is False
    assert cron_due("30 0 * * *", now) is False
    assert cron_due("bad expr", now) is False
