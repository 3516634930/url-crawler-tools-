"""
作者: Star
功能: 多网站深度爬虫 - 自动读取 urls.txt 文件中的起始网址列表，对每个网站进行异步广度优先全站爬取，提取并去重所有符合条件的域名/子域名
日期: 2025-12-19
"""

import asyncio
import httpx
import random
import sys
import os
import time
import datetime
from urllib.parse import urlparse, urljoin
from parsel import Selector

# --- 全局状态（参考 deep_crawler_continue 机制） ---

class GlobalState:
    suffixes = [] 
    max_concurrency = 40
    max_duration = 3600
    start_time = 0.0
    semaphore = None
    crawl_queue = asyncio.Queue()
    visited_urls = set()
    unique_domains = set()
    total_scanned_pages = 0
    req_success = 0
    req_fail = 0
    adjust_lock = asyncio.Lock()
    current_concurrency = 40

state = GlobalState()

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]

def random_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

def clean_domain(url: str) -> str:
    try:
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        parsed = urlparse(url)
        domain = parsed.netloc.split(":")[0].lower()
        return domain
    except:
        return ""

def add_domain(domain: str, base_root: str):
    if not domain:
        return
    save = False
    if state.suffixes:
        if any(domain.endswith(s) or domain == s.lstrip(".") for s in state.suffixes):
            save = True
    else:
        if domain == base_root or domain.endswith("." + base_root):
            save = True
    if save and domain not in state.unique_domains:
        state.unique_domains.add(domain)
        sys.stdout.write(f"\r[+] 新域名: {domain:<60}\n")
        sys.stdout.flush()

def should_enqueue(parsed_link, base_root: str) -> bool:
    netloc = parsed_link.netloc
    if not netloc:
        return False
    return (
        netloc == base_root or
        netloc.endswith("." + base_root) or
        base_root in netloc
    )

async def maybe_adjust_concurrency():
    async with state.adjust_lock:
        total = state.req_success + state.req_fail
        if total < 50:
            return
        fail_rate = state.req_fail / total
        if fail_rate > 0.35 and state.current_concurrency > 10:
            new_c = max(10, state.current_concurrency - 5)
            state.current_concurrency = new_c
            state.semaphore = asyncio.Semaphore(new_c)
            state.req_success = 0
            state.req_fail = 0
            sys.stdout.write(f"\n[!] 失败率较高，降低并发到 {new_c}\n")
            sys.stdout.flush()
        elif fail_rate < 0.15 and state.current_concurrency < state.max_concurrency:
            new_c = min(state.max_concurrency, state.current_concurrency + 5)
            state.current_concurrency = new_c
            state.semaphore = asyncio.Semaphore(new_c)
            state.req_success = 0
            state.req_fail = 0
            sys.stdout.write(f"\n[+] 恢复并发到 {new_c}\n")
            sys.stdout.flush()

async def crawl_worker(client: httpx.AsyncClient):
    while True:
        if time.time() - state.start_time > state.max_duration:
            break
        try:
            try:
                url = state.crawl_queue.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.5)
                continue
            if url in state.visited_urls:
                state.crawl_queue.task_done()
                continue
            state.visited_urls.add(url)
            state.total_scanned_pages += 1
            parsed_url = urlparse(url)
            base_root = parsed_url.netloc.replace("www.", "")
            await asyncio.sleep(random.uniform(0.3, 1.5))
            try:
                async with state.semaphore:
                    resp = await client.get(url, headers=random_headers(), timeout=15.0, follow_redirects=True)
                ctype = resp.headers.get("content-type", "").lower()
                if "text/html" not in ctype:
                    state.req_success += 1
                    state.crawl_queue.task_done()
                    await maybe_adjust_concurrency()
                    continue
                if resp.status_code in (403, 429, 503):
                    state.req_fail += 1
                    await asyncio.sleep(random.uniform(1.5, 3.0))
                    state.crawl_queue.task_done()
                    await maybe_adjust_concurrency()
                    continue
                if "captcha" in resp.text.lower() or "验证码" in resp.text:
                    state.req_fail += 1
                    await asyncio.sleep(5.0)
                    state.crawl_queue.task_done()
                    await maybe_adjust_concurrency()
                    continue
                if resp.status_code >= 400:
                    state.req_fail += 1
                    state.crawl_queue.task_done()
                    await maybe_adjust_concurrency()
                    continue
                state.req_success += 1
                sel = Selector(resp.text)
                links = sel.css("a::attr(href)").getall()
                for link in links:
                    try:
                        full_link = urljoin(str(resp.url), link)
                        parsed_link = urlparse(full_link)
                        link_netloc = parsed_link.netloc
                        if not link_netloc:
                            continue
                        d = clean_domain(full_link)
                        add_domain(d, base_root)
                        if should_enqueue(parsed_link, base_root):
                            path = parsed_link.path.lower()
                            if not any(path.endswith(ext) for ext in [".jpg", ".png", ".gif", ".pdf", ".zip", ".rar", ".exe", ".css", ".js", ".ico", ".svg", ".mp4", ".mp3"]):
                                if full_link not in state.visited_urls:
                                    if state.crawl_queue.qsize() < 10000:
                                        state.crawl_queue.put_nowait(full_link)
                    except:
                        pass
            except Exception:
                state.req_fail += 1
            finally:
                state.crawl_queue.task_done()
                await maybe_adjust_concurrency()
        except Exception:
            pass

