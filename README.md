# Cytopia Deploy

菁英 AI 创孵营学员部署 Skill。它把本地静态网站或 React / Vue 等前端项目构建后发布到训练营服务器，并返回小组的稳定访问域名。

## 安装

```powershell
npx skills add https://gitee.com/Infinity-light/cytopia-deploy.git -a codex -g -y
```

安装后，在项目目录对 AI 说：

```text
请使用 $cytopia-deploy 部署当前项目。
```

AI 会先在本地构建和预检，然后打开训练营浏览器授权页。学员只用自己的训练营账号确认这一次发布，不需要、也不能接触服务器、SSH、DNS、对象存储或模型服务密钥。

## 安全边界

- 只上传静态构建产物，不执行学员的 Dockerfile、Shell、Python 或 Node 服务。
- 本地与服务端各做一次密钥、路径、文件类型、数量和体积检查。
- `.env`、私钥、Token、隐藏文件和符号链接一律拒绝上传。
- 需要 AI 的网页只调用同源 `/__camp/ai/chat`；上游模型密钥留在服务器。
- 每次发布生成独立版本；重新部署保留域名，历史成功版本可在训练营部署中心回滚。

完整工作流见 [SKILL.md](SKILL.md)，协议说明见 [references/api.md](references/api.md)。
