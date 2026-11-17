"""M-01(2026-11-17):GitHub Actions 定时预测循环的失败语义必须唯一。

旧实现自相矛盾: `Guard secrets` 步用 `::warning::` + `pred_failures` 让整个 run 变红,
而各执行步又用 `[ -z "$PREDICT_API_BASE" ] && exit 0` **静默成功** —— 同一次运行里
"一步说失败、其余步说成功", 红/绿无法据以判断是配置问题还是代码问题。

统一后的契约(本文件逐条锁定):
1. `Guard secrets` 是唯一的配置判定点: 缺失 → `exit 1`(job 稳定红), 并输出
   job 级 `secrets_ready=false`;
2. 所有执行步都以 `steps.guard.outputs.secrets_ready == 'true'` 为前提(缺失即 skip,
   skip ≠ success);
3. 没有任何步骤再 `[ -z "$PREDICT_API_BASE" ] && exit 0` 静默放行;
4. `Summary` 永远运行(`always()`), 且对"配置缺失"与"环节失败"给出同一个 exit 1。

为了让断言真的是"行为级", guard 的 shell 脚本会被**真的执行**两种组合
(secrets 缺失 / secrets 齐全), 断言退出码与 GITHUB_OUTPUT 内容。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/prediction.yml"

#: 不属于"执行步"的步骤: 判定点本身与汇总步
_NON_EXEC_STEPS = {"Resolve task kind", "Guard secrets", "Summary"}

BASH = shutil.which("bash") or shutil.which("sh")

_EXPR = re.compile(r"\$\{\{[^}]*\}\}")


def _expand_github_expressions(script: str) -> str:
    """模拟 Actions 在执行前做的 `${{ ... }}` 表达式替换。

    Runner 会先把表达式替换成字面值再交给 bash; 直接执行原脚本时 bash 会把
    `${{ ... }}` 当成 `${...}` 展开而报 "bad substitution"。
    """
    return _EXPR.sub("test-value", script)


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _workflow()["jobs"]["cycle"]["steps"]


def _step(name: str) -> dict:
    for step in _steps():
        if step.get("name") == name:
            return step
    raise AssertionError(f"workflow 里找不到步骤 {name}")


def test_workflow_yaml_is_valid():
    doc = _workflow()
    assert doc["jobs"]["cycle"]["runs-on"] == "ubuntu-latest"
    assert _steps(), "steps 不能为空"


def test_guard_is_the_single_failure_point():
    guard = _step("Guard secrets")
    assert guard["id"] == "guard"
    run = guard["run"]
    assert "secrets_ready=false" in run
    assert "secrets_ready=true" in run
    assert "exit 1" in run, "secrets 缺失必须让 job 稳定变红, 不能只 warning"
    assert "::warning::" not in run, "guard 不能再用 warning 表达失败"


def test_every_exec_step_is_gated_by_guard_output():
    gated = 0
    for step in _steps():
        if step.get("name") in _NON_EXEC_STEPS:
            continue
        cond = step.get("if", "")
        assert "steps.guard.outputs.secrets_ready == 'true'" in cond, (
            f"步骤 {step.get('name')} 未以 guard 输出为前置条件: {cond!r}"
        )
        gated += 1
    assert gated >= 7, f"执行步数量异常({gated}), 说明有步骤没被纳入统一语义"


def test_no_step_silently_succeeds_without_secrets():
    """旧写法 `[ -z "$PREDICT_API_BASE" ] && exit 0` 必须彻底消失。"""
    for step in _steps():
        run = step.get("run", "")
        assert '[ -z "$PREDICT_API_BASE" ] && exit 0' not in run, (
            f"步骤 {step.get('name')} 仍在 secrets 缺失时静默成功"
        )


def test_summary_always_runs_and_uses_same_verdict():
    summary = _step("Summary")
    assert summary["if"] == "always()"
    run = summary["run"]
    assert "secrets_ready" in run
    assert "exit 1" in run


@pytest.mark.skipif(not BASH, reason="需要 bash/sh 才能执行 guard 脚本")
def test_guard_script_behaviour_matrix(tmp_path):
    """真的跑一遍 guard 脚本, 覆盖 secrets 缺失/齐全两种组合。"""
    script = _expand_github_expressions(_step("Guard secrets")["run"])

    # 组合 1: secrets 缺失 → 退出码 1, 输出 secrets_ready=false
    # GITHUB_OUTPUT 用相对文件名 + cwd=tmp_path: Windows 的 "C:/..." 绝对路径在
    # Git Bash 里会被当成相对路径, 直接传绝对路径反而写不进去。
    out_missing = tmp_path / "out_missing"
    out_missing.write_text("", encoding="utf-8")
    # 与 Actions 一致: workflow 的 env: 块会把两个 secret 定义成**空字符串**
    # (而不是"未定义"), 否则脚本里的 `set -u` 会先因 unbound variable 退出,
    # 走不到 guard 自己的判定分支。
    env_missing = dict(os.environ)
    env_missing["PREDICT_API_BASE"] = ""
    env_missing["ADMIN_TOKEN"] = ""
    env_missing["GITHUB_OUTPUT"] = "out_missing"
    proc = subprocess.run(
        [BASH, "-c", script], env=env_missing, capture_output=True, text=True,
        cwd=str(tmp_path),
    )
    assert proc.returncode == 1, f"secrets 缺失必须失败, 实际 rc={proc.returncode}\n{proc.stderr}"
    assert "secrets_ready=false" in out_missing.read_text(encoding="utf-8")

    # 组合 2: secrets 齐全 → 退出码 0, 输出 secrets_ready=true
    out_ready = tmp_path / "out_ready"
    out_ready.write_text("", encoding="utf-8")
    env_ready = dict(os.environ)
    env_ready["PREDICT_API_BASE"] = "https://example.invalid"
    env_ready["ADMIN_TOKEN"] = "token"
    env_ready["GITHUB_OUTPUT"] = "out_ready"
    proc = subprocess.run(
        [BASH, "-c", script], env=env_ready, capture_output=True, text=True,
        cwd=str(tmp_path),
    )
    assert proc.returncode == 0, f"secrets 齐全不应失败\n{proc.stderr}"
    assert "secrets_ready=true" in out_ready.read_text(encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
