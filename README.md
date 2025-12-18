# url-crawler-tools-
使用说明：

确定已安装Python 3.8+。

安装依赖库： pip install httpx[http2] parsel （注意：如果是在 Windows 上运行，httpx[http2] 可能需要安装额外的编译工具，可以或者直接安装 pip install httpx parsel h2）

运行脚本：crawler-tools.py

交互脚本步骤:

脚本会自动读取当前目录下的urls.txt文件。
按提示输入保留的域名后缀（可选）。
输入总工作时间（分钟）。
输入最大整数。
结果：

爬虫运行过程中会实时显示状态。
运行结束后（或按Ctrl+C中断），结果会自动保存在save文件夹下。
文件名格式: multi_website_domains_YYYYMMDD_HHMMSS.txt
注意：

禁止用于非法用途。
爬取速度较快，请注意目标网站的负载承受能力。
