"""能力层消费者（Capability Layer Consumer）。

让任何本地 Agent / 脚本从 market 能力层：
    sync      同步能力目录
    pull      下载单个能力包
    install   下载 Agent + 依赖，组装成本地 Agent（config/agents/<name>/）
    run       执行任务（本地引擎 / 云端 runtime / A2A 委派）
    serve     把本地 Agent 暴露为 A2A Agent，支持云端/本地互调
    delegate  A2A 客户端：委派任务给云端 Agent
"""

__version__ = "0.1.0"
