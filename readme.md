# 你只需要出刀2（KCRR 环奈连结R Relink）

## 📋 功能更新

相比 [第一版](https://github.com/SonderXiaoming/kanna_connection_redive)，KCRR 在功能上进行了大幅度的更新和优化，主要包括：

- 🎯 公会深域查询
- 🛡️ 挂会战支援
- 👥 更好的权限管理
- ⚔️ 竞技场杀手（详见下面说明）
- ✨ 更加优雅的代码结构
- 🚀 更快的性能以及高并发支持
- 🌐 WebUI 支持（见下方部署与使用说明）
- 🌍 三服支持（B服、渠道服、台服）

## ⚠️ 严重警告

**本插件过于方便、优雅、简单，可能会危及公会战、竞技场、"管理困难"、"BOX不对"等经久不衰的重要功能。本人建议各大公会将本插件列为 `禁用` 插件。**

## 📖 项目信息

**项目地址**：[kanna_connection_redive_2](https://github.com/SonderXiaoming/kanna_connection_redive_2)

---

## 🔧 账号绑定指南

> **提示**：以下说明中的"+" 表示空格

### 绑定指令

```
加机器人好友后，使用对应命令绑定账号：

【绑定账号 账号 密码】
【渠绑定账号 login_id token】
【台绑定账号 short_udid udid_viewer_id】
```

### 相关工具

- [pcr_login_helper](https://github.com/SonderXiaoming/pcr_login_helper) - 账号提取器
- ⚠️ 台服未经测试，理论上应该也可以，欢迎 PR

---

## 📊 功能详解

 <details><summary><font size=5 >1. 自动报刀</font></summary>
【出刀监控】机器人登录账号，监视出刀情况并记录<br>
【催刀】栞栞谁没出满三刀<br>
【当前战报】本期会战出刀情况<br>
【我的战报 + 游戏名称】 栞栞个人出刀情况<br>
【今日战报 + 游戏名称】 栞栞今日个人出刀情况<br>
【昨日战报 + 游戏名称】 栞栞昨日个人出刀情况<br>
【出刀详情 + 出刀编号】 栞栞你这刀怎么出的（出刀编号可以通过查看个人战报获得）<br>
【今日出刀】今日出刀情况<br>
【昨日出刀】昨日出刀情况<br>
【启用肃正协议】数据出现异常使用即可清空所有数据（危险！！！）<br>
【修正出刀 + 出刀编号 + （完整刀|尾刀|补偿）】修正错误的刀数记录<br>
【状态】查看当前进度<br>
【boss状态】看看boss里面有几个人<br>
【预约表】栞栞谁预约了<br>
【预约 + 数字 + （周目）+ （留言） 】预约boss, 周目和留言可不写，默认当前周目<br>
【取消预约 + （数字）】取消预约<br>
【清空预约 + （数字）】（仅）管理，清空预约<br>
【查树】栞栞树上有几个人<br>
【下树】寄，掉刀了<br>
【挂树 + 数字】失误了, 寄<br>
【sl】记录sl<br>
【sl?】栞栞今天有没有用过sl<br>
【申请出刀 + 数字 + （留言） 】 申请打boss，boss死亡自动清空<br>
【取消申请】 模拟10次挂10次，老子不打了</details>

 <details><summary><font size=5 >2. BOX查询(可查会战助战)</font></summary>
【刷新box缓存】会顶号，请注意，机器人自动上号记录你的box <br>
【box查询+角色名字】（@别人可以查别人，角色名输入【所有】则都查）<br>
【公会box查询+角色名字】查询绑定公会的玩家的box，不支持输入所有（卡不死你）<br>
【刷新助战缓存】会顶号，请注意，机器人自动上号记录公会助战 <br>
【精确助战+角色名字】（角色名输入【所有】则都查）<br>
【上公会战支援 + 角色名字】换助战，可以at别人 <br>
【上地下城支援 + 角色名字】换助战，可以at别人 <br>
【上关卡支援 + 角色名字】换助战，可以at别人 <br>
【我的助战】 查看自己的助战 (需要刷新box缓存) <br>
【公会深域查询】 查询公会成员的深域进度 <br>
【导出图书馆 + 目标rank + 角色名字】 将你的box导出到兰德索尔图书馆，并且进行目标rank规划。<br>
rank为小数点，小数点后仅只支持03456。可以叠加中间空格隔开，示例，"导出图书馆 19.6 千爱瑠油腻华哥 18.0 小仓唯美美炸弹人" <br>
</details>

 <details><summary><font size=5 >3. 分刀（待修）</font></summary>
分刀 [阶段] [毛分/毛伤] (类型) (BOSS) <br>
阶段：ABCD，对应公会战的四个阶段，支持跨面，如‘CCD’，和后面boss一一对应，只填写一个默认全是这一阶段 <br>
类型：T 代表自动刀，W 代表尾刀，S代表手动刀，填写多个代表都行，留空表示我全要 <br>
BOSS：1-5，对应公会战的一至五王，可以‘123’或者‘12’,也可以‘555’,留空表示哪个boss无所谓 <br>
作业序号：列表中作业的序号 <br>
 <br>
指令示例： <br>
分刀 A 毛分 <br>
(查询一阶段的所有分刀可能，按分数排序) <br>
分刀 A 毛分 123 <br>
(查询一阶段的1,2,3王所有分刀可能，按分数排序) <br>
分刀 A 毛分 T  <br>
(查询一阶段一王的AUTO刀所有分刀可能，按分数排序) <br>
分刀 A 毛分 T 123 <br>
(查询一阶段的1,2,3王所有AUTO刀分刀可能，按分数排序) <br>
注：指令示例中的空格均不可省略。 <br>
 <br>
【添加角色黑名单】 + 角色名称 <br>
（支持多角色，例如春环环奈，无空格） <br>
【添加角色缺失】 + 角色名称 <br> 
（支持多角色，例如春环环奈，无空格） <br>
【删除角色黑名单】 + 角色名称 <br>
（支持多角色，例如春环环奈，无空格） <br>
【删除角色缺失】 + 角色名称 <br>
（支持多角色，例如春环环奈，无空格） <br>
【删除作业黑名单】 + 作业id <br>
【添加作业黑名单】 + 作业id <br>
【查看角色缺失】（查看哪些角色缺失） <br>
【查看角色黑名单】（查看哪些角色是黑名单） <br> <br>
【查看作业黑名单】（查看哪些作业是黑名单） <br>
【清空角色缺失】（清空角色缺失） <br>
【清空角色黑名单】（清空角色黑名单） <br>
【清空作业黑名单】（清空作业黑名单） <br>
数据来源于: https://www.caimogu.cc/gzlj.html
</details>
 <details><summary><font size=5 >4. 竞技场杀手</font></summary>
【竞技场监控】：监视排名变化，自动给出复仇阵容 <br>
【#竞技场监控】：同上，但是这个给群里挂，其余人无需再挂即可使用下面功能 <br>
【竞技场状态】：查看竞技场监控状态 <br>
【竞技场杀手 + (开启|关闭) + 竞技场】：开启竞技场提醒，默认全开 <br>
【竞技场杀手 + (开启|关闭) + 公主竞技场】：开启公主竞技场提醒，默认全开 <br>
【取消竞技场监控】：手动退出，对应【竞技场监控】   <br>
【#取消竞技场监控】：手动退出，对应【#竞技场监控】 <br>
【竞技场清次数+数字】：最大5，仅查询 <br>
【公主竞技场清次数】：最大5，仅查询，如果有之前打过的记录，则显示之前打过的防守 <br>
【竞技场排行榜+数字】：最大5，查询10名的排行榜，如1为1-10，2为11-20 <br>
【公主竞技场排行榜+数字】：最大5，同上 <br>
【竞技场查防守+数字】：迅速查作业 <br>
【公主竞技场查防守+数字】：如果有之前打过的记录，则显示之前打过的防守 <br>
【竞技场查ID+排名】：查询对应玩家信息 <br>
【公主竞技场查ID+排名】：查询对应玩家信息 <br>
！！！请注意，ID必须大写 <br>
</details>
 <details><summary><font size=5 >5. 成员管理</font></summary>
 【绑定本群公会】将自己绑定在这个群 <br>
【删除本群公会绑定】将自己踢出公会（管理可以at别人实现踢人效果/输入qq） <br>
【退出公会+群号】将自己踢出公会，用于自己不在公会 <br>
【账号权限变更+数字】0：默认，只允许本人。1：允许管理，2：任何人 <br>
</details>
---

## 🌐 网页端 WebUI

网页端前端代码位于 [kanna_connection_webui](https://github.com/SonderXiaoming/kanna_connection_webui)。启用本插件后，后端会在 `WebSetting.api_host` 和 `WebSetting.api_port` 指定的地址启动 FastAPI；默认地址是 `127.0.0.1:12139`。前端页面、静态文件和对外访问入口需要另行部署。

网页端包含会战面板、通知与出刀记录、公会管理、BOX 与助战、竞技场中心，以及个人账号和通知设置。页面显示和可执行的操作会根据所在公会及账号权限变化。

### 页面预览

下图由当前前端页面截取。登录页填入测试 QQ `1316864423`，密码留空；首页通过该账号真实登录后截取，公会名称和群号已替换为演示文字。会战面板使用虚构的会战数据及 BOSS 占位图，不代表真实账号的当前记录。

**登录页**

![环奈连结网页登录页](docs/images/webui-login.png)

**首页与功能入口**

![环奈连结网页首页演示](docs/images/webui-home.png)

**会战面板**

![环奈连结会战面板演示](docs/images/webui-dashboard.png)

### 部署前端

1. 确认机器人已加载本插件，并在服务器本机访问 `http://127.0.0.1:12139/docs` 检查 API 是否启动。若修改了 `WebSetting.api_host` 或 `WebSetting.api_port`，使用修改后的地址。
2. 安装 Node.js 20 和 npm，构建网页静态文件：

   ```sh
   git clone https://github.com/SonderXiaoming/kanna_connection_webui.git
   cd kanna_connection_webui
   npm ci
   npm run build
   ```

   构建结果在 `dist/`。也可以从 [前端 Releases](https://github.com/SonderXiaoming/kanna_connection_webui/releases) 下载打包文件并解压，其中包含 `dist/`。

3. 将 `dist/` 放在 Web 服务器上，参考前端仓库的 [Nginx 配置示例](https://github.com/SonderXiaoming/kanna_connection_webui/blob/main/deploy/nginx.conf.example)，把网页部署在 `/kanna_connection/`，并将 `/kanna_connection/api/` 反向代理到 `127.0.0.1:12139`。按实际情况修改静态文件目录、域名、端口和 HTTPS 配置。网页与 API 应使用同一站点；刷新 `/kanna_connection/home` 等子页面时要回退到 `index.html`。
4. 在本插件的 `setting.py` 中设置公开访问地址；例如：

   ```python
   class WebSetting(Enum):
       api_host = "127.0.0.1"
       api_port = "12139"
       cookie_secure = True
       web_public_url = "https://example.com/kanna_connection"
   ```

   `web_public_url` 必须与实际网页地址一致，机器人生成的登录链接会使用它。上例启用了 HTTPS，因此 `cookie_secure` 为 `True`；若仅在 HTTP 环境测试，则设为 `False`。修改配置后重启机器人。不要将内部 API 端口 `12139` 直接暴露到公网。

### 登录与使用

- 向机器人**私聊**发送 `网页端登录`，打开返回的单次登录链接。链接有效期为 5 分钟，使用一次后即失效；过期时重新获取即可。
- 如需密码登录，私聊发送 `设置网页密码 新密码`。网页密码需为 8 至 128 个字符。登录页填写 QQ 号和此网页密码，**不是 QQ 密码**。
- 登录后从首页进入公会战、BOX／助战、竞技场及公会管理功能。涉及刷新 BOX、挂载助战等操作时，网页会提示确认游戏账号登录；这类操作可能使游戏客户端掉线。

若页面能打开但登录或数据加载失败，先确认 `web_public_url`、`/kanna_connection/api/` 反向代理和后端 `12139` 端口是否一致。若只在刷新子页面时出现 404，检查 Nginx 的 `index.html` 回退规则。前端仓库的 [README](https://github.com/SonderXiaoming/kanna_connection_webui#readme) 有更完整的构建与发布说明。

---

## 🖼️ BOX 查询效果展示

![f83157d3b7fe674cd6043c7cb57f24c6_720](https://github.com/user-attachments/assets/f89e4270-a4d4-4ad8-85c8-47c3730cc548)

---

## 📦 安装教程

### 前置依赖

- [multicq_send](https://github.com/SonderXiaoming/multicq_send)
- [convert2img](https://github.com/SonderXiaoming/convert2img)

**⚠️ 注意**：无需加载这两个插件

### 安装步骤

1. **拉取项目**

   在 `HoshinoBot\hoshino\modules` 目录下执行：

   ```bash
   git clone https://github.com/SonderXiaoming/kanna_connection_redive_2
   ```

2. **配置模块**

   编辑 `HoshinoBot\hoshino\config\__bot__.py` 文件，在 `MODULES_ON` 中添加：

   ```python
   'kanna_connection_redive_2'
   ```

3. **重启服务**

   重启 HoshinoBot

---

## 📝 更新日志

| 版本  | 说明     |
| ----- | -------- |
| 1.0.0 | 好的开始 |

---
