#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# airport_real_rtt_cli.py —— 纯终端版（多地区SOCKS代理版，无前端）
import time
import threading
import sys
import signal
from datetime import datetime
import niquests
import urllib3

urllib3.disable_warnings()

# ==================== 配置区 ====================
URL = "https://cp.cloudflare.com/generate_204/"
TIMEOUT = 3.5
INTERVAL = 0.20  # 每个地区的测试间隔，总频率 = INTERVAL * 地区数
THRESHOLD_GOOD = 60
THRESHOLD_BAD = 100
THRESHOLD_LOSS = 1000

# 多个SOCKS代理（假设为socks5，可改socks4）
PROXIES = [
    {"name": "HK", "port": 60000, "color": "#ff4500"},  # 橙红 - 香港
    {"name": "JPN", "port": 60048, "color": "#1e90ff"},  # 蓝   - 日本
    {"name": "US West", "port": 60065, "color": "#32cd32"},  # 绿   - 美国西部
]

# 为每个代理创建独立的httpx.Client（支持SOCKS，需要 httpx[socks] 已安装）
clients: list[niquests.Session] = []
for p in PROXIES:
    proxy_url = f"socks5://127.0.0.1:{p['port']}"
    client = niquests.Session(
        pool_connections=20,
        timeout=4,
        disable_http1=True,
        disable_http2=False,
        disable_http3=True,
        disable_ipv6=True,
    )
    client.proxies = {"http": proxy_url, "https": proxy_url}
    clients.append(client)

# 全局统计（每个地区独立）
regions_stats = []
buffers = []  # 保留最近数据，用于可能的后续扩展
full_buffers = []  # 保留一小时内数据，用于可能的后续扩展

for _ in PROXIES:
    regions_stats.append(
        {
            "sent": 0,
            "received": 0,
            "loss": 0,
            "rtt_sum": 0.0,
            "rtt_min": float("inf"),
            "rtt_max": 0.0,
        }
    )
    buffers.append([])
    full_buffers.append([])

running = True
lock = threading.Lock()


def signal_handler(sig, frame):
    global running
    running = False
    for c in clients:
        try:
            c.close()
        except Exception:
            pass
    print("\n已停止，正在退出...")
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ================== 核心测试函数 ==================
def test_once(idx):
    name = PROXIES[idx]["name"]
    client = clients[idx]
    start = time.time()
    elapsed_ms = THRESHOLD_LOSS + 1
    try:
        r = client.get(URL, allow_redirects=False)
        r.close()
        elapsed_ms = (time.time() - start) * 1000
    except Exception as e:
        print(f"{name} 测试请求失败: {e}")
        return

    ts_str = datetime.now().strftime("%H:%M:%S")
    ts_float = time.time()

    with lock:
        val = elapsed_ms if elapsed_ms <= THRESHOLD_LOSS else None
        buffers[idx].append((ts_str, val))
        full_buffers[idx].append((ts_float, val))

        if len(buffers[idx]) > 5000:
            buffers[idx].pop(0)

        # 清理超过1小时旧数据
        while full_buffers[idx] and full_buffers[idx][0][0] < time.time() - 3600:
            full_buffers[idx].pop(0)

        stats = regions_stats[idx]
        stats["sent"] += 1
        if val is None:
            stats["loss"] += 1
            rtt_str = f"{name}: *** 丢包"
        else:
            stats["received"] += 1
            stats["rtt_sum"] += val
            stats["rtt_min"] = min(stats["rtt_min"], val)
            stats["rtt_max"] = max(stats["rtt_max"], val)

            if val < THRESHOLD_GOOD:
                rtt_str = f"{name}: {val:5.1f}ms 优秀"
            elif val > THRESHOLD_BAD:
                rtt_str = f"{name}: {val:5.1f}ms 较差"
            else:
                rtt_str = f"{name}: {val:5.1f}ms"

        loss_rate = stats["loss"] / stats["sent"] * 100 if stats["sent"] else 0
        avg = stats["rtt_sum"] / stats["received"] if stats["received"] else 0

        # 单行覆盖输出（多地区交替显示）
        sys.stdout.write(
            f"\r\033[K"
            f"[{ts_str}] {name:<10} "
            f"发 {stats['sent']:<5} │ "
            f"丢 {stats['loss']:<4} ({loss_rate:5.2f}%) │ "
            f"平均 {avg:6.1f}ms │ "
            f"极值 {(stats['rtt_min'] if stats['rtt_min'] != float('inf') else 0):.1f}–{stats['rtt_max']:.1f}ms │ "
            f"{rtt_str}"
        )
        sys.stdout.flush()


def main_loop():
    print("正在预热 HTTP/2 连接...")
    for idx, p in enumerate(PROXIES):
        name = p["name"]
        try:
            c = clients[idx]
            with c.get(URL, proxies=c.proxies) as r:
                print(f"{name} 预热成功！协议: {r.http_version}")
        except Exception as e:
            print(f"{name} 预热失败: {e}")
    time.sleep(1)
    print("\n开始实时测试（按 Ctrl+C 停止）\n")

    while running:
        for idx in range(len(PROXIES)):
            if not running:
                break
            test_once(idx)
            time.sleep(INTERVAL)


if __name__ == "__main__":
    print("=== 多地区机场真实 RTT 测试（纯终端版） ===")
    print(f"目标地址 : {URL}")
    print(f"地区代理 : {', '.join([p['name'] for p in PROXIES])}")
    print()

    threading.Thread(target=main_loop, daemon=True).start()

    try:
        while running:
            time.sleep(1)
    except KeyboardInterrupt:
        signal_handler(None, None)
