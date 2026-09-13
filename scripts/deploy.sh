#!/usr/bin/env bash
#
# 服务器端一键部署（在服务器上跑，不是在你自己电脑上）
#
#   bash scripts/deploy.sh
#
# 前提：已经 git clone 到当前目录，并且 .env 已经填好。
# 这个脚本只做"装环境 + 起服务 + 验证"，不动你的 .env。

set -euo pipefail

cd "$(dirname "$0")/.."
APP_DIR="$(pwd)"

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; OFF=$'\033[0m'
step() { echo; echo "${BOLD}▶ $*${OFF}"; }
ok()   { echo "  ${GREEN}✅ $*${OFF}"; }
warn() { echo "  ${YELLOW}⚠️  $*${OFF}"; }
die()  { echo "  ${RED}❌ $*${OFF}"; exit 1; }

# ── 0. 自检 ────────────────────────────────────────────────
step "0/6 检查环境"

# 这是给服务器（Linux）用的脚本。在你自己的 Mac 上跑会把 Docker 装到本机，
# 而且 macOS 的安装脚本根本不支持——直接挡住，别让人误跑。
if [ "$(uname -s)" != "Linux" ]; then
  die "这个脚本要在服务器（Linux）上跑，当前是 $(uname -s)。"
fi

[ -f .env ] || die "没有 .env。先 cp .env.example .env 并填好密钥"

missing=""
for key in DEEPSEEK_API_KEY DASHSCOPE_API_KEY SECRET_KEY; do
  value="$(grep -E "^${key}=" .env 2>/dev/null | head -1 | cut -d= -f2- || true)"
  [ -z "$value" ] && missing="$missing $key"
done
[ -n "$missing" ] && die "以下项在 .env 里是空的，必须填：$missing"

if grep -q "^ADMIN_PASSWORD=$" .env; then
  warn "ADMIN_PASSWORD 是空的——如果账号库也是空的，会创建不了管理员，届时无法登录"
fi
ok ".env 检查通过"

if ! command -v docker >/dev/null 2>&1; then
  step "1/6 安装 Docker"
  curl -fsSL https://get.docker.com | sh
  ok "Docker 已安装"
else
  ok "Docker 已就绪：$(docker --version)"
fi

if ! docker compose version >/dev/null 2>&1; then
  die "docker compose 不可用。Docker 版本太老？试试 apt install docker-compose-plugin"
fi
ok "compose 已就绪"

# ── 2. 目录与权限 ──────────────────────────────────────────
step "2/6 准备数据目录"
mkdir -p data
# 容器里的进程是 root，但保险起见给出明确权限，避免挂载卷写不进去
chmod 700 data 2>/dev/null || true
ok "data/ 就绪"

# ── 3. 构建与启动 ──────────────────────────────────────────
step "3/6 构建镜像并启动（第一次要 3~5 分钟）"
docker compose up -d --build
ok "已启动"

# ── 4. 等健康检查 ──────────────────────────────────────────
step "4/6 等待服务就绪"
for i in $(seq 1 30); do
  if docker compose exec -T app python -c \
      "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7870/health', timeout=3).status==200 else 1)" \
      >/dev/null 2>&1; then
    ok "服务已就绪（等了 ${i} 秒）"
    break
  fi
  [ "$i" = 30 ] && { docker compose logs --tail 40 app; die "服务 30 秒内没起来，上面是日志"; }
  sleep 1
done

# ── 5. 验证 ────────────────────────────────────────────────
step "5/6 验证关键行为"

# 未登录必须被挡住
code="$(docker compose exec -T app python -c "
import urllib.request
try:
    urllib.request.urlopen('http://127.0.0.1:7870/api/overview', timeout=5)
    print(200)
except urllib.error.HTTPError as e:
    print(e.code)
" 2>/dev/null | tr -d '\r')"
if [ "$code" = "401" ]; then
  ok "登录门禁生效（未登录访问接口返回 401）"
else
  warn "未登录访问返回 $code，预期 401 —— 请检查鉴权中间件"
fi

# 账号是否建出来了（有账号才谈得上登录）
users="$(docker compose exec -T app python -c "
import sys; sys.path.insert(0,'/app')
from app import auth
print(len(auth.list_users()))
" 2>/dev/null | tr -d '\r')"
if [ "${users:-0}" -ge 1 ]; then
  ok "账号库有 $users 个账号"
  docker compose exec -T app python -c "
import sys; sys.path.insert(0,'/app')
from app import auth
for u in auth.list_users():
    print(f\"     #{u['id']} {u['username']}{'（管理员）' if u['is_admin'] else ''}\")
"
else
  warn "账号库是空的——说明 ADMIN_PASSWORD 没生效，登录不了"
fi

# ── 6. 收尾 ────────────────────────────────────────────────
step "6/6 完成"
PUBLIC_IP="$(curl -s --max-time 5 https://api.ipify.org || echo '你的公网IP')"
SITE="$(grep -E '^SITE_ADDRESS=' .env | cut -d= -f2-)"
echo
echo "  ${BOLD}访问地址${OFF}"
if [ "$SITE" = ":443" ] || [ -z "$SITE" ]; then
  echo "    https://${PUBLIC_IP}"
  echo "    （自签证书，浏览器会提示不安全 → 高级 → 继续前往）"
else
  echo "    https://${SITE}"
fi
echo
echo "  ${BOLD}用 .env 里的 ADMIN_USERNAME / ADMIN_PASSWORD 登录${OFF}"
echo "  登录后第一件事：我的 → 设置 → 改密码"
echo
echo "  常用命令："
echo "    docker compose logs -f app     看日志"
echo "    docker compose restart app     重启"
echo "    docker compose ps              看状态"
