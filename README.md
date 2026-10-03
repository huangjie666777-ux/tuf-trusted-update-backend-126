# TUF 可信软件更新后端

基于 FastAPI 0.115 与 tuf 5.1.0 的纯后端可信更新服务：通过 TUF 1.0
客户端工作流验证上游仓库元数据，防止篡改与降级（回滚），只在完整
验证后发布目标目录，并按发布目录钉住的摘要交付目标文件。

## 信任锚与范围

- **信任锚**：每个更新源在创建时通过带外通道（POST 请求体，而非从
  上游下载）接收可信 `root.json`。此后所有信任均由该 root 按 TUF
  语义链式推导（root 轮换需旧、新 root 双重阈值签名）。
- **支持范围（超出即拒绝）**：TUF spec 1.0.x；仅 Ed25519 密钥；恰好
  四个顶层角色（root/timestamp/snapshot/targets）；`consistent_snapshot`
  必须为 false；不支持委托（delegations）；不执行任何安装动作，只
  交付已验证字节。
- **防篡改**：root 按版本顺序轮换并逐步持久化；timestamp/snapshot/
  targets 校验角色签名阈值（同一密钥不重复计数）、有效期、版本号及
  引用的长度与 SHA256；下载先落临时文件，核对长度与 SHA256 后才返回。
- **防降级**：timestamp、snapshot、targets 版本单调不减；root 版本
  严格 +1 递增；过期元数据与过期发布目录一律拒绝。

## 运行

```bash
.venv/bin/python -m uvicorn app.main:app --port 8000
```

数据目录默认为 `./data`（可用 `TUF_UPDATE_DATA_DIR` 覆盖），信任状态
与已发布目录跨重启保留。

## API

- `POST /sources` 创建更新源：`{name, metadata_url, targets_url, root_json}`。上游地址固定，各源独立。
- `POST /sources/{name}/refresh` 刷新：root 轮换 → timestamp → snapshot → targets → 发布。同源刷新串行，异源互不阻塞。失败返回 `{status: "failed", stage, reason}` 并保留旧发布目录。
- `GET /sources/{name}` 状态：发布清单（路径、长度、SHA256、角色版本）与最近失败信息。
- `GET /sources/{name}/targets/{path}` 按当前发布目录下载目标；未知目标 404、路径穿越 400、目录过期 409、截断/超长/摘要不符 502。

## 本地演示

```bash
# 1. 生成本地签名仓库（含演示密钥与目标文件）
.venv/bin/python demo/make_repo.py
# 2. 作为上游静态服务
.venv/bin/python -m http.server 8001 --directory demo/repo &
# 3. 启动本服务
.venv/bin/python -m uvicorn app.main:app --port 8000 &
# 4. 注册源（root.json 带外交付）、刷新、下载
ROOT_JSON=$(.venv/bin/python -c 'import json; print(json.dumps(open("demo/repo/metadata/1.root.json").read()))')
curl -s -X POST localhost:8000/sources -H 'content-type: application/json' -d "{
  \"name\": \"demo\",
  \"metadata_url\": \"http://127.0.0.1:8001/metadata\",
  \"targets_url\": \"http://127.0.0.1:8001/targets\",
  \"root_json\": $ROOT_JSON
}"
curl -s -X POST localhost:8000/sources/demo/refresh
curl -s localhost:8000/sources/demo/targets/app/hello.txt
```

## 自测

```bash
.venv/bin/python -m pytest tests -q
```

覆盖：刷新与下载、篡改/截断/未知目标、路径穿越、root 轮换、回滚
拒绝且保留旧目录、非法配置拒绝、重启状态保留、过期目录拒绝下载。

## 模块划分

- `app/validation.py` 配置策略校验（spec/密钥/角色/委托）
- `app/store.py` 信任存储与原子持久化
- `app/refresh.py` 角色刷新流水线（root 轮换、三类元数据验证）
- `app/publish.py` 发布目录清单构建
- `app/downloads.py` 钉住清单的临时文件下载与核验
- `app/sources.py` 源注册表与每源串行锁
- `app/fetch.py` / `app/config.py` 限时限量 HTTP 与全局上限
- `app/main.py` FastAPI 端点
