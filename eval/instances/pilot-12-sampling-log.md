# pilot-12 抽样日志（由 eval/build_subset.py 生成，人不得改）

- 生成时间：2026-10-12T22:50:54+0800
- 对应规则节：§7.2 候选池构建 / §7.3 抽样规则 / §3.1 C1–C4 / §3.2 E0–E10
- 时间窗（§3.3 C3 钉死）：[2025-04-01, 2026-09-30]
- 排序键：`(fix_commit_date 升序, instance_id 字典序)`；quota=3，单仓库上限 5，目标 12 条

## 候选池与各步计数

| 仓库 | 窗内提交 | C1+C2 通过 | 进入实测 | 三向全过 | 入选 pilot | reserve |
|---|---|---|---|---|---|---|
| more-itertools/more-itertools | 356 | 71 | 1 | 0 | 3 | 3 |
| pytest-dev/pluggy | 221 | 29 | 1 | 1 | 3 | 3 |
| pallets/click | 445 | 107 | 1 | 0 | 3 | 3 |
| astanin/python-tabulate | 116 | 24 | 1 | 1 | 3 | 2 |

## 排除流水（规则代号见 §3.2）

- `more-itertools__more-itertools` `45b6bf8` **E4** — 源码文件数 1 / 改动行数 463 超上限
- `more-itertools__more-itertools` `e39ae39` **E1** — test_patch 删除/禁用了测试：['def test_range(self):', 'def test_list_range(self):', 'def test_tuple_range(self):']
- `more-itertools__more-itertools` `fb33cf2` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 9 < 20；泄漏项=['symbol:argmax', 'symbol:argmin']
- `more-itertools__more-itertools` `a16e9c1` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `more-itertools__more-itertools` `cca3294` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 0 < 20；泄漏项=['symbol:last']
- `more-itertools__more-itertools` `6887a7b` **E9** — E9: 全文非空白字符 17 < 20
- `more-itertools__more-itertools` `5c8336c` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 0 < 20；泄漏项=['symbol:running_median']
- `more-itertools__more-itertools` `7f21fb1` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 0 < 20；泄漏项=['symbol:interleave_randomly']
- `more-itertools__more-itertools` `5bd5c26` **E9** — E9: 全文非空白字符 12 < 20
- `more-itertools__more-itertools` `11c8280` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `more-itertools__more-itertools` `361b925` **E1** — test_patch 删除/禁用了测试：['def test_deprecation(self):', 'def test_equal(self):', 'def test_unequal_lists(self):']
- `more-itertools__more-itertools` `21d3d88` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 10 < 20；泄漏项=['symbol:reshape']
- `more-itertools__more-itertools` `4e105c4` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `more-itertools__more-itertools` `74c001a` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `pytest-dev__pluggy` `b2d1c4c` **E1** — test_patch 删除/禁用了测试：['def test_register_skips_properties(he_pm: PluginManager) -> None:', 'def test_register_skips_pydantic_fields(he_pm: PluginManager) -> None:']
- `pytest-dev__pluggy` `5d1ffb9` **E6** — G 没有任何逐 node 结果（json report 为空）
- `pytest-dev__pluggy` `d0ab361` **R4** — editable 安装自检失败（pluggy 未从本实例工作区导入）：pth=E:\aicoding\eval-venvs\pytest-dev-pluggy__d0ab361-py312\Lib\site-packages\_eval_editable_b3d840a1ff9197ec.pth 解析到= stderr=val-runs\build-p0\pytest-dev-pluggy__d0ab361\repo\src\pluggy\__init__.py", line 28, in <module>
    from ._version import version as __version__
