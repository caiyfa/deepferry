# Financial Ledger Mock Microservice

deepferry HTTP API 数据源验证用例 — 基于 Spring Boot 3.3 的财务总账微服务。

## 业务模型

5 张表，完整业务关联链：

```
员工(Employee) → 取得增值税发票(VAT Invoice) → 申请报销(Reimbursement)
→ 生成会计凭证(Voucher) → 含多条借贷分录(VoucherEntry) → 过账
```

**借贷平衡**：每张凭证的 `total_debit = total_credit`，且分录借方合计 = 贷方合计。

## 快速启动

```bash
# 1. 先启动 deepferry 共享 MySQL（项目根目录）
docker compose --profile full up mysql -d

# 2. 再启动本微服务
cd examples/financial-ledger-mock
docker compose up --build
```

首次构建约 3-5 分钟（下载依赖）。启动后：

| 服务 | 地址 |
|------|------|
| 微服务 API | `http://localhost:8080` |
| MySQL（共享） | `localhost:3306` |

> **独立模式**（不依赖 deepferry 项目）：`docker compose --profile standalone up --build`

## 默认账号

| 字段 | 值 |
|------|-----|
| Username | `admin` |
| Password | `deepferry123` |
| Token 有效期 | 3600s (1小时) |

## 数据库

| 参数 | 值 |
|------|-----|
| Host | `localhost:3306` |
| Database | `finance_ledger` |
| User | `finance` |
| Password | `finance_pass` |

## API 端点

### 认证（无需 Token）

```bash
# 登录
curl -X POST http://localhost:8080/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"deepferry123"}'

# 响应
# {"access_token":"...","refresh_token":"...","token_type":"Bearer","expires_in":3600}

# 刷新 Token
curl -X POST http://localhost:8080/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"<refresh_token>"}'
```

### 业务端点（需要 Bearer Token）

所有列表返回 `{"data": [...], "total": N}`。

```bash
TOKEN="<access_token>"

# 员工列表
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/employees
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8080/api/v1/employees?department=财务部"

# 发票列表（含嵌套 seller/buyer 对象，验证 deepferry 扁平化）
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/invoices
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8080/api/v1/invoices?invoiceType=专票"

# 报销列表（嵌套 employee + invoice）
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/reimbursements
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8080/api/v1/reimbursements?status=approved&category=差旅"
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8080/api/v1/reimbursements?department=销售部"

# 凭证列表（嵌套 reimb + entries 数组）
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/vouchers
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8080/api/v1/vouchers?status=posted"

# 凭证详情（单条，不包 data 数组）
curl -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/v1/vouchers/1
```

### 错误响应格式

```json
{"code": "AUTH_FAILED", "message": "...", "suggestion": "..."}
```

错误码：`AUTH_FAILED` (401)、`FORBIDDEN` (403)、`NOT_FOUND` (404)、`VALIDATION_ERROR` (400)、`INTERNAL_ERROR` (500)。

## deepferry 集成

参见 `config.deepferry.toml.example` — 包含两种接入方式：

1. **M1 直连 MySQL**：deepferry SQL 数据源直接查询 finance_ledger 数据库
2. **M2 HTTP API + Two-Step Auth**：deepferry HTTP 数据源经微服务访问，自动完成 login → token 注入 → 数据扁平化

## 技术栈

Spring Boot 3.3.5 · Java 21 · Spring Security 6 · jjwt 0.12.6 · Spring Data JPA · MySQL 8.0 · Docker
