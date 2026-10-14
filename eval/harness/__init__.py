"""容器无关评测 harness（预注册 §5 / §8）。

模块分工：
- `env.py`        工作区与 venv 的生命周期（§8.1 / §8.2）
- `judge.py`      判定逻辑：六分类 verdict、超时优先、测试侧剥离（§5）
- `run_instance.py` 单实例全流程（造工作区 → test_patch → 候选补丁 → 判定 → 证据落盘）
- `scheduler.py`  进程池并发 + 状态落盘 + 断点续跑（§8.3 / §8.5）
"""