ModuleNotFoundError: No module named 'pluggy._version'
；仓库 .gitignore 未覆盖可编辑安装产物，§9.1「跑后 porcelain 为空」在该仓库不可满足（§2.3 兜底：判不了即排除）
- `pytest-dev__pluggy` `eb70fb9` **R4** — editable 安装自检失败（pluggy 未从本实例工作区导入）：pth=E:\aicoding\eval-venvs\pytest-dev-pluggy__eb70fb9-py312\Lib\site-packages\_eval_editable_7cbd53cf510de58d.pth 解析到= stderr=val-runs\build-p0\pytest-dev-pluggy__eb70fb9\repo\src\pluggy\__init__.py", line 28, in <module>
    from ._version import version as __version__
ModuleNotFoundError: No module named 'pluggy._version'
；仓库 .gitignore 未覆盖可编辑安装产物，§9.1「跑后 porcelain 为空」在该仓库不可满足（§2.3 兜底：判不了即排除）
- `pytest-dev__pluggy` `6a417de` **R4** — editable 安装自检失败（pluggy 未从本实例工作区导入）：pth=E:\aicoding\eval-venvs\pytest-dev-pluggy__6a417de-py312\Lib\site-packages\_eval_editable_fc3f3afc4c6000be.pth 解析到= stderr=val-runs\build-p0\pytest-dev-pluggy__6a417de\repo\src\pluggy\__init__.py", line 28, in <module>
    from ._version import version as __version__
ModuleNotFoundError: No module named 'pluggy._version'
；仓库 .gitignore 未覆盖可编辑安装产物，§9.1「跑后 porcelain 为空」在该仓库不可满足（§2.3 兜底：判不了即排除）
- `pytest-dev__pluggy` `4dd2443` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `pytest-dev__pluggy` `53eeddf` **E1** — test_patch 删除/禁用了测试：['def test_load_setuptools_instantiation(monkeypatch, pm: PluginManager) -> None:']
- `pytest-dev__pluggy` `74876c4` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `pytest-dev__pluggy` `0b7790e` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `pytest-dev__pluggy` `09676ee` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `pallets__click` `f14b750` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `pallets__click` `ba770cb` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `pallets__click` `f3a4363` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 0 < 20；泄漏项=['symbol:CliRunner']
- `pallets__click` `80efdf6` **E6** — T 没有任何逐 node 结果（json report 为空）
- `pallets__click` `93c6966` **E10** — 三向第1向未过（gold 期望 resolved，实为 infra_error）
- `pallets__click` `9caedb9` **E4** — 源码文件数 2 / 改动行数 510 超上限
- `pallets__click` `a1235aa` **E10** — 三向第1向未过（gold 期望 resolved，实为 infra_error）
- `pallets__click` `8bc9127` **E1** — test_patch 删除/禁用了测试：['def test_choice_default_rendering(runner, choices, default, default_string):', 'def test_zsh_full_complete_with_colons(']
- `pallets__click` `e252437` **E10** — 三向第1向未过（gold 期望 resolved，实为 infra_error）
- `pallets__click` `812b800` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `pallets__click` `b64ea07` **E4** — 源码文件数 4 / 改动行数 1978 超上限
- `astanin__python-tabulate` `9dedd45` **§6.4** — 三向第3向 wrong_patch 无法 apply（期望能 apply）：error: patch failed: tabulate/__init__.py:737
error: tabulate/__init__.py: patch does not apply

- `astanin__python-tabulate` `4d535af` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `886e2ed` **E5** — FAIL_TO_PASS 为空集（T 红灯全部属于「基线本来就有病」（1 条）或在 G 不为绿；T 红灯示例=['test/test_textwrapper.py::test_wrap_optional_bool_strs']）
- `astanin__python-tabulate` `f0a5df6` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `87a9a4e` **§6.4** — 三向第3向 wrong_patch 无法 apply（期望能 apply）：error: patch failed: tabulate/__init__.py:2288
error: tabulate/__init__.py: patch does not apply

- `astanin__python-tabulate` `a3223ed` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `e13a4d0` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `b96c59a` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `5373ebf` **§6.4** — 三向第3向 wrong_patch 无法 apply（期望能 apply）：error: patch failed: tabulate/__init__.py:13
error: tabulate/__init__.py: patch does not apply

