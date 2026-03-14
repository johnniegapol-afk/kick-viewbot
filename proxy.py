import sys
import time
import random
import datetime
import threading
import json
import os
from threading import Thread, Semaphore
import tls_client

try:
    import websocket
except ImportError:
    print("[!] websocket-client not installed!")
    print("[!] Run: pip install websocket-client")
    sys.exit(1)

CLIENT_TOKEN = "e1393935a959b4020a4491574f6490129f678acdaa92760471263db43487f823"
VIEWS_PER_PROXY = 200

channel = ""
channel_id = None
stream_id = None
proxy_list = []
max_threads = 0
threads = []
thread_limit = None
active = 0
stop = False
start_time = None
lock = threading.Lock()
connections = 0
attempts = 0
pings = 0
heartbeats = 0
viewers = 0
last_check = 0


def load_proxies(filename="proxy.txt"):
    result = []
    if not os.path.exists(filename):
        print(f"[!] {filename} not found!")
        print("[!] Create proxy.txt with one proxy per line:")
        print("    ip:port")
        print("    ip:port:user:pass")
        print("    http://ip:port")
        print("    http://user:pass@ip:port")
        sys.exit(1)
    with open(filename, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                result.append(line)
    if not result:
        print(f"[!] No proxies found in {filename}!")
        sys.exit(1)
    return result


def parse_proxy_url(proxy_str):
    """Convert proxy string to http:// URL format for tls_client"""
    proxy_str = proxy_str.strip()
    if "://" in proxy_str:
        return proxy_str
    if "@" in proxy_str:
        return f"http://{proxy_str}"
    parts = proxy_str.split(":")
    if len(parts) == 2:
        return f"http://{parts[0]}:{parts[1]}"
    elif len(parts) == 4:
        return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    return f"http://{proxy_str}"


def parse_proxy_parts(proxy_str):
    """Parse proxy string into (host, port, auth_tuple_or_None) for websocket-client"""
    proxy_str = proxy_str.strip()
    for prefix in ["http://", "https://", "socks5://", "socks4://"]:
        if proxy_str.lower().startswith(prefix):
            proxy_str = proxy_str[len(prefix):]
            break
    auth = None
    if "@" in proxy_str:
        auth_part, host_part = proxy_str.rsplit("@", 1)
        if ":" in auth_part:
            user, passwd = auth_part.split(":", 1)
            auth = (user, passwd)
        hp = host_part.split(":")
        return hp[0], int(hp[1]) if len(hp) > 1 else 8080, auth
    parts = proxy_str.split(":")
    if len(parts) == 2:
        return parts[0], int(parts[1]), None
    elif len(parts) == 4:
        return parts[0], int(parts[1]), (parts[2], parts[3])
    return proxy_str, 8080, None


def get_proxy_dict(proxy_url):
    return {"http": proxy_url, "https": proxy_url}


def clean_channel_name(name):
    if "kick.com/" in name:
        parts = name.split("kick.com/")
        ch = parts[1].split("/")[0].split("?")[0]
        return ch.lower()
    return name.lower()


def get_channel_info(name, proxy_url=None):
    global channel_id, stream_id
    try:
        s = tls_client.Session(client_identifier="chrome_120", random_tls_extension_order=True)
        if proxy_url:
            s.proxies = get_proxy_dict(proxy_url)
        s.headers.update({
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://kick.com/',
            'Origin': 'https://kick.com',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        })

        try:
            response = s.get(f'https://kick.com/api/v2/channels/{name}')
            if response.status_code == 200:
                data = response.json()
                channel_id = data.get("id")
                if 'livestream' in data and data['livestream']:
                    stream_id = data['livestream'].get('id')
                return channel_id
        except:
            pass

        try:
            response = s.get(f'https://kick.com/api/v1/channels/{name}')
            if response.status_code == 200:
                data = response.json()
                channel_id = data.get("id")
                if 'livestream' in data and data['livestream']:
                    stream_id = data['livestream'].get('id')
                return channel_id
        except:
            pass

        try:
            response = s.get(f'https://kick.com/{name}')
            if response.status_code == 200:
                import re
                patterns = [
                    r'"id":(\d+).*?"slug":"' + re.escape(name) + r'"',
                    r'"channel_id":(\d+)',
                    r'channelId["\']:\s*(\d+)',
                ]
                for pattern in patterns:
                    match = re.search(pattern, response.text, re.IGNORECASE)
                    if match:
                        channel_id = int(match.group(1))
                        break
                stream_patterns = [
                    r'"livestream":\s*\{[^}]*"id":(\d+)',
                    r'livestream.*?"id":(\d+)',
                ]
                for pattern in stream_patterns:
                    match = re.search(pattern, response.text, re.IGNORECASE | re.DOTALL)
                    if match:
                        stream_id = int(match.group(1))
                        break
                if channel_id:
                    return channel_id
        except:
            pass

        print(f"Failed to get info for: {name}")
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None
    finally:
        if channel_id:
            print(f"Channel ID: {channel_id}")
        if stream_id:
            print(f"Stream ID: {stream_id}")


def get_token(proxy_url=None):
    try:
        s = tls_client.Session(client_identifier="chrome_120", random_tls_extension_order=True)
        if proxy_url:
            s.proxies = get_proxy_dict(proxy_url)
        s.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        })

        try:
            s.get("https://kick.com")
            s.headers["X-CLIENT-TOKEN"] = CLIENT_TOKEN
            response = s.get('https://websockets.kick.com/viewer/v1/token')
            if response.status_code == 200:
                data = response.json()
                token = data.get("data", {}).get("token")
                if token:
                    return token
        except:
            pass

        endpoints = [
            'https://websockets.kick.com/viewer/v1/token',
            'https://kick.com/api/websocket/token',
            'https://kick.com/api/v1/websocket/token'
        ]
        for endpoint in endpoints:
            try:
                s.headers["X-CLIENT-TOKEN"] = CLIENT_TOKEN
                response = s.get(endpoint)
                if response.status_code == 200:
                    data = response.json()
                    token = data.get("data", {}).get("token") or data.get("token")
                    if token:
                        return token
            except:
                continue
        return None
    except:
        return None


