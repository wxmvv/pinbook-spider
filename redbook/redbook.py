# selenium 4
# from gettext import find
import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
import time
from scrapy import Selector
import os
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from retrying import retry
import requests
from tqdm import tqdm
import logging
import random
import json
from datetime import datetime

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", handlers=[logging.FileHandler("redbook/spider.log"), logging.StreamHandler()])
logger = logging.getLogger(__name__)

# 加载代理IP配置
PROXY_LIST = []
try:
    with open("redbook/proxy_list.json", "r") as f:
        PROXY_LIST = json.load(f)
except FileNotFoundError:
    logger.warning("没有找到代理IP配置文件")

# 失败URL存储
FAILED_URLS_FILE = "redbook/failed_urls.txt"

stealth_path = "redbook/stealth.min.js"
with open(stealth_path, "r") as f:
    stealth_js = f.read()


class RedbookSpider:
    # 基础方法
    def __init__(self, dev=False, headless=False) -> None:
        self.options = webdriver.ChromeOptions()
        if dev:
            self.options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
            # self.options.add_experimental_option("excludeSwitches", ["enable-automation"])
            # self.options.add_experimental_option("useAutomationExtension", False)
        if headless:
            self.options.add_argument("--headless")
            self.options.add_argument("--disable-blink-features=AutomationControlled")
        self.driver = webdriver.Chrome(options=self.options, service=ChromeService(ChromeDriverManager().install()), keep_alive=True)
        # stealth.min.js 注入脚本 隐藏selenium特征 防止反扒
        # 参考 https://testerhome.com/topics/40149
        # 脚本项目原地址 https://github.com/berstend/puppeteer-extra/tree/master/packages/extract-stealth-evasions
        # 这个地址是requireCool自动生成的 https://github.com/requireCool/stealth.min.js

        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": stealth_js})
        self.cookies = None
        self.proxy_index = 0

    def save_failed_url(self, url: str, error: str):
        """保存失败的URL到文件"""
        with open(FAILED_URLS_FILE, "a") as f:
            f.write(f"{url}\t{error}\t{datetime.now()}\n")
        logger.error(f"URL处理失败: {url}, 错误: {error}")

    def rotate_proxy(self):
        """轮换代理IP"""
        if not PROXY_LIST:
            return None
        self.proxy_index = (self.proxy_index + 1) % len(PROXY_LIST)
        return PROXY_LIST[self.proxy_index]

    def check_network(self, url="https://www.xiaohongshu.com"):
        """检查网络连接"""
        try:
            proxy = self.rotate_proxy()
            proxies = {"http": proxy, "https": proxy} if proxy else None
            response = requests.get(url, timeout=5, proxies=proxies)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"网络检查失败: {str(e)}")
            return False

    @retry(stop_max_attempt_number=3, wait_fixed=2000)
    def get_with_retry(self, url: str, wait_element: tuple[str, str] | None = None):
        """带重试的页面加载"""
        try:
            self.driver.get(url)
            if wait_element:
                WebDriverWait(self.driver, 10).until(EC.presence_of_element_located(wait_element))
            return True
        except Exception as e:
            logger.error(f"页面加载失败: {url}, 错误: {str(e)}")
            raise

    def get_local_cookies(self):
        with open("./cookies.txt", "r+", encoding="utf-8") as f:
            r = f.read()
        self.cookies = eval(r)
        for a in self.cookies:
            self.driver.add_cookie(a)
        return self.cookies

    def close(self):
        self.driver.quit()

    def read_url_fromcsv(self, filename: str):
        print("Current working directory:", os.getcwd())
        # 判断是绝对地址还是相对地址
        if filename.find("/") != 0:
            filename = os.getcwd() + "/" + filename
        df = pd.read_csv(filename)
        return df["url"].values.tolist()

    def get_userPostedFeeds(self, profileUrl):
        try:
            # 初始化
            d = self.driver
            logger.info(f"开始获取用户发布列表: {profileUrl}")

            # 创建输出目录（如果不存在）
            os.makedirs(f"{os.getcwd()}/redbook", exist_ok=True)

            # 检查网络并重试加载页面
            if not self.check_network():
                raise Exception("网络连接失败")

            self.get_with_retry(url=profileUrl, wait_element=("css selector", "#userPostedFeeds"))

            exploreList = []
            bannerheight = 400
            lastscollheight = 0
            scollheight = 0
            exploreListcsv = []
            scroll_count = 0
            max_scroll_attempts = 100  # 防止无限滚动

            # 开始循环滚动&读取列表，直到滚动到底部
            retry_count = 0  # 移到外部，避免重置
            prev_scroll_height = 0
            with tqdm(desc="滚动加载页面", unit="项") as pbar:
                while scroll_count < max_scroll_attempts:
                    try:
                        s = Selector(text=d.page_source)
                        list1 = s.css("#userPostedFeeds section").extract()
                        avatar = s.css(".user-name::text").extract_first()

                        initial_count = len(exploreList)
                        for a in list1:
                            sa = Selector(text=a)
                            title = sa.css(".title span::text").extract_first()
                            # 这里使用 携带xsec_token xsec_source 的链接
                            href_old = sa.css("a[href^='/explore/']::attr(href)").extract_first()
                            # 这样的链接 /user/profile/6757ca5e000000001c018538/6819a25d000000000303ba56?xsec_token=ABejTkqNbdQk0P_vX88TdABoABHnMA3l-zT9cXQQaFPCA=&amp;xsec_source=pc_user
                            # /user/profile开头以包含xsec_token xsec_source
                            href = sa.css("a[href^='/user/profile/'][href*='xsec_token'][href*='xsec_source']::attr(href)").extract_first()
                            url = "https://www.xiaohongshu.com" + href if href else None
                            imgurl = sa.css(".cover.ld.mask::attr(style)").re_first(r'url\("([^"]+)"\)')
                            like_count = sa.css(".like-wrapper .count::text").extract_first()

                            if not url:
                                continue

                            if url not in exploreList:
                                exploreListcsv.append([title, href, url, imgurl, like_count])
                                exploreList.append(url)

                        # 更新进度条，显示新增的数量
                        new_items = len(exploreList) - initial_count
                        if new_items > 0:
                            pbar.update(new_items)
                            retry_count = 0  # 有新内容，重置重试计数

                        listheight = int(s.css("#userPostedFeeds").attrib["style"].split("height:")[1].split("px")[0])
                        scollheight = listheight + bannerheight
                        d.execute_script("window.scrollTo(0,{})".format(scollheight))

                        if lastscollheight == scollheight:
                            # 连续3次没有新内容才认为到达底部
                            retry_count += 1
                            logger.debug(f"滚动停止，重试次数: {retry_count}")
                            if retry_count >= 3:
                                logger.info(f"已到达页面底部，共获取{len(exploreList)}条数据")
                                # 在这里保存数据并退出循环
                                if exploreListcsv and len(exploreListcsv) > 0:
                                    # 获取用户名，如果不存在则使用时间戳
                                    s = Selector(text=d.page_source)
                                    avatar = s.css(".user-name::text").extract_first() or str(int(time.time()))

                                    output_path = f"{os.getcwd()}/redbook/{avatar}-postlist.csv"
                                    pd.DataFrame(
                                        exploreListcsv,
                                        columns=["title", "href", "url", "imgurl", "like_count"],
                                    ).to_csv(output_path)
                                    logger.info(f"数据已保存到: {output_path}")
                                else:
                                    logger.warning("没有数据需要保存")
                                return exploreList
                            # 如果高度相同，多等待一会儿再试
                            time.sleep(random.uniform(5, 8))
                            continue
                        else:
                            retry_count = 0  # 只有在高度不同时才重置计数器

                        retry_count = 0  # 重置重试计数
                        lastscollheight = scollheight
                        scroll_count += 1

                        # 更新总进度
                        pbar.set_postfix({"获取数量": len(exploreList)}, refresh=True)

                        # 随机等待时间，增加波动范围
                        time.sleep(random.uniform(3, 7))

                    except Exception as e:
                        logger.error(f"滚动加载出错: {str(e)}")
                        time.sleep(5)
                        continue

            if not exploreList:
                raise Exception("未获取到任何数据")

            # 保存数据
            try:
                if exploreListcsv and len(exploreListcsv) > 0:
                    # 获取用户名，如果不存在则使用时间戳
                    s = Selector(text=self.driver.page_source)
                    avatar = s.css(".user-name::text").extract_first() or str(int(time.time()))

                    output_path = f"{os.getcwd()}/redbook/{avatar}-postlist.csv"
                    pd.DataFrame(
                        exploreListcsv,
                        columns=["title", "href", "url", "imgurl", "like_count"],
                    ).to_csv(output_path)
                    logger.info(f"数据已保存到: {output_path}")
                else:
                    logger.warning("没有数据需要保存")
            except Exception as e:
                logger.error(f"保存CSV文件失败: {str(e)}")

            return exploreList

        except Exception as e:
            logger.error(f"获取用户发布列表失败: {str(e)}")
            self.save_failed_url(profileUrl, str(e))
            return []

    def get_detail_fromlist(self, urllist: list[str]):
        exploreDetailList = []
        failed_urls = []

        # 使用tqdm创建进度条
        for a in tqdm(urllist, desc="获取详情"):
            try:
                if not self.check_network():
                    logger.error("网络连接失败，等待重试...")
                    time.sleep(10)
                    continue

                # 使用带重试的页面加载
                self.get_with_retry(url=a, wait_element=(By.CSS_SELECTOR, ".username"))
                e = self.get_detail(url=a, to_csv=True)

                # if e and all(e):  # 检查返回的数据是否完整
                #     exploreDetailList.append(e)
                #     logger.info(f"成功获取详情: {a}")
                # else:
                #     raise Exception("获取到的数据不完整")

                # 随机等待时间，避免被反爬
                time.sleep(random.uniform(2, 4))

            except Exception as e:
                logger.error(f"获取详情失败: {a}, 错误: {str(e)}")
                self.save_failed_url(a, str(e))
                failed_urls.append(a)
                time.sleep(5)  # 失败后等待更长时间

        if not exploreDetailList:
            logger.error("没有成功获取任何详情数据")
            return []

        try:
            avatar = exploreDetailList[0][0]
            df = pd.DataFrame(
                exploreDetailList,
                columns=["avatar", "url", "title", "desc", "tag", "imgurls", "videourl"],
            )
            output_path = f"{os.getcwd()}/redbook/{avatar}-postlist-detail.csv"
            df.to_csv(output_path)
            logger.info(f"成功保存数据到: {output_path}")

            if failed_urls:
                logger.warning(f"有 {len(failed_urls)} 个URL获取失败，已保存到 {FAILED_URLS_FILE}")

        except Exception as e:
            logger.error(f"保存CSV文件失败: {str(e)}")

        return exploreDetailList

    def get_detail(self, url: str, to_csv=True):
        """获取笔记详情"""
        try:
            # 使用带重试的页面加载
            self.get_with_retry(url=url, wait_element=(By.CSS_SELECTOR, ".username"))

            s = Selector(text=self.driver.page_source)
            # 加载完成后等待一段时间，确保数据加载完毕
            time.sleep(random.uniform(2, 4))

            # 提取数据
            avatar = s.css(".username::text").extract_first()
            title = s.css("#detail-title::text").extract_first()
            desc = s.css("#detail-desc>span::text").extract_first()
            tag = s.css("#hash-tag::text").extract()  # list
            imgurls = s.css(".note-slider-img::attr(src)").getall()
            # 去重
            imgurls = list(set(imgurls))
            videourl = s.css(".player-container>div>video::attr(src)").extract_first()

            # 处理空值
            if title == None:
                title = str(int(time.time()))
            if desc == None:
                desc = ""
            if tag == None:
                tag = []
            if imgurls == None:
                imgurls = []
            if videourl == None:
                videourl = ""

            mkdir = os.path.join(os.getcwd(), "redbook", f"{avatar}")
            os.makedirs(mkdir, exist_ok=True)

            # 下载图片
            if imgurls:
                save_dir = os.path.join(os.getcwd(), "redbook", f"{avatar}", f"{avatar}-{title}-images")
                saved_images = []
                for i, img_url in enumerate(imgurls):
                    saved_path = self._download_image(img_url, save_dir, title, i)
                    if saved_path:
                        saved_images.append(saved_path)
                logger.info(f"总共下载了 {len(saved_images)} 张图片")

            if to_csv:
                try:
                    output_path = os.path.join(os.getcwd(), "redbook", f"{avatar}", f"{avatar}-{title}-post-detail.csv")
                    pd.DataFrame(
                        [[avatar, url, title, desc, tag, imgurls, videourl]],
                        columns=[
                            "avatar",
                            "url",
                            "title",
                            "desc",
                            "tag",
                            "imgurls",
                            "videourl",
                        ],
                    ).to_csv(output_path)
                    logger.info(f"数据已保存到: {output_path}")
                except Exception as e:
                    logger.error(f"保存CSV文件失败: {str(e)}")

            return [avatar, title, desc, tag, imgurls]

        except Exception as e:
            logger.error(f"获取笔记详情失败: {url}, 错误: {str(e)}")
            self.save_failed_url(url, str(e))
            return None

    def _download_image(self, img_url: str, save_dir: str, title: str, index: int):
        """下载图片到本地"""
        try:
            # 确保目录存在
            os.makedirs(save_dir, exist_ok=True)

            # 获取图片格式
            img_format = img_url.split(".")[-1].split("?")[0]  # 从 URL 获取图片格式
            if img_format not in ["jpg", "jpeg", "png", "gif", "webp"]:
                img_format = "jpg"  # 默认格式

            # 构建保存路径（使用标题和索引）
            filename = f"{title}_{index}.{img_format}".replace("/", "-")
            save_path = os.path.join(save_dir, filename)

            # 尝试从浏览器缓存获取图片
            img_element = self.driver.find_element(By.CSS_SELECTOR, f"img[src='{img_url}']")
            if img_element:
                # 获取图片的 base64 数据
                img_base64 = self.driver.execute_script("return arguments[0].src.indexOf('data:') === 0 ? arguments[0].src : null;", img_element)

                if img_base64 and img_base64.startswith("data:image"):
                    # 如果能获取到 base64 数据，直接保存
                    import base64

                    img_data = base64.b64decode(img_base64.split(",")[1])
                    with open(save_path, "wb") as f:
                        f.write(img_data)
                    logger.info(f"已从缓存保存图片: {save_path}")
                    return save_path

            # 如果无法从缓存获取，则直接下载
            # 设置请求头，模仿浏览器行为
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}

            # 使用代理（如果有）
            proxy = self.rotate_proxy()
            proxies = {"http": proxy, "https": proxy} if proxy else None

            # 下载图片
            response = requests.get(img_url, headers=headers, proxies=proxies, stream=True)
            if response.status_code == 200:
                with open(save_path, "wb") as f:
                    for chunk in response.iter_content(1024):
                        f.write(chunk)
                logger.info(f"已下载图片: {save_path}")
                return save_path
            else:
                raise Exception(f"下载图片失败，状态码: {response.status_code}")

        except Exception as e:
            logger.error(f"保存图片失败: {str(e)}")
            return None

    def run(self, url: str, local_cookies=False):
        """运行爬虫主程序"""
        try:
            if not url:
                raise ValueError("URL不能为空")

            # 检查网络连接
            if not self.check_network():
                logger.error("网络连接失败，请检查网络后重试")
                return

            # 加载cookies
            if local_cookies:
                try:
                    self.get_local_cookies()
                    logger.info("成功加载本地cookies")
                except Exception as e:
                    logger.error(f"加载cookies失败: {str(e)}")
                    return

            # 根据URL类型执行不同的爬取策略
            if "https" in url and "profile" in url:
                logger.info(f"开始爬取用户主页: {url}")
                url_list = self.get_userPostedFeeds(url)
                if url_list:
                    logger.info(f"成功获取到 {len(url_list)} 个笔记链接")
                time.sleep(random.uniform(2, 4))

            elif "https:" in url and "explore" in url:
                logger.info(f"开始爬取单个笔记: {url}")
                result = self.get_detail(url)
                if result:
                    logger.info("笔记爬取成功")
                time.sleep(random.uniform(2, 4))

            elif "csv" in url and "postlist" in url:
                logger.info(f"开始从CSV文件批量爬取: {url}")
                url_list = self.read_url_fromcsv(url)
                if url_list:
                    logger.info(f"从CSV文件读取到 {len(url_list)} 个URL")
                    self.get_detail_fromlist(url_list)
                time.sleep(random.uniform(2, 4))

            else:
                logger.error(f"无效的URL格式: {url}")
                return

        except Exception as e:
            logger.error(f"运行出错: {str(e)}")
            self.save_failed_url(url, str(e))
        finally:
            logger.info("爬取任务结束")


if __name__ == "__main__":
    print(
        """
        开始前请先使用命令 `sh redbook/start_chrome.sh` 启动chrome
        
        1.输入个人主页url 获取所有笔记的链接，并保存csv 
            (https://www.xiaohongshu.com/user/profile/)
        2.输入笔记url 获取笔记的内容，并保存csv 
            (https://www.xiaohongshu.com/explore/)
        3.输入个人主页csv文件名，获取所有笔记的内容，并保存csv 
            (redbook/xxoo.csv)
        """
    )
    _url = input("请输入url或者文件名:")
    spider = RedbookSpider(dev=True)
    print(_url)
    spider.run(_url)
    time.sleep(3)
    spider.close()
    print("程序结束,请关闭浏览器")