- `astanin__python-tabulate` `4a95974` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `5768953` **E1** — test_patch 删除/禁用了测试：['def test_youtrack():']
- `astanin__python-tabulate` `3381cfc` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `032dc0d` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `4c9a049` **E3/test_apply** — 收集前 test_patch 无法应用 error: patch failed: test/test_grapheme_clusters.py:1
error: test/test_grapheme_clusters.py: patch does not apply

- `astanin__python-tabulate` `ebd50a7` **E5** — FAIL_TO_PASS 为空集（T 红灯全部属于「基线本来就有病」（2 条）或在 G 不为绿；T 红灯示例=['test/test_output.py::test_moinmoin', 'test/test_output.py::test_moinmoin_headerless']）
- `more-itertools__more-itertools` `45b6bf8` **E4** — 源码文件数 1 / 改动行数 463 超上限
- `more-itertools__more-itertools` `e39ae39` **E1** — test_patch 删除/禁用了测试：['def test_range(self):', 'def test_list_range(self):', 'def test_tuple_range(self):']
- `more-itertools__more-itertools` `fb33cf2` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 9 < 20；泄漏项=['symbol:argmax', 'symbol:argmin']
- `more-itertools__more-itertools` `a16e9c1` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `more-itertools__more-itertools` `cca3294` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 0 < 20；泄漏项=['symbol:last']
- `more-itertools__more-itertools` `6887a7b` **E9** — E9: 全文非空白字符 17 < 20
- `more-itertools__more-itertools` `5c8336c` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 0 < 20；泄漏项=['symbol:running_median']
- `more-itertools__more-itertools` `7f21fb1` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 0 < 20；泄漏项=['symbol:interleave_randomly']
- `more-itertools__more-itertools` `5bd5c26` **E9** — E9: 全文非空白字符 12 < 20
- `more-itertools__more-itertools` `11c8280` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `more-itertools__more-itertools` `361b925` **E1** — test_patch 删除/禁用了测试：['def test_deprecation(self):', 'def test_equal(self):', 'def test_unequal_lists(self):']
- `more-itertools__more-itertools` `21d3d88` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 10 < 20；泄漏项=['symbol:reshape']
- `more-itertools__more-itertools` `4e105c4` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `more-itertools__more-itertools` `74c001a` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `more-itertools__more-itertools` `196acd2` **E1** — test_patch 删除/禁用了测试：['def test_base_case(self):', 'def test_short_case(self):', 'def test_coverage(self):']
- `more-itertools__more-itertools` `9463231` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `more-itertools__more-itertools` `120ee0a` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 9 < 20；泄漏项=['symbol:iequals']
- `more-itertools__more-itertools` `cf186b5` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 0 < 20；泄漏项=['symbol:product_index']
- `more-itertools__more-itertools` `619ab9c` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 0 < 20；泄漏项=['symbol:powerset_of_sets']
- `more-itertools__more-itertools` `adeda34` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 13 < 20；泄漏项=['symbol:exactly_n']
- `more-itertools__more-itertools` `2af32e1` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 0 < 20；泄漏项=['symbol:extract']
- `more-itertools__more-itertools` `4f39922` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 10 < 20；泄漏项=['symbol:serialize']
- `more-itertools__more-itertools` `428a0a2` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 10 < 20；泄漏项=['symbol:synchronized']
- `more-itertools__more-itertools` `6743b2f` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `more-itertools__more-itertools` `37ae0d6` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 0 < 20；泄漏项=['symbol:nth_product']
- `more-itertools__more-itertools` `b0106ba` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 0 < 20；泄漏项=['symbol:product_index']
- `more-itertools__more-itertools` `867798d` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 0 < 20；泄漏项=['symbol:gray_product']
- `more-itertools__more-itertools` `5e6ae76` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 0 < 20；泄漏项=['symbol:partial_product']
- `more-itertools__more-itertools` `bf78689` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `more-itertools__more-itertools` `b0aa91e` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 0 < 20；泄漏项=['symbol:random_product']
- `pytest-dev__pluggy` `b2d1c4c` **E1** — test_patch 删除/禁用了测试：['def test_register_skips_properties(he_pm: PluginManager) -> None:', 'def test_register_skips_pydantic_fields(he_pm: PluginManager) -> None:']
- `pytest-dev__pluggy` `5d1ffb9` **E6** — G 没有任何逐 node 结果（json report 为空）
- `pytest-dev__pluggy` `d0ab361` **R4** — editable 安装自检失败（pluggy 未从本实例工作区导入）：pth=E:\aicoding\eval-venvs\pytest-dev-pluggy__d0ab361-py312\Lib\site-packages\_eval_editable_b3d840a1ff9197ec.pth 解析到= stderr=val-runs\build-p0\pytest-dev-pluggy__d0ab361\repo\src\pluggy\__init__.py", line 28, in <module>
    from ._version import version as __version__
