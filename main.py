#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# airport_real_rtt_web.py —— 终极安全局域网版（多地区SOCKS代理版）
import time
import threading
import sys
import signal
import socket
from datetime import datetime
import httpx
import urllib3
from flask import Flask, jsonify, render_template_string
import logging

log = logging.getLogger("werkzeug")
log.disabled = True
urllib3.disable_warnings()

# ==================== 配置区 ====================
URL = "https://cp.cloudflare.com/generate_204/"
TIMEOUT = 3.5
INTERVAL = 0.20  # 每个地区的测试间隔，总频率 = INTERVAL * 地区数
THRESHOLD_GOOD = 60
THRESHOLD_BAD = 100
THRESHOLD_LOSS = 1000

# 新增：多个SOCKS代理（假设为socks5，可改socks4）
PROXIES = [
    {"name": "HK", "port": 60000, "color": "#ff4500"},  # 橙红 - 香港
    {"name": "JPN", "port": 60048, "color": "#1e90ff"},  # 蓝   - 日本
    {"name": "US West", "port": 60065, "color": "#32cd32"},  # 绿   - 美国西部
]

# 为每个代理创建独立的httpx.Client（支持SOCKS，需要 httpx[socks] 已安装）
clients = []
for p in PROXIES:
    proxy_url = f"socks5://127.0.0.1:{p['port']}"
    client = httpx.Client(
        proxy=proxy_url,
        timeout=httpx.Timeout(connect=2.0, read=TIMEOUT, write=2.0, pool=1.0),
        verify=True,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        },
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        transport=httpx.HTTPTransport(http2=True, retries=1),
    )
    clients.append(client)

