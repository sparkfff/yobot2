# yobot2 运行、初始化与配置

[项目主页](https://github.com/sparkfff/yobot2) · [会战指令](../../README.md#指令表)

## 运行

当前自动化测试使用 Python 3.9。使用独立虚拟环境安装 [requirements.txt](requirements.txt) 中的依赖，然后在 `src/client` 目录启动程序：

```sh
python -m pip install -r requirements.txt
python main.py
```

Linux 首次执行会生成 `yobotg.sh` 并退出；按提示运行 `sh yobotg.sh` 启动。系统时区应设为北京/上海时区。

## 首次初始化

首次启动会创建 `yobot_data`，写入 `yobot_config.json`、Boss 配置及数据库；终端会显示配置文件的完整路径。默认监听 `0.0.0.0:9222`。

1. 首次启动后停止程序，编辑生成的 `yobot_config.json`。
2. 设置 `access_token`、`super-admin` 和网页地址等配置，再启动程序。
3. 在兼容 CQ HTTP API 的机器人端配置到本程序的反向 WebSocket 连接，默认地址为 `ws://127.0.0.1:9222/ws/`；不同机器运行时替换主机地址，并设置相同的令牌。
4. 连接后向机器人发送 `help` 或“帮助”打开本地帮助页，发送“登录”获取网页登录链接。

如启动目录已有 `yobot_config.json`，程序使用该目录作为数据目录；否则使用 `src/client/yobot_data`。建议始终在 `src/client` 目录运行，避免启动器读取的配置与程序数据目录不一致。打包版本的数据目录位于程序旁。

## 配置

配置文件采用 JSON 格式，修改后重启生效。默认值见 [default_config.json](packedfiles/default_config.json)。

| 配置项 | 说明 |
| --- | --- |
| `host` / `port` | 监听地址与端口，默认 `0.0.0.0` / `9222`。 |
| `access_token` | 机器人接口令牌，默认空字符串；应改为随机生成的非空令牌，并在机器人端配置相同值。 |
| `super-admin` | 超级管理员 QQ 号数组，例如 `[123456789]`。若留空，首位通过机器人请求登录的用户会成为超级管理员。 |
| `public_address` | 用户可访问的网页地址，例如 `http://127.0.0.1:9222/`（仅本机）或自行配置的域名 `https://bot.example.com/`。默认 `null` 时程序尝试检测公网 IP。 |
| `public_basepath` | 网页路径前缀，默认 `/`；部署在子路径时需与反向代理配置一致。 |
| `web_gzip` | 保持默认 `0`，避免网页静态资源加载不全。 |

### access_token

可用以下命令生成随机令牌，将结果写入配置文件的 `access_token` 字段，并同步到机器人端的连接配置：

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

配置示例（请替换令牌及 QQ 号）：

```json
{
    "access_token": "替换为生成的随机令牌",
    "super-admin": [123456789],
    "public_address": "http://127.0.0.1:9222/"
}
```

原 yobot.win 文档站及免费域名服务已不可用。网页帮助由本程序提供，默认地址为 `http://127.0.0.1:9222/help/`；需要公网访问时自行配置域名和反向代理，并更新 `public_address`。

## 打包

（一般不建议对 python 项目打包）

安装 `pyinstaller`

```sh
pip install pyinstaller
```

打包程序

```sh
pyinstaller main.spec
```

在 `dist` 中找到目标文件

## 扩展

见[custom.py](./ybplugins/custom.py)文件

## 移植

经过多次迭代，yobot与[cq-http-api](https://github.com/richardchien/coolq-http-api/)的耦合越来越深，不再适合移植了
