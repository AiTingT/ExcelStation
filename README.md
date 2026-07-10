# 表格工作台 - 源码部署说明

## 环境要求

- Python 3.10 及以上
- Windows / macOS / Linux 均可

## 安装步骤

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动服务

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

或者直接运行入口脚本：

```bash
python main.py
```

### 3. 访问应用

打开浏览器访问：http://127.0.0.1:8000

## 功能说明

- 📊 **数据浏览**：上传 Excel 后快速浏览，支持筛选、排序、双击编辑
- 📈 **透视表**：行/列/值字段自由配置，多种聚合方式
- 🧹 **数据清洗**：去重、删除空行空列、类型转换
- 🔍 **对比分析**：两个 Excel 文件按指定列对比差异
- 🤖 **AI 对话**：自然语言查询数据，支持跨 sheet 统计

## 数据库配置

默认使用本地 SQLite，无需配置。如需使用 MySQL：

1. 安装 pymysql：`pip install pymysql`
2. 在页面右上角点击「数据库」按钮配置连接信息

## 目录结构

```
部署源码/
├── app/
│   ├── __init__.py
│   ├── main.py          # 应用入口
│   ├── config.py        # 配置文件
│   ├── models/          # 数据模型
│   ├── routers/         # 路由层
│   └── services/        # 服务层
├── static/
│   └── index.html       # 前端页面
├── _shared/             # 共享资源（字体、JS库等）
├── main.py              # 启动脚本
├── requirements.txt     # 依赖列表
└── excel_station.spec   # PyInstaller 打包配置
```