async def progress_monitor():
    while True:
        elapsed = time.time() - state.start_time
        remaining = max(0, state.max_duration - elapsed)
        rm, rs = divmod(int(remaining), 60)
        msg = (f"\r[运行中] 待爬: {state.crawl_queue.qsize()} | "
               f"已扫页面: {state.total_scanned_pages} | "
               f"唯一域名: {len(state.unique_domains)} | "
               f"并发: {state.current_concurrency} | "
               f"剩余时间: {rm:02d}:{rs:02d}   ")
        sys.stdout.write(msg)
        sys.stdout.flush()
        if remaining <= 0:
            sys.stdout.write("\n\n[!] 本次工作时间已到，停止爬取。\n")
            break
        await asyncio.sleep(1)

def save_results(interrupted=False):
    print("\n" + "="*50)
    if interrupted:
        print("用户中断，正在保存当前结果...")
    final_domains = sorted(list(state.unique_domains))
    print(f"最终共获取 {len(final_domains)} 个唯一域名")
    for d in final_domains:
        print(d)
    print("="*50)
    save_dir = os.path.join(os.getcwd(), "save")
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)
    filename = f"multi_website_domains_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    filepath = os.path.join(save_dir, filename)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            for d in final_domains:
                f.write(d + "\n")
        print(f"结果已保存至: {filepath}")
    except Exception as e:
        print(f"保存文件失败: {e}")

async def main():
    print("--- 多网站深度爬虫 (By Star) ---")
    urls_file = os.path.join(os.getcwd(), "urls.txt")
    if not os.path.exists(urls_file):
        print("错误: 当前目录下未找到 'urls.txt' 文件。")
        print("请创建该文件，每行输入一个起始网址，然后重试。")
        return
    seeds = []
    with open(urls_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                seeds.append(line)
    if not seeds:
        print("错误: 'urls.txt' 为空或无有效网址。")
        return
    print(f"成功加载 {len(seeds)} 个起始网站")
    s_input = input("请输入要保留的域名后缀（例如 .edu.cn tsinghua.edu.cn，支持多个空格，回车默认各站子域名）: ").strip()
    if s_input:
        state.suffixes = [s.strip() for s in s_input.split() if s.strip()]
        print(f"已设置过滤后缀: {state.suffixes}")
    else:
        print("未设置后缀，将默认保留每个网站的根域名及其子域名。")
    t_input = input("请输入总工作时间（分钟，范围1-300，默认60分钟）: ").strip()
    try:
        minutes = int(t_input)
        if minutes < 1 or minutes > 300:
            minutes = 60
    except:
        minutes = 60
    state.max_duration = minutes * 60
    print(f"总工作时间设置为: {minutes} 分钟")
    c_input = input("请输入最大并发请求数（推荐20-60，默认40）: ").strip()
    try:
        cc = int(c_input) if c_input else 40
        if cc < 1:
            cc = 40
    except:
        cc = 40
    state.max_concurrency = cc
    state.current_concurrency = cc
    state.semaphore = asyncio.Semaphore(cc)
    print(f"最大并发数设置为: {cc}")
    for s in seeds:
        if "://" in s:
            u = s
            parsed = urlparse(u)
            base_root = parsed.netloc.replace("www.", "")
            d = parsed.netloc.split(":")[0].lower()
            add_domain(d, base_root)
            state.crawl_queue.put_nowait(u)
        else:
            d = s.strip()
            base_root = d.replace("www.", "")
            add_domain(d, base_root)
            state.crawl_queue.put_nowait(f"https://{d}/")
            state.crawl_queue.put_nowait(f"http://{d}/")
    limits = httpx.Limits(max_keepalive_connections=state.max_concurrency, max_connections=state.max_concurrency)
    state.start_time = time.time()
    print("\n启动爬虫...")
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        async with httpx.AsyncClient(http2=True, verify=False, limits=limits, timeout=15.0) as client:
            monitor_task = asyncio.create_task(progress_monitor())
            workers = [asyncio.create_task(crawl_worker(client)) for _ in range(state.max_concurrency)]
            await asyncio.gather(*workers)
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
    except KeyboardInterrupt:
        save_results(interrupted=True)
        return
    save_results()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        save_results(interrupted=True)