ModuleNotFoundError: No module named 'pluggy._version'
；仓库 .gitignore 未覆盖可编辑安装产物，§9.1「跑后 porcelain 为空」在该仓库不可满足（§2.3 兜底：判不了即排除）
- `pytest-dev__pluggy` `eb70fb9` **R4** — editable 安装自检失败（pluggy 未从本实例工作区导入）：pth=E:\aicoding\eval-venvs\pytest-dev-pluggy__eb70fb9-py312\Lib\site-packages\_eval_editable_7cbd53cf510de58d.pth 解析到= stderr=val-runs\build-p0\pytest-dev-pluggy__eb70fb9\repo\src\pluggy\__init__.py", line 28, in <module>
    from ._version import version as __version__
ModuleNotFoundError: No module named 'pluggy._version'
；仓库 .gitignore 未覆盖可编辑安装产物，§9.1「跑后 porcelain 为空」在该仓库不可满足（§2.3 兜底：判不了即排除）
- `pytest-dev__pluggy` `6a417de` **R4** — editable 安装自检失败（pluggy 未从本实例工作区导入）：pth=E:\aicoding\eval-venvs\pytest-dev-pluggy__6a417de-py312\Lib\site-packages\_eval_editable_fc3f3afc4c6000be.pth 解析到= stderr=val-runs\build-p0\pytest-dev-pluggy__6a417de\repo\src\pluggy\__init__.py", line 28, in <module>
    from ._version import version as __version__
ModuleNotFoundError: No module named 'pluggy._version'
；仓库 .gitignore 未覆盖可编辑安装产物，§9.1「跑后 porcelain 为空」在该仓库不可满足（§2.3 兜底：判不了即排除）
- `pytest-dev__pluggy` `4dd2443` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `pytest-dev__pluggy` `53eeddf` **E1** — test_patch 删除/禁用了测试：['def test_load_setuptools_instantiation(monkeypatch, pm: PluginManager) -> None:']
- `pytest-dev__pluggy` `74876c4` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `pytest-dev__pluggy` `0b7790e` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `pytest-dev__pluggy` `09676ee` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `pallets__click` `f14b750` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `pallets__click` `ba770cb` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `pallets__click` `f3a4363` **E9** — E9/§3.4: 删除 1 句泄漏文本后非空白字符 0 < 20；泄漏项=['symbol:CliRunner']
- `pallets__click` `80efdf6` **E6** — T 没有任何逐 node 结果（json report 为空）
- `pallets__click` `93c6966` **E10** — 三向第1向未过（gold 期望 resolved，实为 infra_error）
- `pallets__click` `9caedb9` **E4** — 源码文件数 2 / 改动行数 510 超上限
- `pallets__click` `a1235aa` **E10** — 三向第1向未过（gold 期望 resolved，实为 infra_error）
- `pallets__click` `8bc9127` **E1** — test_patch 删除/禁用了测试：['def test_choice_default_rendering(runner, choices, default, default_string):', 'def test_zsh_full_complete_with_colons(']
- `pallets__click` `e252437` **E10** — 三向第1向未过（gold 期望 resolved，实为 infra_error）
- `pallets__click` `812b800` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `pallets__click` `b64ea07` **E4** — 源码文件数 4 / 改动行数 1978 超上限
- `pallets__click` `701b313` **E10** — 三向第1向未过（gold 期望 resolved，实为 infra_error）
- `pallets__click` `2d1ceff` **E1** — test_patch 删除/禁用了测试：['def test_flag_value(runner, default, args, expect):', 'def test_flag_value_callback(runner, default, args, expected):']
- `pallets__click` `7f4fa21` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `4d535af` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `886e2ed` **E5** — FAIL_TO_PASS 为空集（T 红灯全部属于「基线本来就有病」（1 条）或在 G 不为绿；T 红灯示例=['test/test_textwrapper.py::test_wrap_optional_bool_strs']）
- `astanin__python-tabulate` `f0a5df6` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `a3223ed` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `e13a4d0` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `b96c59a` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `4a95974` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `5768953` **E1** — test_patch 删除/禁用了测试：['def test_youtrack():']
- `astanin__python-tabulate` `3381cfc` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `032dc0d` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `4c9a049` **E3/test_apply** — 收集前 test_patch 无法应用 error: patch failed: test/test_grapheme_clusters.py:1
error: test/test_grapheme_clusters.py: patch does not apply

