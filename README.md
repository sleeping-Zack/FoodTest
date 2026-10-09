# FoodTest · 食分

独立的食品分类与国标依据工作台，当前源码版本为 `food-classifier-1.2.0`。

输入食品名称，查看分类路径、判断依据、原文页码、关联检验项目和国家标准文件。前端使用原生 HTML/CSS/JavaScript，后端使用 Python 标准库 HTTP 服务和 SQLite，默认仅供本机使用。

在线入口：[食分工作台](https://yaoanxin.xyz/foodtest/)。在线部署使用配套资料快照，查询历史按浏览器隔离；清除 Cookie 或更换浏览器后无法找回该浏览器的历史，请及时导出重要结果。这是匿名访客隔离，不是机构账号及角色权限系统。

## 本仓库范围

本仓库仅包含食品分类系统源码、必要的分类映射配置、启动脚本和测试代码。**标准全文、资料索引和分类数据集另行提供，本仓库不包含运行所需的资料库。直接克隆后，需要先准备下述外部资料才能启动完整查询功能。**

不包含报告审查系统、检测报告、内部需求文档、查询数据库、日志、缓存、测试截图或验收产物。

## 功能

- 按食品名称及加工状态检索分类候选；不同标准保留各自的分类体系和来源。
- 从产品种类定义关联食品层级，保留定义原句和原文位置。
- 具体食品沿原文层级关联最近的检验项目表。例如在对应资料快照下，“年糕”自动关联“米粉制品”的 8 条项目记录，无需改搜上级名称。
- 多分支食品分别预览候选项目表；没有可靠关联时显示待关联，不将缺口当作 0 项。
- 展示项目引用、分节引用、标准版本和原文缺口；支持原文预览、下载和关联文件打包。
- 在本机保存查询历史和人工复核记录。
- 支持 HTTPS 共享部署，按访客隔离查询、导出和复核记录；可挂载在已有网站的 `/foodtest/` 路径。

分类基于名称、规则和有来源的层级关联，目前没有接入语义模型。自动关联不等于已确认样品适用性或本次任务必检项目；原料、工艺、比例、条件脚注和例外仍需核对。

## 目录

```text
food-classifier/
  engine.py                       分类与标准关联
  product_definitions.py          产品种类定义和层级提取
  server.py                       HTTP API、SQLite、原文预览和下载
  web/                            中文前端
  resources/inspection-bridges.json  定义名称与检验表标题的映射配置
  Start.ps1                       Windows 启动器
  启动食品分类系统.cmd             双击启动入口
  requirements.txt               可选 PDF 页图像预览依赖
  build_source_cache.py           从本地细则原件生成页级缓存
  export_definitions.py           导出产品种类定义表
  export_matching_tables.py       导出实际匹配表
  audit_inspection_links.py       回放检验项目表关联
  tests/                         自动测试和浏览器测试源码
  deploy/                        Docker、反向代理、资料打包与校验工具
```

## 准备外部资料

保持 `food-classifier/` 位于仓库根目录下，并将资料库放在同级的 `references/` 下：

```text
FoodTest/
  food-classifier/
    resources/
      inspection-bridges.json    已随代码提供
      guide-pages.json           本地生成，不上传
  references/
    食品标准资料库/
      索引/
        食品项目标准方法关联.json
        标准总目录.json
        标准逐层引用关系.json
      分类体系/
        *.json
      ... 原始标准及抽检细则文件，路径与索引记录保持一致
```

索引必须采用当前引擎读取的数据结构，仅下载 PDF 并不能替代结构化资料索引。索引中的原件路径相对于仓库根目录，文件须位于 `references/` 内。不同标准的分类文件应分别保存。当前集成测试针对 2026-10-09 的配套资料快照（9 套分类体系）编写。

准备索引及对应细则原件后，生成页级缓存：

```powershell
python -m pip install pypdf
python -X utf8 food-classifier/build_source_cache.py
```

也可使用与配套资料相同的 `guide-pages.json`。原件与索引的 SHA-256 必须一致；不要将缓存与其他版本索引混用。资料不完整时不会自动从互联网补齐。

## 启动

需要 Python 3.11 或以上。以下命令均在仓库根目录执行，先完成资料准备：

```powershell
python -m pip install -r food-classifier/requirements.txt
python -X utf8 food-classifier/server.py --port 8011
```

打开 <http://127.0.0.1:8011>。Windows 也可双击 `food-classifier/启动食品分类系统.cmd`；启动器优先使用本机已有的 Codex Python 运行时，找不到时使用系统 `python`。

基础查询、SQLite 和文件打包仅使用 Python 标准库。`requirements.txt` 中的 `pypdfium2` 和 `Pillow` 用于 PDF 页图像预览；生成缓存另需上面的 `pypdf`。

数据库、预览缓存及启动日志默认写入 `food-classifier/data/`，已加入忽略规则。替换资料库后应重新生成相应缓存并重启服务。默认仅监听本机；公网部署需显式设置 HTTPS 公共地址，具体步骤见 [部署说明](food-classifier/deploy/README.md)。

## 主要接口

- `POST /api/classify`：食品分类及关联资料。
- `GET /api/catalog`、`GET /api/definitions`、`GET /api/meta`：类别、产品种类定义及资料版本。
- `GET /api/inspection-options/{entry_id}`：预览具体检验项目表。
- `GET /api/history`、`GET /api/queries/{id}`：本机查询记录。
- `POST /api/queries/{id}/reviews`：保存人工处理记录。
- `GET /api/queries/{id}/export`、`GET /api/queries/{id}/bundle`：结果和资料包导出。
- `GET /api/standards/{record_id}`、`GET /api/files/{file_id}`：标准详情和原件。
- `GET /api/files/{file_id}/preview?page=N`：指定 PDF 页图像预览。

## 验证

完整测试需要上述外部资料。当前已通过 42 项自动测试，覆盖分类、来源定位、检验项目继承、访客隔离、子路径和部署文件时间兼容性。上线前还进行了公网浏览器检查。这些是功能回归结果，不代表对所有食品的分类准确率验证。

```powershell
python -X utf8 -m unittest discover -s food-classifier/tests -p "test*.py" -v
```

浏览器测试需要 Node.js、Playwright 和 Edge，默认使用本机 Codex 运行时路径。其他环境可在 PowerShell 中设置实际安装位置后执行：

```powershell
$env:FOOD_CLASSIFIER_PYTHON = (Get-Command python).Source
$env:CODEX_NODE_MODULES = 'C:\your-path\node_modules'
$env:EDGE_PATH = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
node food-classifier/tests/browser_smoke.cjs
```

`CODEX_NODE_MODULES` 指向已安装 `playwright` 的 `node_modules` 目录。测试使用独立数据库，产物写入已忽略的 `food-classifier/tests/artifacts/`。