def get_viewer_count(proxy_url=None):
    global viewers, last_check
    if not stream_id:
        return 0
    try:
        s = tls_client.Session(client_identifier="chrome_120", random_tls_extension_order=True)
        if proxy_url:
            s.proxies = get_proxy_dict(proxy_url)
        s.headers.update({
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://kick.com/',
            'Origin': 'https://kick.com',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        })
        url = f"https://kick.com/current-viewers?ids[]={stream_id}"
        response = s.get(url)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                viewers = data[0].get('viewers', 0)
                last_check = time.time()
                return viewers
        return 0
    except:
        return 0


def show_stats():
    global stop, start_time, connections, attempts, pings, heartbeats, viewers, last_check
    print("\n\n\n\n")

    while not stop:
        try:
            now = time.time()
            if now - last_check >= 5:
                px = parse_proxy_url(random.choice(proxy_list)) if proxy_list else None
                get_viewer_count(px)

            with lock:
                elapsed_str = "0s"
                if start_time:
                    elapsed = datetime.datetime.now() - start_time
                    elapsed_str = f"{int(elapsed.total_seconds())}s"
                ws_count = connections
                ws_attempts = attempts
                ping_count = pings
                hb_count = heartbeats
                s_display = stream_id if stream_id else 'N/A'
                v_display = viewers if viewers else 'N/A'

            total_target = len(proxy_list) * VIEWS_PER_PROXY

            print("\033[4A", end="")
            print(f"\033[2K\r[x] Proxies: \033[32m{len(proxy_list)}\033[0m | Target: \033[32m{total_target}\033[0m | Connections: \033[32m{ws_count}\033[0m | Attempts: \033[32m{ws_attempts}\033[0m")
            print(f"\033[2K\r[x] Pings: \033[32m{ping_count}\033[0m | Heartbeats: \033[32m{hb_count}\033[0m | Duration: \033[32m{elapsed_str}\033[0m | Stream: \033[32m{s_display}\033[0m")
            print(f"\033[2K\r[x] Viewers: \033[32m{v_display}\033[0m | Updated: \033[32m{time.strftime('%H:%M:%S', time.localtime(last_check)) if last_check else 'N/A'}\033[0m")
            print(f"\033[2K\r[x] Views/Proxy: \033[32m{VIEWS_PER_PROXY}\033[0m")
            sys.stdout.flush()
            time.sleep(1)
        except:
            time.sleep(1)