- `astanin__python-tabulate` `ebd50a7` **E5** — FAIL_TO_PASS 为空集（T 红灯全部属于「基线本来就有病」（2 条）或在 G 不为绿；T 红灯示例=['test/test_output.py::test_moinmoin', 'test/test_output.py::test_moinmoin_headerless']）
- `astanin__python-tabulate` `4c39df9` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `f1a64ec` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `e6a24aa` **E5** — FAIL_TO_PASS 为空集（T 红灯全部属于「基线本来就有病」（1 条）或在 G 不为绿；T 红灯示例=['test/test_regression.py::test_github_escape_pipe_character']）
- `astanin__python-tabulate` `b47acc1` **E4** — 源码文件数 3 / 改动行数 434 超上限
- `astanin__python-tabulate` `17bf1cf` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `cf15727` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `astanin__python-tabulate` `3681fa5` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）
- `pytest-dev__pluggy` `b2d1c4c` **E1** — test_patch 删除/禁用了测试：['def test_register_skips_properties(he_pm: PluginManager) -> None:', 'def test_register_skips_pydantic_fields(he_pm: PluginManager) -> None:']
- `pytest-dev__pluggy` `5d1ffb9` **E6** — G 没有任何逐 node 结果（json report 为空）
- `more-itertools__more-itertools` `45b6bf8` **E4** — 源码文件数 1 / 改动行数 463 超上限
- `pallets__click` `f14b750` **E5** — FAIL_TO_PASS 为空集（T 观测点无红灯（新测试也没变红）；T 红灯示例=[]）

## 入选序列（机械理由）

