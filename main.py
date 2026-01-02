#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# airport_real_rtt_cli.py —— 纯终端版（多地区SOCKS代理，多行同时显示）
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
INTERVAL = 0.20  # 每个地区的测试间隔
THRESHOLD_GOOD = 60
THRESHOLD_BAD = 200
THRESHOLD_LOSS = 1000

# 多个SOCKS代理
PROXIES = [
    {"name": "HK", "port": 60000, "color": "\033[38;5;208m"},  # 橙红
    {"name": "JPN", "port": 60048, "color": "\033[94m"},  # 蓝
    {"name": "US West", "port": 60065, "color": "\033[92m"},  # 绿
]

# 重置颜色
RESET = "\033[0m"
LINES_PER_REGION = 1
TOTAL_LINES = len(PROXIES) * LINES_PER_REGION

# 创建客户端
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

# 统计数据
regions_stats = []
buffers = []
full_buffers = []

for _ in PROXIES:
    regions_stats.append(
        {
            "sent": 0,
            "received": 0,
            "loss": 0,
            "rtt_sum": 0.0,
            "rtt_min": float("inf"),
            "rtt_max": 0.0,
            "last_rtt": None,  # 用于本次显示
            "last_status": "",  # 优秀/较差/丢包
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
    print("\n\n已停止，正在退出...")
    print("\033[?25h", end="")  # 显示光标
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ================== 核心测试函数 ==================
def test_once(idx):
    name = PROXIES[idx]["name"]
    color = PROXIES[idx]["color"]
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

    ts_float = time.time()
    val = elapsed_ms if elapsed_ms <= THRESHOLD_LOSS else None

    with lock:
        stats = regions_stats[idx]
        stats["sent"] += 1
        if val is None:
            stats["loss"] += 1
            stats["last_rtt"] = None
            stats["last_status"] = f"{color}*** 丢包{RESET}"
        else:
            stats["received"] += 1
            stats["rtt_sum"] += val
            stats["rtt_min"] = min(stats["rtt_min"], val)
            stats["rtt_max"] = max(stats["rtt_max"], val)
            stats["last_rtt"] = val

            if val < THRESHOLD_GOOD:
                stats["last_status"] = f"{color}{val:5.1f}ms 优秀{RESET}"
            elif val > THRESHOLD_BAD:
                stats["last_status"] = f"{color}{val:5.1f}ms 较差{RESET}"
            else:
                stats["last_status"] = f"{color}{val:5.1f}ms      {RESET}"

        # 保存历史（可选）
        buffers[idx].append((datetime.now().strftime("%H:%M:%S"), val))
        full_buffers[idx].append((ts_float, val))
        if len(buffers[idx]) > 5000:
            buffers[idx].pop(0)
        while full_buffers[idx] and full_buffers[idx][0][0] < time.time() - 3600:
            full_buffers[idx].pop(0)


# ================== 显示函数 ==================
def refresh_display():
    with lock:
        # 上移光标到本轮开始位置并清除以下行
        if TOTAL_LINES > 0:
            sys.stdout.write(f"\033[{TOTAL_LINES}A")  # 上移
            sys.stdout.write("\033[K" * TOTAL_LINES)  # 每行清空

        current_time = datetime.now().strftime("%H:%M:%S")

        for idx, stats in enumerate(regions_stats):
            name = PROXIES[idx]["name"]
            color = PROXIES[idx]["color"]

            loss_rate = stats["loss"] / stats["sent"] * 100 if stats["sent"] else 0
            avg = stats["rtt_sum"] / stats["received"] if stats["received"] else 0
            rtt_min = stats["rtt_min"] if stats["rtt_min"] != float("inf") else 0

            line = (
                f"[{current_time}] {color}{name:<10}{RESET} | "
                f"发 {stats['sent']:<5} | "
                f"丢 {stats['loss']:<4} ({loss_rate:5.2f}%) | "
                f"平均 {avg:6.1f}ms | "
                f"极值 {rtt_min:6.1f}ms - {stats['rtt_max']:6.1f}ms | "
                f"{stats['last_status']}"
            )
            print(line)

        sys.stdout.flush()


def main_loop():
    # 预热
    print("正在预热 HTTP/2 连接...")
    for idx, p in enumerate(PROXIES):
        name = p["name"]
        try:
            c = clients[idx]
            with c.get(URL) as r:
                print(f"{name} 预热成功！协议: {r.http_version}")
        except Exception as e:
            print(f"{name} 预热失败: {e}")
    time.sleep(1)

    print("\n=== 多地区机场真实 RTT 测试（多行实时显示） ===")
    print(f"目标地址 : {URL}")
    print(f"测试间隔 : 每个地区约 {INTERVAL:.2f} 秒")
    print(f"超时设置 : {TIMEOUT:.1f} 秒")
    print("按 Ctrl+C 停止测试\n")

    # 打印初始空行（之后会被覆盖）
    for _ in range(TOTAL_LINES):
        print(" " * 100)  # 预占位
    print("\033[?25l", end="")  # 隐藏光标
    sys.stdout.flush()
    while running:
        start_time = time.time()

        # 测试所有地区
        for idx in range(len(PROXIES)):
            if not running:
                break
            test_once(idx)

        # 刷新显示（一次性覆盖所有行）
        refresh_display()

        # 控制频率
        elapsed = time.time() - start_time
        sleep_time = max(0, INTERVAL * len(PROXIES) - elapsed)
        time.sleep(sleep_time)


if __name__ == "__main__":
    threading.Thread(target=main_loop, daemon=True).start()

    try:
        while running:
            time.sleep(1)
    except KeyboardInterrupt:
        signal_handler(None, None)