def connect(proxy_str):
    send_connection(proxy_str)


def send_connection(proxy_str):
    global active, attempts, channel_id, thread_limit
    active += 1
    with lock:
        attempts += 1
    try:
        proxy_url = parse_proxy_url(proxy_str)
        token = get_token(proxy_url)
        if not token:
            return
        if not channel_id:
            channel_id = get_channel_info(channel, proxy_url)
            if not channel_id:
                return
        proxy_host, proxy_port, proxy_auth = parse_proxy_parts(proxy_str)
        websocket_connect(token, proxy_host, proxy_port, proxy_auth)
    except:
        pass
    finally:
        active -= 1
        thread_limit.release()


def websocket_connect(token, proxy_host, proxy_port, proxy_auth=None):
    global connections, stop, channel_id, heartbeats, pings
    connected = False
    ws = None
    try:
        url = f"wss://websockets.kick.com/viewer/v1/connect?token={token}"

        ws = websocket.create_connection(
            url,
            http_proxy_host=proxy_host,
            http_proxy_port=proxy_port,
            http_proxy_auth=proxy_auth,
            header={"Origin": "https://kick.com"},
            timeout=30
        )

        with lock:
            connections += 1
        connected = True

        handshake = {
            "type": "channel_handshake",
            "data": {"message": {"channelId": channel_id}}
        }
        ws.send(json.dumps(handshake))
        with lock:
            heartbeats += 1

        ping_count = 0
        while not stop and ping_count < 10:
            ping_count += 1
            ws.send(json.dumps({"type": "ping"}))
            with lock:
                pings += 1
            time.sleep(12 + random.randint(1, 5))
    except:
        pass
    finally:
        if ws:
            try:
                ws.close()
            except:
                pass
        if connected:
            with lock:
                if connections > 0:
                    connections -= 1


def run(channel_name):
    global max_threads, channel, start_time, threads, thread_limit, channel_id, proxy_list

    proxy_list = load_proxies("proxy.txt")
    max_threads = len(proxy_list) * VIEWS_PER_PROXY

    channel = clean_channel_name(channel_name)
    thread_limit = Semaphore(max_threads)
    start_time = datetime.datetime.now()

    first_proxy = parse_proxy_url(proxy_list[0])
    channel_id = get_channel_info(channel, first_proxy)
    if not channel_id:
        channel_id = get_channel_info(channel)
    if not channel_id:
        print("[!] Could not get channel info. Exiting.")
        sys.exit(1)

    print(f"\n[+] Loaded {len(proxy_list)} proxies")
    print(f"[+] Views per proxy: {VIEWS_PER_PROXY}")
    print(f"[+] Total target views: {max_threads}")
    print(f"[+] Channel: {channel}")
    print(f"[+] Starting...\n")

    proxy_assignments = []
    for p in proxy_list:
        for _ in range(VIEWS_PER_PROXY):
            proxy_assignments.append(p)

    threads = []

    stats_thread = Thread(target=show_stats, daemon=True)
    stats_thread.start()

    idx = 0
    while not stop:
        proxy_str = proxy_assignments[idx % len(proxy_assignments)]
        if thread_limit.acquire():
            t = Thread(target=connect, args=(proxy_str,))
            t.daemon = True
            t.start()
            threads.append(t)
            time.sleep(0.05)
        idx += 1

        if idx % 500 == 0:
            threads = [t for t in threads if t.is_alive()]

        if stop:
            break

    for t in threads:
        t.join(timeout=5)


if __name__ == "__main__":
    try:
        os.system('cls' if os.name == 'nt' else 'clear')
        print("=" * 50)
        print("  Kick Viewer Bot - Proxy Mode")
        print(f"  {VIEWS_PER_PROXY} views per proxy")
        print("=" * 50)
        print()

        if not os.path.exists("proxy.txt"):
            print("[!] proxy.txt not found!")
            print("[!] Create proxy.txt with one proxy per line:")
            print("    ip:port")
            print("    ip:port:user:pass")
            print("    http://ip:port")
            print("    http://user:pass@ip:port")
            sys.exit(1)

        channel_input = input("Enter channel name or URL: ").strip()
        if not channel_input:
            print("Channel name needed.")
            sys.exit(1)

        run(channel_input)
    except KeyboardInterrupt:
        stop = True
        print("\nStopping...")
        sys.exit(0)
