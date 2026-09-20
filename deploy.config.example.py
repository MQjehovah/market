# 复制为 deploy.config.py 后按实际环境修改。
# deploy.config.py 已加入 .gitignore，勿把密码提交进仓库。

HOST = "192.168.31.34"
USER = "root"
PASSWORD = "change-me"
REMOTE_DIR = "/root/market"
# 对外访问地址（脚本结束时打印）
PUBLIC_URL = "http://192.168.31.34:8093/"
# SSH 端口
PORT = 22
# 是否在远程重新 build 镜像（首次或依赖变更时建议 True）
BUILD = True
