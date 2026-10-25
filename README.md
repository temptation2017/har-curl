# har-curl

把浏览器导出的 **HAR** 转成可复制的 `curl` 命令。纯标准库 Python CLI，默认脱敏 Authorization / Cookie / Token 等敏感头。

> HAR → curl, stdlib Python CLI; redacts auth headers by default.

## 做什么

从 Chrome / Firefox / Edge 的「另存为 HAR」文件里，抽出 HTTP(S) 请求，生成一条条 `curl`。方便人工复现，也方便 AI agent 直接跑。

- 零第三方依赖（仅 `json` / `argparse` / `urllib`）
- 默认把敏感头替换成 `REDACTED`
- 跳过 `data:`、`blob:` 等非 HTTP URL
- 支持按 host / method / path 过滤

## 安装 / 打开

```bash
git clone https://github.com/temptation2017/har-curl.git
cd har-curl
python3 har_curl.py --help
```

需要 Python 3.8+。无需 `pip install`。

## 用法

```bash
python3 har_curl.py <file.har>
python3 har_curl.py - < file.har          # 从 stdin 读
python3 har_curl.py file.har --host api.example.com
python3 har_curl.py file.har --method POST
python3 har_curl.py file.har --path /v1/orders
python3 har_curl.py file.har --limit 5
python3 har_curl.py file.har --pretty
python3 har_curl.py file.har --include-headers Accept,Content-Type
python3 har_curl.py file.har --all-headers
python3 har_curl.py file.har --keep-secrets   # 危险：保留明文密钥
```

### 常用参数

| 参数 | 说明 |
|------|------|
| `--host SUBSTR` | 主机名包含子串才输出 |
| `--method METHOD` | 只保留该 HTTP 方法 |
| `--path SUBSTR` | path+query 包含子串才输出 |
| `--limit N` | 最多输出 N 条 |
| `--pretty` | 多行 `curl \` 续行格式 |
| `--include-headers A,B` | 只保留列出的请求头（可重复） |
| `--all-headers` | 输出全部请求头（仍默认脱敏） |
| `--keep-secrets` | **危险**：关闭脱敏，原样输出 Cookie / Authorization 等 |

### 默认脱敏规则

以下请求头名（大小写不敏感）的值会被替换为 `REDACTED`：

- `Authorization`
- `Cookie` / `Set-Cookie`
- `X-Api-Key`
- `Proxy-Authorization`

另外，任意头的值若形如 `Bearer …`，也会被脱敏。

使用 `--keep-secrets` 会关闭上述行为——**只在本地、且你明确需要明文时再用**。

## 示例

样例 HAR（虚构域名与假 token）：[`examples/sample.har`](examples/sample.har)

```bash
python3 har_curl.py examples/sample.har --host api.example.com --pretty
```

预期输出（敏感头已脱敏，JSON body 压成一行）：

```bash
curl
  'https://api.example.com/v1/users?limit=10' \
  -H 'Host: api.example.com' \
  -H 'Accept: application/json' \
  -H 'Authorization: REDACTED' \
  -H 'Cookie: REDACTED' \
  -H 'X-Api-Key: REDACTED' \
  -H 'User-Agent: Mozilla/5.0 (compatible; HAR-Sample/1.0)'

curl
  -X POST \
  'https://api.example.com/v1/orders' \
  -H 'Host: api.example.com' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -H 'Authorization: REDACTED' \
  -H 'Proxy-Authorization: REDACTED' \
  --data-raw '{"sku":"WIDGET-1","qty":2,"note":"demo order"}'
```

过滤 POST：

```bash
python3 har_curl.py examples/sample.har --method POST --limit 1
```

`data:` URL 会被跳过；`cdn.example.com` 可用 `--host` 排除或保留。

非法 JSON 会以非 0 退出并打印清晰错误：

```bash
echo 'not-json' | python3 har_curl.py -
# error: invalid HAR JSON: ...
```

## 限制与后续改进

**当前限制**

- 不解析 HAR 里的 `postData.params` 表单字段（仅有 `text` 时才带 body）
- 二进制 / 超大 body（默认 >64KiB）会省略，并加注释说明
- 不自动跟随重定向、不执行 curl（只生成命令）
- 不处理 HTTP/2 伪头（如 `:authority`）；遇到会原样输出或随 allowlist 过滤

**可能的后续**

- `--output` 写文件、`--jq`-style 字段选择
- 把 `multipart` 拆成 `--form`
- 生成 [httpie](https://httpie.io/) / [xh](https://github.com/ducaale/xh) 命令
- 作为库函数 `har_to_curls(path) -> list[str]` 给 agent 调用

## License

MIT — 见 [LICENSE](LICENSE)。