# 全局统计（每个地区独立）
regions_stats = []
buffers = []  # 每个地区一个 buffer: list of (ts_str, rtt or None)
full_buffers = []  # 每个地区一个 full_buffer: list of (ts_float, rtt or None)

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
        except:
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
        r = client.get(URL, follow_redirects=False)
        r.close()
        elapsed_ms = (time.time() - start) * 1000
    except Exception:
        pass

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

        sys.stdout.write(
            f"\r\033[K"
            f"[{ts_str}] {name:<8} "
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
            r = clients[idx].get(URL)
            print(f"{name} 预热成功！协议: {r.http_version}")
            r.close()
        except Exception as e:
            print(f"{name} 预热失败: {e}")
    time.sleep(1)

    while running:
        for idx in range(len(PROXIES)):
            if not running:
                break
            test_once(idx)
            time.sleep(INTERVAL)  # 每个地区间隔INTERVAL，总体更高频率


# ==================== Flask Web ====================
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>机场多地区实时 RTT 监控面板</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.jsdelivr.net/npm/echarts/dist/echarts.min.js"></script>
    <style>
        body {margin:0; background:#111; color:#0f0; font-family: Consolas,Monaco,monospace; overflow:hidden;}
        .header {padding:15px; text-align:center; background:#000; position:relative;}
        .clock {position:absolute; left:20px; top:50%; transform:translateY(-50%); font-size:24px; font-weight:bold;}
        .stats {display:flex; justify-content:center; flex-wrap:wrap; gap:18px; padding:15px;}
        .stat-box {background:#222; padding:12px 22px; border-radius:10px; min-width:180px; text-align:center; box-shadow:0 0 15px rgba(0,255,0,0.15);}
        #chart {width:96vw; height:65vh; margin:10px auto;}
    </style>
</head>
<body>
    <div class="header">
        <div class="clock" id="clock">00:00:00</div>
        <h1>机场多地区 CF RTT 监控（最近60秒）</h1>
    </div>
    <div class="stats" id="stats-container">
        <!-- 动态插入每个地区的统计框 -->
    </div>
    <div id="chart"></div>
    <script>
        const chart = echarts.init(document.getElementById('chart'));
        const colors = {{ colors | tojson }};
        const region_names = {{ region_names | tojson }};
        const option = {
            backgroundColor: '#111',
            tooltip: {trigger: 'axis', formatter: params => {
                if (!params || params.length === 0) return '';
                let tip = params[0].axisValue;
                params.forEach(p => {
                    const v = p.data;
                    tip += '<br/>' + p.marker + p.seriesName + ': ' + (v===null ? '<span style="color:#f66">丢包</span>' : v.toFixed(1)+' ms');
                });
                return tip;
            }},
            legend: {textStyle:{color:'#0f0'}, data: region_names},
            grid: {left: '5%', right: '4%', bottom: '10%', containLabel: true},
            xAxis: {type: 'category', boundaryGap: false},
            yAxis: {type: 'value', name: 'RTT (ms)', min: 0, max: 200},
            series: region_names.map((name, i) => ({
                name: name,
                type: 'line',
                smooth: true,
                symbol: 'none',
                lineStyle: {color: colors[i], width: 2.5},
                areaStyle: {color: colors[i] + '22'},
                data: []
            }))
        };
        chart.setOption(option);

        function updateYAxis(all_rtts) {
            let vals = [];
            all_rtts.forEach(series => vals.push(...series.filter(v => v !== null && isFinite(v))));
            if (vals.length === 0) { option.yAxis.max = 200; return; }
            const maxV = Math.max(...vals);
            const margin = Math.max(15, maxV * 0.2);
            option.yAxis.max = Math.min(Math.ceil((maxV + margin)/10)*10, 2000);
        }

        let lastData = null;
        function loop() {
            document.getElementById('clock').textContent = new Date().toLocaleTimeString('zh-CN', {hour12:false});
            fetch('/api/data?' + Date.now())
                .then(r => r.json())
                .then(d => {
                    if (JSON.stringify(d) === JSON.stringify(lastData)) {
                        requestAnimationFrame(loop);
                        return;
                    }
                    lastData = d;

                    // 动态更新统计框
                    document.getElementById('stats-container').innerHTML = d.stats_html;

                    // 更新图表
                    updateYAxis(d.all_rtts);
                    option.xAxis.data = d.timestamps;
                    d.all_rtts.forEach((rtts, i) => {
                        option.series[i].data = rtts;
                    });
                    chart.setOption(option, false);

                    requestAnimationFrame(loop);
                })
                .catch(() => requestAnimationFrame(loop));
        }
        requestAnimationFrame(loop);
    </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(
        HTML_TEMPLATE,
        colors=[p["color"] for p in PROXIES],
        region_names=[p["name"] for p in PROXIES],
    )


@app.route("/api/data")
def api_data():
    with lock:
        # 最近60秒数据（所有地区共用相同时间戳）
        cutoff = time.time() - 60
        all_recent = []
        for fb in full_buffers:
            recent = [x for x in fb if x[0] >= cutoff]
            all_recent.append(recent)

        # 取最多的一组时间戳作为x轴
        max_len_idx = max(range(len(all_recent)), key=lambda i: len(all_recent[i]))
        timestamps = [
            datetime.fromtimestamp(ts).strftime("%H:%M:%S")
            for ts, _ in all_recent[max_len_idx]
        ]

        all_rtts = []
        stats_html = ""
        for idx, recent in enumerate(all_recent):
            name = PROXIES[idx]["name"]
            color = PROXIES[idx]["color"]
            rtts = [r for _, r in recent]
            all_rtts.append(rtts)

            stats = regions_stats[idx]
            sent = stats["sent"]
            loss = stats["loss"]
            received = stats["received"]
            loss_rate = (loss / sent * 100) if sent else 0
            avg = (stats["rtt_sum"] / received) if received else 0
            rtt_min = stats["rtt_min"] if stats["rtt_min"] != float("inf") else 0
            rtt_max = stats["rtt_max"]

            valid = [r for r in rtts if r is not None]
            avg_1min = round(sum(valid) / len(valid), 1) if valid else 0.0

            stats_html += f"""
            <div class="stat-box" style="border:2px solid {color};">
                <strong>{name}</strong><br>
                发送 <strong>{sent}</strong> │ 丢包 <strong>{loss}</strong> ({round(loss_rate, 2)}%)<br>
                总平均 <strong>{round(avg, 1)}</strong> ms │ 1分钟平均 <strong style="font-size:22px;">{avg_1min if valid else "—"}</strong> ms<br>
                最快 <strong>{"—" if rtt_min == 0 else round(rtt_min, 1)}</strong> ms │ 最慢 <strong>{round(rtt_max, 1)}</strong> ms
            </div>
            """

        return jsonify(
            {
                "timestamps": timestamps,
                "all_rtts": all_rtts,
                "stats_html": stats_html,
            }
        )


def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


if __name__ == "__main__":
    lan_ip = get_lan_ip()
    print("=== 多地区机场真实 RTT 测试（安全局域网版） ===")
    print(f"目标地址 : {URL}")
    print(f"地区代理 : {', '.join([p['name'] for p in PROXIES])}")
    print(f"局域网访问 → http://{lan_ip}:5000")
    print("按 Ctrl+C 停止\n")

    threading.Thread(target=main_loop, daemon=True).start()
    app.run(host=f"{get_lan_ip()}", port=5000, threaded=True, debug=False)
