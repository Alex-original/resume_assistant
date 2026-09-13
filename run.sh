#!/bin/bash
# 启动求职助手
#
#   ./run.sh              普通 HTTP，http://127.0.0.1:7870（电脑上可用麦克风）
#   ./run.sh --https      自签 HTTPS，https://<本机IP>:8443（手机可用麦克风）
#
# 为什么手机必须用 HTTPS：
#   浏览器只在「安全上下文」里允许 getUserMedia（拿麦克风）。
#   http://192.168.x.x 不是安全上下文，手机上的录音功能会被直接拒绝。
set -e
cd "$(dirname "$0")"

HOST="${APP_HOST:-127.0.0.1}"
PORT="${APP_PORT:-7870}"

if [ "$1" = "--https" ]; then
  CERT_DIR="data/certs"
  mkdir -p "$CERT_DIR"
  IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo 127.0.0.1)

  if [ ! -f "$CERT_DIR/cert.pem" ]; then
    echo "生成自签证书（含本机 IP: ${IP}）…"
    openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
      -keyout "$CERT_DIR/key.pem" -out "$CERT_DIR/cert.pem" \
      -subj "/CN=$IP" \
      -addext "subjectAltName=IP:$IP,IP:127.0.0.1,DNS:localhost" 2>/dev/null
    echo "  ✅ 证书已生成：$CERT_DIR/cert.pem"
  fi

  echo
  echo "════════════════════════════════════════════"
  echo "  手机访问：https://$IP:8443"
  echo "  电脑访问：https://127.0.0.1:8443"
  echo
  echo "  手机首次打开会提示「不安全」——这是自签证书的正常现象。"
  echo "  iOS Safari：点「显示详细信息」→「访问此网站」"
  echo "  Android Chrome：点「高级」→「继续前往」"
  echo "  信任之后麦克风才能用。"
  echo "════════════════════════════════════════════"
  echo
  exec .venv/bin/python -m uvicorn app.main:app \
    --host 0.0.0.0 --port 8443 \
    --ssl-keyfile "$CERT_DIR/key.pem" --ssl-certfile "$CERT_DIR/cert.pem"
fi

echo "════════════════════════════════════════════"
echo "  求职助手  http://$HOST:$PORT"
echo "  （手机访问请用 ./run.sh --https）"
echo "════════════════════════════════════════════"
exec .venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