- `pytest-dev-pluggy__cbf58d5` — §7.3：仓库 pytest-dev/pluggy 排序后候选序列中第 1 个通过 §6 三向自校验的候选
- `astanin-python-tabulate__9dedd45` — §7.3：仓库 astanin/python-tabulate 排序后候选序列中第 1 个通过 §6 三向自校验的候选
- `pallets-click__ac6a2ac` — §7.3：仓库 pallets/click 排序后候选序列中第 1 个通过 §6 三向自校验的候选
- `pallets-click__4fd2fea` — §7.3：仓库 pallets/click 排序后候选序列中第 2 个通过 §6 三向自校验的候选
- `pallets-click__1b0e19f` — §7.3：仓库 pallets/click 排序后候选序列中第 3 个通过 §6 三向自校验的候选
- `more-itertools-more-itertools__f8ccab2` — §7.3：仓库 more-itertools/more-itertools 排序后候选序列中第 1 个通过 §6 三向自校验的候选
- `more-itertools-more-itertools__ccf96ac` — §7.3：仓库 more-itertools/more-itertools 排序后候选序列中第 2 个通过 §6 三向自校验的候选
- `more-itertools-more-itertools__1b7f033` — §7.3：仓库 more-itertools/more-itertools 排序后候选序列中第 3 个通过 §6 三向自校验的候选
- `pytest-dev-pluggy__20d8143` — §7.3：仓库 pytest-dev/pluggy 排序后候选序列中第 2 个通过 §6 三向自校验的候选
- `astanin-python-tabulate__87a9a4e` — §7.3：仓库 astanin/python-tabulate 排序后候选序列中第 2 个通过 §6 三向自校验的候选
- `astanin-python-tabulate__5373ebf` — §7.3：仓库 astanin/python-tabulate 排序后候选序列中第 3 个通过 §6 三向自校验的候选
- `pytest-dev-pluggy__dd20a85` — §7.3：仓库 pytest-dev/pluggy 排序后候选序列中第 3 个通过 §6 三向自校验的候选
- `pallets-click__f2ae679` — §7.3：紧随其后的第 1 个合格候选，标 reserve，仅在正式实例判 excluded_infra 时按顺序顶替
- `pallets-click__06847da` — §7.3：紧随其后的第 2 个合格候选，标 reserve，仅在正式实例判 excluded_infra 时按顺序顶替
- `pallets-click__029bbed` — §7.3：紧随其后的第 3 个合格候选，标 reserve，仅在正式实例判 excluded_infra 时按顺序顶替
- `more-itertools-more-itertools__073d234` — §7.3：紧随其后的第 1 个合格候选，标 reserve，仅在正式实例判 excluded_infra 时按顺序顶替
- `more-itertools-more-itertools__d9adeb4` — §7.3：紧随其后的第 2 个合格候选，标 reserve，仅在正式实例判 excluded_infra 时按顺序顶替
- `more-itertools-more-itertools__def2d82` — §7.3：紧随其后的第 3 个合格候选，标 reserve，仅在正式实例判 excluded_infra 时按顺序顶替
- `astanin-python-tabulate__3aa568c` — §7.3：紧随其后的第 1 个合格候选，标 reserve，仅在正式实例判 excluded_infra 时按顺序顶替
- `astanin-python-tabulate__8014ec6` — §7.3：紧随其后的第 2 个合格候选，标 reserve，仅在正式实例判 excluded_infra 时按顺序顶替
- `pytest-dev-pluggy__0258484` — §7.3：紧随其后的第 1 个合格候选，标 reserve，仅在正式实例判 excluded_infra 时按顺序顶替
- `pytest-dev-pluggy__3d5417a` — §7.3：紧随其后的第 2 个合格候选，标 reserve，仅在正式实例判 excluded_infra 时按顺序顶替
- `pytest-dev-pluggy__c1c39fe` — §7.3：紧随其后的第 3 个合格候选，标 reserve，仅在正式实例判 excluded_infra 时按顺序顶替

## 判定不了的兜底（§2.3）

- R6「不需要网络即可跑测试」：本机无法真正断网 ⇒ 按 §2.3 **保守处理**：判定阶段不主动访问外网域名，并在结果节声明「网络未强制隔离」这一局限。
- 快照测试占比（§2.2）：四个仓库均无 golden-file 快照机制（tabulate 的期望值写在测试源码字符串里，属于普通断言而非外部 golden 文件），故不按 §2.2 第二条弃用。
- §4.2 的实现口径（必须在结果节复述）：F2P 的准入是「B 中该 node **不存在**（= 本提交新增的测试）**或**存在且 passed」，排除的只是「B 中已存在且本来就非绿」的 node。原文「B 中 n 存在且 passed」的字面读法会把「修复提交顺手加回归测试」这一类**最标准**的 FAIL_TO_PASS 全部误杀；本构造器按该条**写明的目的**（排除基线上本来就红的测试）实现，差异已记入本节与实例 known_limits。

